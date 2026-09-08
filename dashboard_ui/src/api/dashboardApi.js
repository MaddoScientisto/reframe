import { jsonRequest, request, getPhotoFilename } from './http'

export function getPhotos(page, limit, carouselOnly = false) {
  const params = new URLSearchParams({ page, limit, carousel_only: carouselOnly })
  return request(`/api/photos?${params.toString()}`)
}

export function getSettings() {
  return request('/api/settings')
}

export function saveSettings(settings) {
  return jsonRequest('/api/settings', 'POST', settings)
}

export function getExtensionActions() {
  return request('/api/extensions/actions')
}

export function capturePhoto() {
  return request('/api/capture', { method: 'POST' })
}

export function stopPreview(clientId) {
  const params = new URLSearchParams({ client_id: clientId })
  return request(`/api/preview/stop?${params.toString()}`, { method: 'POST' })
}

export function getPreviewTelemetry(clientId) {
  const params = new URLSearchParams({ client_id: clientId })
  return request(`/api/preview/telemetry?${params.toString()}`)
}

export function setPreviewFocus(body) {
  return jsonRequest('/api/preview/focus', 'POST', body)
}

export function getCarouselStatus() {
  return request('/api/carousel/status')
}

export function startCarousel() {
  return request('/api/carousel/start', { method: 'POST' })
}

export function stopCarousel() {
  return request('/api/carousel/stop', { method: 'POST' })
}

export function setCarouselPhoto(photoId, included) {
  return jsonRequest(`/api/carousel/photos/${encodeURIComponent(photoId)}`, 'POST', { included })
}

export function displayPhoto(photoId) {
  return request(`/api/display/${encodeURIComponent(photoId)}`, { method: 'POST' })
}

export function displayPreview(body) {
  return jsonRequest('/api/preview/display', 'POST', body)
}

export function generatePreview(photoId, body, signal) {
  return request(`/api/photos/${encodeURIComponent(photoId)}/preview`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  })
}

export function runExtensionAction(extensionId, photoId) {
  return request(`/api/extensions/${encodeURIComponent(extensionId)}/photos/${encodeURIComponent(photoId)}`, { method: 'POST' })
}

export function displayControl(action) {
  return request(`/api/display/${action}`, { method: 'POST' })
}

export function showDashboardQr() {
  return request('/api/dashboard/qr', { method: 'POST' })
}

export function getBattery() {
  return request('/api/battery')
}

export function resetTimeout() {
  return request('/api/timeout/reset', { method: 'POST' })
}

export function checkForUpdates() {
  return request('/api/update/status')
}

export function installUpdate() {
  return request('/api/update/install', { method: 'POST' })
}

export function startDownload() {
  return request('/api/photos/download-all/start', { method: 'POST' })
}

export function getDownloadProgress() {
  return request('/api/photos/download-all/progress')
}

export function abortDownload(options = {}) {
  return request('/api/photos/download-all/abort', { method: 'POST', ...options })
}

export function startDelete() {
  return request('/api/photos/delete-all/start', { method: 'POST' })
}

export function getDeleteProgress() {
  return request('/api/photos/delete-all/progress')
}

export function abortDelete() {
  return request('/api/photos/delete-all/abort', { method: 'POST' })
}

export function ditheredDownloadUrl(photo) {
  return photo?.dithered_path
    ? `/api/download/dithered/${encodeURIComponent(getPhotoFilename(photo.dithered_path))}`
    : ''
}
