import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useInfiniteQuery, useQueryClient } from '@tanstack/vue-query'
import { getPhotos } from '../api/dashboardApi'
import { galleryQueryKey, normalizePage } from './photoGalleryIdentity'

export const PAGE_SIZE_OPTIONS = [12, 24, 48, 100]
const PAGE_SIZE_STORAGE_KEY = 'reframe.gallery.pageSize'

function readNumber(value) {
  const parsed = Number.parseInt(value, 10)
  return Number.isInteger(parsed) ? parsed : null
}

function readPageSize() {
  const params = new URLSearchParams(window.location.search)
  const urlValue = readNumber(params.get('limit'))
  if (PAGE_SIZE_OPTIONS.includes(urlValue)) return urlValue

  try {
    const storedValue = readNumber(window.localStorage.getItem(PAGE_SIZE_STORAGE_KEY))
    if (PAGE_SIZE_OPTIONS.includes(storedValue)) return storedValue
  } catch {
    // Local storage may be disabled; the URL and default still work.
  }
  return PAGE_SIZE_OPTIONS[0]
}

export { galleryQueryKey, normalizePage }

export function usePhotoGallery(carouselOnly) {
  const queryClient = useQueryClient()
  const pageSize = ref(readPageSize())
  const initialPage = Math.max(1, readNumber(new URLSearchParams(window.location.search).get('page')) || 1)
  const currentPage = ref(initialPage)
  const queryStartPage = ref(initialPage)
  const sentinel = ref(null)
  const pageJumpBusy = ref(false)
  let observer = null
  let nextPageRequest = null

  const viewKey = computed(() => `view:${carouselOnly.value ? 'carousel' : 'all'}:limit:${pageSize.value}`)
  const query = useInfiniteQuery({
    queryKey: computed(() => galleryQueryKey(pageSize.value, carouselOnly.value, queryStartPage.value)),
    queryFn: ({ pageParam }) => {
      const requestedPage = pageParam === 1 ? queryStartPage.value : pageParam
      return getPhotos(requestedPage, pageSize.value, carouselOnly.value)
    },
    initialPageParam: 1,
    getNextPageParam: (lastPage) => {
      const pagination = lastPage?.pagination || {}
      if (!pagination.has_next) return undefined
      return (readNumber(pagination.page) || 1) + 1
    },
    staleTime: 30_000,
    gcTime: 10 * 60_000,
    retry: (failureCount, error) => failureCount < 1 && !(error?.status >= 400 && error?.status < 500),
    refetchOnWindowFocus: false,
  })

  const normalizedGallery = computed(() => {
    try {
      const rawPages = query.data.value?.pages || []
      const normalized = rawPages.map((response, index) => normalizePage(
        response,
        query.data.value.pageParams[index] || index + 1,
        viewKey.value,
      ))
      const seen = new Set()
      for (const page of normalized) {
        for (const photo of page.photos) {
          if (seen.has(photo.entityKey)) {
            throw new Error(`Duplicate photo id returned: ${photo.id}`)
          }
          seen.add(photo.entityKey)
        }
      }
      return { pages: normalized, error: null }
    } catch (error) {
      return { pages: [], error }
    }
  })

  const pages = computed(() => normalizedGallery.value.pages)

  const photos = computed(() => {
    const seen = new Set()
    return pages.value.flatMap((page) => page.photos.filter((photo) => {
      if (seen.has(photo.entityKey)) return false
      seen.add(photo.entityKey)
      return true
    }))
  })

  const selectedPage = computed(() => (
    pages.value.find((page) => page.page === currentPage.value)
    || pages.value[0]
    || null
  ))
  const pagination = computed(() => selectedPage.value?.pagination || {
    page: currentPage.value,
    limit: pageSize.value,
    total_photos: 0,
    total_pages: 1,
    has_prev: false,
    has_next: false,
  })
  const error = computed(() => normalizedGallery.value.error?.message || query.error.value?.message || '')

  function updatePageUrl() {
    const url = new URL(window.location.href)
    if (currentPage.value === 1) url.searchParams.delete('page')
    else url.searchParams.set('page', currentPage.value)
    if (carouselOnly.value) url.searchParams.set('view', 'carousel')
    else url.searchParams.delete('view')
    if (pageSize.value === PAGE_SIZE_OPTIONS[0]) url.searchParams.delete('limit')
    else url.searchParams.set('limit', pageSize.value)
    window.history.replaceState({}, '', url)
  }

  async function fetchNextPageOnce() {
    if (!query.hasNextPage.value || query.isFetchingNextPage.value) return
    if (nextPageRequest) return nextPageRequest
    nextPageRequest = query.fetchNextPage().finally(() => {
      nextPageRequest = null
    })
    return nextPageRequest
  }

  function scrollToCurrentPage() {
    document.querySelector(`[data-page-marker="${currentPage.value}"]`)?.scrollIntoView({ block: 'start' })
  }

  async function changePage(targetPage) {
    const target = Math.min(Math.max(Number(targetPage) || 1, 1), pagination.value.total_pages || 1)
    const alreadyAnchored = queryStartPage.value === target
      && currentPage.value === target
      && pages.value.some((page) => page.page === target)
    if (alreadyAnchored) {
      await nextTick()
      scrollToCurrentPage()
      return
    }

    pageJumpBusy.value = true
    try {
      queryClient.removeQueries({
        queryKey: galleryQueryKey(pageSize.value, carouselOnly.value, target),
        exact: true,
      })
      currentPage.value = target
      queryStartPage.value = target
      updatePageUrl()
      await nextTick()
      await query.refetch()
      await nextTick()
      scrollToCurrentPage()
    } finally {
      pageJumpBusy.value = false
    }
  }

  function setPageSize(value) {
    const nextSize = Number(value)
    if (!PAGE_SIZE_OPTIONS.includes(nextSize) || nextSize === pageSize.value) return
    pageSize.value = nextSize
    currentPage.value = 1
    try {
      window.localStorage.setItem(PAGE_SIZE_STORAGE_KEY, String(nextSize))
    } catch {
      // The query still isolates the page-size variant when storage is unavailable.
    }
    updatePageUrl()
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  function setCarouselView(nextValue) {
    if (carouselOnly.value === nextValue) return
    carouselOnly.value = nextValue
    currentPage.value = 1
    updatePageUrl()
  }

  async function refresh() {
    return query.refetch()
  }

  function setSentinel(element) {
    sentinel.value = element
  }

  async function invalidate() {
    return queryClient.invalidateQueries({ queryKey: ['photos'] })
  }

  function updatePhotoCache(cached, photoId, trimToCurrentPage = false) {
    if (!cached?.pages) return cached
    let removedCount = 0
    const filteredPages = cached.pages.map((page) => {
      const photos = Array.isArray(page.photos) ? page.photos : []
      const filteredPhotos = photos.filter((photo) => {
        const keep = photo?.id !== photoId
        if (!keep) removedCount += 1
        return keep
      })
      return { ...page, photos: filteredPhotos }
    })
    if (!removedCount) return cached
    const firstPagination = filteredPages[0]?.pagination || {}
    const totalPhotos = Math.max(0, Number(firstPagination.total_photos) - removedCount)
    const limit = Math.max(1, Number(firstPagination.limit) || pageSize.value)
    const totalPages = Math.max(1, Math.ceil(totalPhotos / limit))
    const pagesWithPagination = filteredPages.map((page) => ({
      ...page,
      pagination: {
        ...page.pagination,
        total_photos: totalPhotos,
        total_pages: totalPages,
        has_prev: page.page > 1,
        has_next: page.page < totalPages,
      },
    }))
    if (!trimToCurrentPage) {
      return { ...cached, pages: pagesWithPagination }
    }
    const retainedIndex = Math.max(
      0,
      pagesWithPagination.findIndex((page) => page.page === currentPage.value),
    )
    return {
      ...cached,
      pages: [pagesWithPagination[retainedIndex]],
      pageParams: [cached.pageParams?.[retainedIndex] ?? currentPage.value],
    }
  }

  async function removePhoto(photoId) {
    const targetId = String(photoId || '').trim()
    if (!targetId) return
    let removedFromCache = false
    queryClient.setQueriesData({ queryKey: ['photos'] }, (cached) => {
      const updated = updatePhotoCache(cached, targetId)
      if (updated !== cached) removedFromCache = true
      return updated
    })
    if (!removedFromCache) return
    if (currentPage.value > pagination.value.total_pages) currentPage.value = pagination.value.total_pages
    const activeQueryKey = galleryQueryKey(pageSize.value, carouselOnly.value, queryStartPage.value)
    queryClient.setQueryData(activeQueryKey, (cached) => updatePhotoCache(cached, targetId, true))
    queryClient.invalidateQueries({ queryKey: ['photos'], refetchType: 'none' })
    void queryClient.refetchQueries({ queryKey: activeQueryKey, exact: true, type: 'active' })
  }

  function patchPhoto(updatedPhoto) {
    if (!updatedPhoto?.id) return
    queryClient.setQueriesData({ queryKey: ['photos'] }, (cached) => {
      if (!cached?.pages) return cached
      return {
        ...cached,
        pages: cached.pages.map((page) => ({
          ...page,
          photos: (page.photos || []).map((photo) => (
            photo.id === updatedPhoto.id ? { ...photo, ...updatedPhoto } : photo
          )),
        })),
      }
    })
  }

  function reconnectObserver() {
    observer?.disconnect()
    if (!sentinel.value || typeof IntersectionObserver === 'undefined') return
    if (!query.hasNextPage.value) return
    observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void fetchNextPageOnce()
    }, { rootMargin: '200px 0px' })
    observer.observe(sentinel.value)
  }

  watch(sentinel, reconnectObserver)
  watch(pages, (nextPages) => {
    const resolvedPage = nextPages[0]?.page
    if (resolvedPage && !nextPages.some((page) => page.page === currentPage.value)) {
      currentPage.value = resolvedPage
    }
    reconnectObserver()
  })
  watch([pageSize, carouselOnly], () => {
    currentPage.value = 1
    queryStartPage.value = 1
    updatePageUrl()
    reconnectObserver()
  })
  watch(currentPage, () => {
    updatePageUrl()
    reconnectObserver()
  })
  watch(() => query.hasNextPage.value, reconnectObserver)
  onMounted(() => {
    reconnectObserver()
  })
  onUnmounted(() => observer?.disconnect())

  return {
    photos,
    pages,
    pagination,
    currentPage,
    pageSize,
    pageSizeOptions: PAGE_SIZE_OPTIONS,
    setSentinel,
    isPending: query.isPending,
    isFetching: query.isFetching,
    isFetchingNextPage: query.isFetchingNextPage,
    pageJumpBusy,
    error,
    hasNextPage: query.hasNextPage,
    refresh,
    invalidate,
    removePhoto,
    patchPhoto,
    changePage,
    setPageSize,
    setCarouselView,
    fetchNextPage: fetchNextPageOnce,
    retry: query.refetch,
  }
}
