<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import AppHeader from './components/AppHeader.vue'
import LivePreviewDialog from './components/LivePreviewDialog.vue'
import PhotoGallery from './components/PhotoGallery.vue'
import PhotoPreviewDialog from './components/PhotoPreviewDialog.vue'
import SettingsModal from './components/SettingsModal.vue'
import { useLongRunningJob } from './composables/useLongRunningJob'
import {
  abortDelete,
  abortDownload,
  capturePhoto,
  checkForUpdates,
  displayControl,
  displayPhoto,
  getBattery,
  getCarouselStatus,
  getDeleteProgress,
  getDownloadProgress,
  getExtensionActions,
  getPhotos,
  getSettings,
  installUpdate,
  resetTimeout,
  runExtensionAction,
  saveSettings,
  showDashboardQr,
  startCarousel,
  startDelete,
  startDownload,
  stopCarousel,
} from './api/dashboardApi'

const photosPerPage = 12
const initialPage = Number.parseInt(new URLSearchParams(window.location.search).get('page'), 10)
const initialView = new URLSearchParams(window.location.search).get('view') === 'carousel'
const currentPage = ref(Number.isInteger(initialPage) && initialPage > 0 ? initialPage : 1)
const carouselOnly = ref(initialView)
const photos = ref([])
const pagination = ref({ total_pages: 1, total_photos: 0, has_prev: false, has_next: false, limit: photosPerPage })
const extensionActions = ref([])
const galleryLoading = ref(false)
const galleryError = ref('')
const latestPhotoLoadRequest = ref(0)
const batteryLevel = ref(null)
const captureBusy = ref(false)
const carouselActive = ref(false)
const carouselBusy = ref(false)
const busyAction = ref('')
const selectedPhoto = ref(null)
const livePreviewOpen = ref(false)
const settingsOpen = ref(false)
const settings = ref(null)
const settingsLoading = ref(false)
const settingsSaving = ref(false)
const displayState = ref('')
const updateState = ref({ busy: false, can_update: false, message: '' })
const notification = ref(null)
let notificationTimer = null
let batteryTimer = null
let refreshTimer = null

const previewAspectRatio = computed(() => {
  const resolution = settings.value?.camera?.resolution
  const width = Number(resolution?.width)
  const height = Number(resolution?.height)
  return width > 0 && height > 0 ? `${width} / ${height}` : '3 / 2'
})

const downloadJob = useLongRunningJob({
  start: startDownload,
  getProgress: getDownloadProgress,
  abort: abortDownload,
  label: 'download',
  maxAttempts: 600,
})
const deleteJob = useLongRunningJob({
  start: startDelete,
  getProgress: getDeleteProgress,
  abort: abortDelete,
  label: 'deletion',
  maxAttempts: 300,
})

function notify(message, type = 'info') {
  notification.value = { message, type }
  clearTimeout(notificationTimer)
  notificationTimer = setTimeout(() => {
    notification.value = null
  }, 4500)
}

function updatePageUrl() {
  const url = new URL(window.location.href)
  if (currentPage.value === 1) url.searchParams.delete('page')
  else url.searchParams.set('page', currentPage.value)
  if (carouselOnly.value) url.searchParams.set('view', 'carousel')
  else url.searchParams.delete('view')
  window.history.replaceState({}, '', url)
}

async function notifyUserActivity() {
  try {
    await resetTimeout()
  } catch {
    // Activity reporting is best effort and should not block dashboard actions.
  }
}

async function loadExtensionActions() {
  try {
    const result = await getExtensionActions()
    extensionActions.value = result.actions || []
  } catch {
    extensionActions.value = []
  }
}

async function loadPhotos(page = currentPage.value) {
  const requestedPage = Math.max(1, Number.parseInt(page, 10) || 1)
  const requestId = latestPhotoLoadRequest.value + 1
  latestPhotoLoadRequest.value = requestId
  galleryLoading.value = true
  galleryError.value = ''
  await loadExtensionActions()
  try {
    const result = await getPhotos(requestedPage, photosPerPage, carouselOnly.value)
    if (requestId !== latestPhotoLoadRequest.value) return
    const totalPages = Math.max(1, result.pagination?.total_pages || 1)
    if (requestedPage > totalPages) {
      await loadPhotos(totalPages)
      return
    }
    photos.value = result.photos || []
    pagination.value = result.pagination || pagination.value
    currentPage.value = requestedPage
    updatePageUrl()
  } catch (error) {
    if (requestId === latestPhotoLoadRequest.value) galleryError.value = error.message || 'Could not load photos'
  } finally {
    if (requestId === latestPhotoLoadRequest.value) galleryLoading.value = false
  }
}

function configureRefreshTimer() {
  clearInterval(refreshTimer)
  refreshTimer = null
  const seconds = Number(settings.value?.system?.auto_refresh_interval)
  if (seconds > 0) refreshTimer = setInterval(() => loadPhotos(currentPage.value), seconds * 1000)
}

