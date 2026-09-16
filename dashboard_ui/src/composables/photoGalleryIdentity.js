function readNumber(value) {
  const parsed = Number.parseInt(value, 10)
  return Number.isInteger(parsed) ? parsed : null
}

export function galleryQueryKey(pageSize, carouselOnly, startPage = 1) {
  const key = { pageSize, carouselOnly }
  if (startPage !== 1) key.startPage = startPage
  return ['photos', key]
}

export function normalizePage(response, pageParam, viewKey) {
  const serverPagination = response?.pagination || {}
  const serverPage = readNumber(serverPagination.page) || pageParam
  const photos = Array.isArray(response?.photos) ? response.photos : []
  const serviceError = typeof response?.service_error === 'string'
    ? response.service_error.trim()
    : ''
  return {
    page: serverPage,
    pageKey: `${viewKey}:page:${serverPage}`,
    serviceError,
    pagination: {
      page: serverPage,
      limit: readNumber(serverPagination.limit) || readNumber(serverPagination.photos_per_page) || 12,
      total_photos: Number(serverPagination.total_photos) || 0,
      total_pages: Math.max(1, Number(serverPagination.total_pages) || 1),
      has_prev: Boolean(serverPagination.has_prev),
      has_next: Boolean(serverPagination.has_next),
    },
    photos: photos.map((photo, slot) => {
      if (!photo || typeof photo.id !== 'string' || !photo.id.trim()) {
        throw new Error(`Photo page ${serverPage} contains a record without an id`)
      }
      const id = photo.id.trim()
      return {
        ...photo,
        id,
        page: serverPage,
        slot,
        pageKey: `${viewKey}:page:${serverPage}`,
        slotKey: `${viewKey}:page:${serverPage}:slot:${slot}`,
        entityKey: `photo:${id}`,
      }
    }),
  }
}
