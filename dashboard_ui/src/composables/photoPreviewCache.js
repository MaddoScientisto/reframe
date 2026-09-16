const generatedPreviewCache = new Map()
const loadedImageSources = new Set()

export function generatedPreviewKey(photoId, mode, palette) {
  return `${photoId}:${mode}:${palette}`
}

export function getGeneratedPreview(key) {
  return generatedPreviewCache.get(key) || null
}

export function setGeneratedPreview(key, preview) {
  generatedPreviewCache.set(key, preview)
}

export function isImagePreviewLoaded(source) {
  return Boolean(source) && loadedImageSources.has(source)
}

export function markImagePreviewLoaded(source) {
  if (source) loadedImageSources.add(source)
}

export function forgetImagePreview(source) {
  if (source) loadedImageSources.delete(source)
}

export function invalidateImagePreview(source) {
  if (!source) return
  for (const loadedSource of loadedImageSources) {
    if (loadedSource === source || loadedSource.startsWith(`${source}?`)) {
      loadedImageSources.delete(loadedSource)
    }
  }
}