async function loadInitialSettings() {
  try {
    settings.value = await getSettings()
    configureRefreshTimer()
  } catch {
    // Settings are loaded again when the modal opens.
  }
}

async function loadCarouselStatus() {
  try {
    const result = await getCarouselStatus()
    carouselActive.value = Boolean(result.active)
  } catch {
    carouselActive.value = false
  }
}

async function openSettings() {
  settingsLoading.value = true
  await notifyUserActivity()
  try {
    settings.value = await getSettings()
    settingsOpen.value = true
  } catch (error) {
    notify(error.message || 'Could not load settings', 'error')
  } finally {
    settingsLoading.value = false
  }
}

async function handleSaveSettings(payload) {
  settingsSaving.value = true
  try {
    await saveSettings(payload)
    settings.value = await getSettings()
    settingsOpen.value = false
    configureRefreshTimer()
    await loadExtensionActions()
    await loadPhotos(currentPage.value)
    notify('Settings saved successfully', 'success')
  } catch (error) {
    notify(error.message || 'Could not save settings', 'error')
  } finally {
    settingsSaving.value = false
  }
}

async function handleCapture() {
  captureBusy.value = true
  carouselActive.value = false
  await notifyUserActivity()
  try {
    const result = await capturePhoto()
    notify(result.message || 'Capture complete', 'success')
    await loadPhotos(currentPage.value)
  } catch (error) {
    notify(error.message || 'Could not capture photo', 'error')
  } finally {
    captureBusy.value = false
  }
}

async function openLivePreview() {
  await notifyUserActivity()
  livePreviewOpen.value = true
}

function closeLivePreview() {
  livePreviewOpen.value = false
}

async function handleCarouselToggle() {
  carouselBusy.value = true
  await notifyUserActivity()
  try {
    const result = carouselActive.value ? await stopCarousel() : await startCarousel()
    carouselActive.value = Boolean(result.active)
    notify(result.message || (carouselActive.value ? 'Carousel started' : 'Carousel stopped'), 'success')
  } catch (error) {
    notify(error.message || 'Could not update carousel mode', 'error')
  } finally {
    carouselBusy.value = false
  }
}

async function handleDisplay(photo) {
  busyAction.value = `display:${photo.id}`
  await notifyUserActivity()
  try {
    const result = await displayPhoto(photo.id)
    notify(result.message || 'Photo sent to screen', 'success')
  } catch (error) {
    notify(error.message || 'Could not display photo', 'error')
  } finally {
    busyAction.value = ''
  }
}

async function handleExtension(action, photo) {
  const key = `${action.id}:${photo.id}`
  busyAction.value = key
  await notifyUserActivity()
  try {
    const result = await runExtensionAction(action.id, photo.id)
    notify(result.message || 'Action complete', 'success')
  } catch (error) {
    notify(error.message || 'Extension action failed', 'error')
  } finally {
    busyAction.value = ''
  }
}

function selectPhoto(photo) {
  selectedPhoto.value = photo
  notifyUserActivity()
}

function closePreview() {
  selectedPhoto.value = null
}

function changePage(page) {
  const target = Math.min(Math.max(Number(page) || 1, 1), pagination.value.total_pages || 1)
  if (target === currentPage.value) return
  notifyUserActivity()
  loadPhotos(target)
}

function changeView(nextView) {
  const nextCarouselOnly = nextView === 'carousel'
  if (nextCarouselOnly === carouselOnly.value) return
  carouselOnly.value = nextCarouselOnly
  currentPage.value = 1
  notifyUserActivity()
  loadPhotos(1)
}

async function handleCarouselUpdated({ id, included }) {
  photos.value = photos.value.map((photo) => (
    photo.id === id ? { ...photo, carousel_enabled: included } : photo
  ))
  if (selectedPhoto.value?.id === id) {
    selectedPhoto.value = { ...selectedPhoto.value, carousel_enabled: included }
  }
  await loadCarouselStatus()
  await loadPhotos(currentPage.value)
}

async function handleDisplayControl(action) {
  if (action === 'force-stop' && !window.confirm('Force-stop the panel and power it off?')) return
  displayState.value = `${action} in progress...`
  await notifyUserActivity()
  try {
    const result = await displayControl(action)
    displayState.value = result.message || `${action} complete`
    notify(displayState.value, 'success')
  } catch (error) {
    displayState.value = error.message || `${action} failed`
    notify(displayState.value, 'error')
  }
}

async function handleShowQr() {
  await notifyUserActivity()
  try {
    const result = await showDashboardQr()
    notify(result.message || 'Dashboard QR displayed', 'success')
  } catch (error) {
    notify(error.message || 'Could not show dashboard QR', 'error')
  }
}

async function handleCheckUpdates() {
  updateState.value = { ...updateState.value, busy: true, message: 'Checking for updates...' }
  try {
    const result = await checkForUpdates()
    updateState.value = { ...result, busy: false }
  } catch (error) {
    updateState.value = { ...updateState.value, busy: false, message: error.message || 'Could not check for updates.' }
  }
}

async function handleInstallUpdate() {
  if (!window.confirm('Install the available update? The camera should be rebooted after the update finishes.')) return
  updateState.value = { ...updateState.value, busy: true, message: 'Installing update...' }
  try {
    const result = await installUpdate()
    updateState.value = { ...result, busy: false }
  } catch (error) {
    updateState.value = { ...updateState.value, busy: false, message: error.message || 'Could not install update.' }
  }
}

function triggerZipDownload() {
  const link = document.createElement('a')
  link.href = '/api/photos/download-all/result'
  link.download = `reframe-photos-${new Date().toISOString().slice(0, 10)}.zip`
  link.click()
  notify('ZIP download sent to browser', 'success')
  setTimeout(() => downloadJob.reset(), 2500)
}

async function handleDownloadAll() {
  await notifyUserActivity()
  if (downloadJob.state.value.status === 'completed') {
    triggerZipDownload()
    return
  }
  await downloadJob.startJob()
}

async function handleAbortDownload() {
  await downloadJob.abortJob()
}

async function handleDeleteAll() {
  if (!window.confirm('This will permanently delete ALL photos. Are you absolutely sure?')) return
  if (!window.confirm('Final confirmation: Delete ALL photos?')) return
  await notifyUserActivity()
  await deleteJob.startJob()
}

async function handleAbortDelete() {
  await deleteJob.abortJob()
}

watch(() => downloadJob.state.value.status, (status) => {
  if (status === 'completed') triggerZipDownload()
})

watch(() => deleteJob.state.value.status, async (status) => {
  if (status === 'completed') {
    notify(deleteJob.state.value.message || 'All photos deleted', 'success')
    await loadPhotos(1)
    setTimeout(() => deleteJob.reset(), 2500)
  }
})

onMounted(async () => {
  await Promise.all([loadPhotos(currentPage.value), loadInitialSettings(), loadCarouselStatus()])
  await updateBattery()
  batteryTimer = setInterval(updateBattery, 30000)
})

async function updateBattery() {
  try {
    const result = await getBattery()
    batteryLevel.value = result.battery_level ?? null
  } catch {
    batteryLevel.value = null
  }
}

onUnmounted(() => {
  clearInterval(batteryTimer)
  clearInterval(refreshTimer)
  clearTimeout(notificationTimer)
})
</script>

<template>
  <main class="app-shell">
    <AppHeader
      :battery-level="batteryLevel"
      :photo-count="pagination.total_photos"
      :capture-busy="captureBusy"
      :carousel-active="carouselActive"
      :carousel-busy="carouselBusy"
      @refresh="loadPhotos(currentPage)"
      @capture="handleCapture"
      @live-preview="openLivePreview"
      @toggle-carousel="handleCarouselToggle"
      @settings="openSettings"
    />
    <nav class="gallery-tabs" aria-label="Photo views">
      <button class="tab-button" :class="{ active: !carouselOnly }" type="button" @click="changeView('all')">all photos</button>
      <button class="tab-button" :class="{ active: carouselOnly }" type="button" @click="changeView('carousel')">carousel</button>
    </nav>
    <PhotoGallery
      :photos="photos"
      :pagination="pagination"
      :current-page="currentPage"
      :loading="galleryLoading"
      :error="galleryError"
      :extension-actions="extensionActions"
      :busy-action="busyAction"
      @select="selectPhoto"
      @display="handleDisplay"
      @extension="handleExtension"
      @change-page="changePage"
    />
    <PhotoPreviewDialog
      :photo="selectedPhoto"
      :extension-actions="extensionActions"
      @close="closePreview"
      @notify="notify($event, 'success')"
      @carousel-updated="handleCarouselUpdated"
    />
    <LivePreviewDialog
      :open="livePreviewOpen"
      :capture-busy="captureBusy"
      :aspect-ratio="previewAspectRatio"
      @close="closeLivePreview"
      @capture="handleCapture"
    />
    <SettingsModal
      :open="settingsOpen"
      :settings="settings"
      :saving="settingsSaving || settingsLoading"
      :update-state="updateState"
      :display-state="displayState"
      :download-state="downloadJob.state"
      :delete-state="deleteJob.state"
      @close="settingsOpen = false"
      @save="handleSaveSettings"
      @check-updates="handleCheckUpdates"
      @install-update="handleInstallUpdate"
      @show-qr="handleShowQr"
      @display-control="handleDisplayControl"
      @download-all="handleDownloadAll"
      @abort-download="handleAbortDownload"
      @delete-all="handleDeleteAll"
      @abort-delete="handleAbortDelete"
    />
    <aside v-if="notification" class="toast" :class="`toast-${notification.type}`" role="status">
      {{ notification.message }}
    </aside>
  </main>
</template>
