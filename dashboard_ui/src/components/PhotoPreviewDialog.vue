<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import {
  deletePhoto,
  displayPhoto,
  displayPreview,
  ditheredDownloadUrl,
  generatePreview,
  runExtensionAction,
  savePhotoPreview,
  setCarouselPhoto,
} from '../api/dashboardApi'
import {
  generatedPreviewKey,
  getGeneratedPreview,
  markImagePreviewLoaded,
  setGeneratedPreview,
} from '../composables/photoPreviewCache'
import PhotoMetadataTree from './PhotoMetadataTree.vue'

const props = defineProps({
  photo: { type: Object, default: null },
  extensionActions: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'notify', 'carousel-updated', 'photo-updated', 'deleted'])

const dialogRef = ref(null)
const deleteDialogRef = ref(null)
const tab = ref('dithered')
const mode = ref('saved')
const palette = ref('blue_yellow')
const initialMode = ref('saved')
const initialPalette = ref('blue_yellow')
const initialRotation = ref(0)
const generatedPng = ref('')
const downloadPng = ref('')
const loading = ref(false)
const sending = ref(false)
const saving = ref(false)
const deleting = ref(false)
const error = ref('')
const rotation = ref(0)
const imageDimensions = ref(null)
const ditherControlsOpen = ref(false)
const extensionBusy = ref('')
const carouselIncluded = ref(false)
const carouselSaving = ref(false)
const metadataOpen = ref(true)
const revision = ref(0)
const savedRevision = ref('')
const stageRef = ref(null)
const viewportSize = ref({ width: 0, height: 0 })
const zoom = ref(1)
const panX = ref(0)
const panY = ref(0)
const dragging = ref(false)
const dragOrigin = ref(null)
const maxZoom = 4
let requestController = null
let stageResizeObserver = null

const isGenerated = computed(() => mode.value !== 'saved')
const hasChanges = computed(() => (
  rotation.value !== initialRotation.value
  || mode.value !== initialMode.value
  || palette.value !== initialPalette.value
))
const effectiveDitherMode = computed(() => (
  mode.value === 'saved' ? props.photo?.dithering_method || 'floyd_steinberg' : mode.value
))
const modeLabel = computed(() => mode.value === 'saved' ? 'saved version' : mode.value)
const previewSource = computed(() => {
  if (!props.photo) return ''
  if (!isGenerated.value) {
    const path = props.photo.dithered_path || ''
    if (!path) return ''
    const version = props.photo.dithered_updated_at || savedRevision.value
    if (!version) return path
    return `${path}${path.includes('?') ? '&' : '?'}v=${version}`
  }
  return generatedPng.value ? `data:image/png;base64,${generatedPng.value}` : ''
})
const imageSource = computed(() => {
  if (!props.photo) return ''
  return tab.value === 'original' ? props.photo.original_path : previewSource.value
})
const ditheredDownload = computed(() => {
  if (!props.photo || !previewSource.value) return ''
  if (!isGenerated.value) return ditheredDownloadUrl(props.photo)
  return `data:image/png;base64,${downloadPng.value || generatedPng.value}`
})
const ditheredFilename = computed(() => {
  if (!props.photo) return 'dithered.png'
  return isGenerated.value
    ? `${props.photo.id}_${mode.value}.png`
    : `${props.photo.id}_dithered.png`
})

const metadataRows = computed(() => {
  const photo = props.photo
  if (!photo) return []
  const rows = [
    ['name', metadataValue(photo.filename || photo.id)],
    ['size', metadataValue(photo.file_size, formatFileSize)],
    [photo.id_kind === 'content_hash' ? 'hash' : 'id', metadataValue(photo.id)],
    ['capture date', metadataValue(photo.capture_time, formatDate)],
    ['file date', metadataValue(photo.created_at, formatDate)],
    ['dimensions', metadataValue(imageDimensions.value ? `${imageDimensions.value.width} x ${imageDimensions.value.height}` : 'not loaded')],
    ['dithered', metadataValue(photo.has_dithered, (value) => value ? 'yes' : 'no')],
    ['dither mode', metadataValue(photo.dithering_method || 'not set')],
    ['palette', metadataValue(photo.gb_color_palette || 'not set')],
    ['EXIF metadata', metadataValue(photo.exif_metadata)],
    ['sensor metadata', metadataValue(photo.sensor_metadata)],
    ['rotation', metadataValue(photo.rotation, (value) => `${Number(value) || 0} quarter turns`)],
  ]
  if (photo.legacy_id) rows.splice(3, 0, ['legacy id', metadataValue(photo.legacy_id)])
  if (photo.processing_metadata) rows.push(['processing metadata', metadataValue(photo.processing_metadata)])
  return rows.filter(([, value]) => value && value !== 'not set')
})

function parseJsonValue(value) {
  if (value && typeof value === 'object') return value
  if (typeof value !== 'string') return null
  try {
    const parsed = JSON.parse(value)
    return parsed && typeof parsed === 'object' ? parsed : null
  } catch {
    return null
  }
}

function metadataValue(value, formatter = (item) => item) {
  return parseJsonValue(value) || formatter(value)
}

const sourceDimensions = computed(() => {
  if (!imageDimensions.value) return null
  return {
    width: evenDimension(imageDimensions.value.width),
    height: evenDimension(imageDimensions.value.height),
  }
})

const renderedDimensions = computed(() => {
  if (!sourceDimensions.value) return null
  const { width, height } = sourceDimensions.value
  const rotatedWidth = rotation.value % 2 ? height : width
  const rotatedHeight = rotation.value % 2 ? width : height
  const viewportWidth = viewportSize.value.width
  const viewportHeight = viewportSize.value.height
  const fitScale = viewportWidth > 0 && viewportHeight > 0
    ? Math.min(viewportWidth / rotatedWidth, viewportHeight / rotatedHeight)
    : 1
  return {
    width: width * fitScale * zoom.value,
    height: height * fitScale * zoom.value,
  }
})

const visualDimensions = computed(() => {
  if (!renderedDimensions.value) return null
  return rotation.value % 2
    ? { width: renderedDimensions.value.height, height: renderedDimensions.value.width }
    : renderedDimensions.value
})

const stageStyle = computed(() => {
  if (!sourceDimensions.value) return {}
  const { width: sourceWidth, height: sourceHeight } = sourceDimensions.value
  const width = rotation.value % 2 ? sourceHeight : sourceWidth
  const height = rotation.value % 2 ? sourceWidth : sourceHeight
  return {
    '--preview-stage-ratio': `${width} / ${height}`,
    '--preview-stage-ratio-value': width / height,
  }
})

function evenDimension(value) {
  const dimension = Number(value)
  if (!Number.isFinite(dimension) || dimension < 2) return dimension
  return Math.ceil(dimension / 2) * 2
}

function formatFileSize(value) {
  const size = Number(value)
  if (!Number.isFinite(size) || size < 0) return 'unknown'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(2)} MB`
}

function formatDate(value) {
  if (value === null || value === undefined || value === '') return 'unknown'
  const numericValue = Number(value)
  const date = Number.isFinite(numericValue) && numericValue > 0
    ? new Date(numericValue * 1000)
    : new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium',
  }).format(date)
}

function imageAttributes() {
  if (!imageDimensions.value) return {}
  const width = imageDimensions.value.width
  const height = imageDimensions.value.height
  if (tab.value !== 'dithered') return { width, height }
  return { width: evenDimension(width), height: evenDimension(height) }
}

const imageStyle = computed(() => ({
  width: renderedDimensions.value ? `${renderedDimensions.value.width}px` : undefined,
  height: renderedDimensions.value ? `${renderedDimensions.value.height}px` : undefined,
  transform: `translate3d(calc(-50% + ${panX.value}px), calc(-50% + ${panY.value}px), 0) rotate(${rotation.value * 90}deg)`,
}))

function cancelPreviewRequest() {
  requestController?.abort()
  requestController = null
}

function resetPreviewState(photo) {
  cancelPreviewRequest()
  tab.value = photo.has_dithered ? 'dithered' : 'original'
  mode.value = photo.has_dithered ? 'saved' : photo.dithering_method || 'floyd_steinberg'
  palette.value = photo.gb_color_palette || 'blue_yellow'
  initialMode.value = mode.value
  initialPalette.value = palette.value
  initialRotation.value = Number.isInteger(photo.rotation) ? ((photo.rotation % 4) + 4) % 4 : 0
  generatedPng.value = ''
  downloadPng.value = ''
  loading.value = false
  sending.value = false
  error.value = ''
  rotation.value = initialRotation.value
  imageDimensions.value = null
  resetViewport()
  ditherControlsOpen.value = false
  extensionBusy.value = ''
  carouselIncluded.value = Boolean(photo.carousel_enabled)
  carouselSaving.value = false
  savedRevision.value = ''
}

watch(
  () => props.photo,
  async (photo) => {
    if (!photo) {
      cancelPreviewRequest()
      if (dialogRef.value?.open) dialogRef.value.close()
      closeDeleteConfirmation()
      return
    }
    resetPreviewState(photo)
    await nextTick()
    if (!dialogRef.value?.open) dialogRef.value?.showModal()
    if (!photo.has_dithered) await generatePhotoPreview()
  },
  { immediate: true },
)

watch([mode, palette], async () => {
  if (props.photo && dialogRef.value?.open && isGenerated.value) {
    await generatePhotoPreview()
  }
})

async function generatePhotoPreview() {
  if (!props.photo || !isGenerated.value) return
  cancelPreviewRequest()
  const currentRevision = ++revision.value
  const cacheKey = generatedPreviewKey(props.photo.id, mode.value, palette.value)
  const cached = getGeneratedPreview(cacheKey)
  if (cached) {
    generatedPng.value = cached.png
    downloadPng.value = cached.downloadPng
    loading.value = false
    error.value = ''
    return
  }
  requestController = new AbortController()
  loading.value = true
  error.value = ''
  generatedPng.value = ''
  downloadPng.value = ''
  imageDimensions.value = null
  try {
    const result = await generatePreview(
      props.photo.id,
      { dithering_method: mode.value, gb_color_palette: palette.value },
      requestController.signal,
    )
    if (currentRevision !== revision.value || !props.photo) return
    generatedPng.value = result.png || ''
    downloadPng.value = result.download_png || result.png || ''
    setGeneratedPreview(cacheKey, {
      png: generatedPng.value,
      downloadPng: downloadPng.value,
    })
  } catch (requestError) {
    if (requestError.name !== 'AbortError' && currentRevision === revision.value) {
      error.value = requestError.message || 'Could not generate preview'
    }
  } finally {
    if (currentRevision === revision.value) {
      loading.value = false
    }
  }
}

function setTab(nextTab) {
  tab.value = nextTab
  imageDimensions.value = null
}

function handleTabKeydown(event) {
  if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const nextTab = event.key === 'Home'
    ? 'original'
    : event.key === 'End'
      ? 'dithered'
      : tab.value === 'original' ? 'dithered' : 'original'
  setTab(nextTab)
  nextTick(() => document.getElementById(`preview-${nextTab}-tab`)?.focus())
}

function closeDialog() {
  if (dialogRef.value?.open) dialogRef.value.close()
  else emit('close')
}

function handleDocumentPointerDown(event) {
  const dialog = dialogRef.value
  if (!dialog?.open || deleteDialogRef.value?.open) return
  const bounds = dialog.getBoundingClientRect()
  const outside = event.clientX < bounds.left
    || event.clientX > bounds.right
    || event.clientY < bounds.top
    || event.clientY > bounds.bottom
  if (outside) closeDialog()
}

function handleClose() {
  cancelPreviewRequest()
  emit('close')
}

function rotate(direction) {
  rotation.value = (rotation.value + direction + 4) % 4
  clampPan()
}

function handleImageLoad(event) {
  markImagePreviewLoaded(imageSource.value)
  imageDimensions.value = {
    width: event.target.naturalWidth,
    height: event.target.naturalHeight,
  }
  nextTick(updateViewportSize)
  clampPan()
}

function resetViewport() {
  zoom.value = 1
  panX.value = 0
  panY.value = 0
  dragging.value = false
  dragOrigin.value = null
}

function updateViewportSize() {
  if (!stageRef.value) return
  viewportSize.value = {
    width: stageRef.value.clientWidth,
    height: stageRef.value.clientHeight,
  }
  clampPan()
}

function setZoom(nextZoom) {
  zoom.value = Math.min(maxZoom, Math.max(1, Math.round(nextZoom)))
  clampPan()
}

function zoomIn() {
  setZoom(zoom.value + 1)
}

function zoomOut() {
  setZoom(zoom.value - 1)
}

function panBounds() {
  const stage = stageRef.value
  if (!stage || !visualDimensions.value) return { x: 0, y: 0 }
  return {
    x: Math.max(0, (visualDimensions.value.width - stage.clientWidth) / 2),
    y: Math.max(0, (visualDimensions.value.height - stage.clientHeight) / 2),
  }
}

function clampPan() {
  const bounds = panBounds()
  panX.value = Math.min(bounds.x, Math.max(-bounds.x, panX.value))
  panY.value = Math.min(bounds.y, Math.max(-bounds.y, panY.value))
}

function startPan(event) {
  event.preventDefault()
  const bounds = panBounds()
  if (!bounds.x && !bounds.y) return
  dragging.value = true
  dragOrigin.value = {
    pointerId: event.pointerId,
    x: event.clientX,
    y: event.clientY,
    panX: panX.value,
    panY: panY.value,
  }
  event.currentTarget.setPointerCapture?.(event.pointerId)
}

function movePan(event) {
  if (!dragging.value || !dragOrigin.value || event.pointerId !== dragOrigin.value.pointerId) return
  event.preventDefault()
  panX.value = dragOrigin.value.panX + event.clientX - dragOrigin.value.x
  panY.value = dragOrigin.value.panY + event.clientY - dragOrigin.value.y
  clampPan()
}

function stopPan(event) {
  if (dragOrigin.value && event.pointerId !== dragOrigin.value.pointerId) return
  event.currentTarget.releasePointerCapture?.(event.pointerId)
  dragging.value = false
  dragOrigin.value = null
}

async function rotateBlob(blob) {
  const bitmap = await createImageBitmap(blob)
  const canvas = document.createElement('canvas')
  canvas.width = rotation.value % 2 ? bitmap.height : bitmap.width
  canvas.height = rotation.value % 2 ? bitmap.width : bitmap.height
  const context = canvas.getContext('2d')
  context.imageSmoothingEnabled = false
  context.translate(canvas.width / 2, canvas.height / 2)
  context.rotate(rotation.value * Math.PI / 2)
  context.drawImage(bitmap, -bitmap.width / 2, -bitmap.height / 2)
  bitmap.close()
  return new Promise((resolve, reject) => {
    canvas.toBlob((result) => result ? resolve(result) : reject(new Error('Could not rotate download')), 'image/png')
  })
}

async function downloadImage(kind, event) {
  event.preventDefault()
  const href = kind === 'original' ? props.photo?.original_path : ditheredDownload.value
  if (!href || (kind === 'dithered' && loading.value)) return
  const filename = kind === 'original' ? props.photo.filename : ditheredFilename.value
  try {
    if (!rotation.value) {
      const link = document.createElement('a')
      link.href = href
      link.download = filename
      link.click()
      return
    }
    const response = await fetch(href)
    if (!response.ok) throw new Error('Could not download image')
    const rotated = await rotateBlob(await response.blob())
    const link = document.createElement('a')
    link.href = URL.createObjectURL(rotated)
    link.download = filename
    link.click()
    setTimeout(() => URL.revokeObjectURL(link.href), 60000)
  } catch (downloadError) {
    error.value = downloadError.message
  }
}

async function sendToDisplay() {
  if (!props.photo || loading.value || sending.value || (isGenerated.value && !generatedPng.value)) return
  sending.value = true
  error.value = ''
  try {
    const result = isGenerated.value
      ? await displayPreview({ png: generatedPng.value })
      : await displayPhoto(props.photo.id)
    emit('notify', result.message || 'sent to screen')
  } catch (sendError) {
    error.value = sendError.message || 'Could not display photo'
  } finally {
    sending.value = false
  }
}

async function savePreview() {
  if (!props.photo || !hasChanges.value || saving.value || loading.value || (isGenerated.value && !generatedPng.value)) return
  saving.value = true
  error.value = ''
  try {
    const result = await savePhotoPreview(props.photo.id, {
      png: isGenerated.value ? generatedPng.value : null,
      rotation: rotation.value,
      dithering_method: effectiveDitherMode.value,
      gb_color_palette: palette.value,
    })
    const updatedPhoto = {
      ...props.photo,
      ...(result.photo || {}),
      has_dithered: true,
      rotation: result.rotation ?? rotation.value,
      dithering_method: result.dithering_method || effectiveDitherMode.value,
      gb_color_palette: result.gb_color_palette || palette.value,
    }
    initialMode.value = 'saved'
    initialPalette.value = updatedPhoto.gb_color_palette
    initialRotation.value = updatedPhoto.rotation
    mode.value = 'saved'
    generatedPng.value = ''
    downloadPng.value = ''
    savedRevision.value = updatedPhoto.dithered_updated_at || String(Date.now())
    emit('photo-updated', updatedPhoto)
    emit('notify', result.message || 'Dithered photo saved')
  } catch (saveError) {
    error.value = saveError.message || 'Could not save dithered photo'
  } finally {
    saving.value = false
  }
}

function openDeleteConfirmation() {
  if (!props.photo || deleting.value) return
  deleteDialogRef.value?.showModal()
}

function closeDeleteConfirmation() {
  if (deleteDialogRef.value?.open) deleteDialogRef.value.close()
}

async function confirmDelete() {
  if (!props.photo || deleting.value) return
  deleting.value = true
  error.value = ''
  try {
    await deletePhoto(props.photo.id)
    closeDeleteConfirmation()
    emit('deleted', props.photo.id)
  } catch (deleteError) {
    error.value = deleteError.message || 'Could not delete photo'
  } finally {
    deleting.value = false
  }
}

async function runExtension(action) {
  if (!props.photo || extensionBusy.value) return
  extensionBusy.value = action.id
  try {
    const result = await runExtensionAction(action.id, props.photo.id)
    emit('notify', result.message || 'Action complete')
  } catch (actionError) {
    error.value = actionError.message || 'Extension action failed'
  } finally {
    extensionBusy.value = ''
  }
}

async function updateCarouselSelection(event) {
  if (!props.photo || carouselSaving.value) return
  const included = event.target.checked
  carouselSaving.value = true
  error.value = ''
  try {
    const result = await setCarouselPhoto(props.photo.id, included)
    carouselIncluded.value = Boolean(result.included)
    emit('carousel-updated', { id: props.photo.id, included: carouselIncluded.value })
    emit('notify', carouselIncluded.value ? 'Photo added to carousel' : 'Photo removed from carousel')
  } catch (selectionError) {
    event.target.checked = carouselIncluded.value
    error.value = selectionError.message || 'Could not save carousel selection'
  } finally {
    carouselSaving.value = false
  }
}

onMounted(() => {
  document.addEventListener('pointerdown', handleDocumentPointerDown)
  if (typeof ResizeObserver !== 'undefined') {
    stageResizeObserver = new ResizeObserver(updateViewportSize)
    if (stageRef.value) stageResizeObserver.observe(stageRef.value)
  }
  nextTick(updateViewportSize)
})
onUnmounted(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
  stageResizeObserver?.disconnect()
  cancelPreviewRequest()
})
</script>

<template>
  <dialog ref="dialogRef" class="preview-dialog photo-preview-dialog" aria-labelledby="preview-title" @close="handleClose">
    <div class="dialog-header">
      <h2 id="preview-title">{{ photo?.filename || photo?.id }}</h2>
      <label class="carousel-toggle">
        <input
          type="checkbox"
          :checked="carouselIncluded"
          :disabled="!photo || carouselSaving"
          @change="updateCarouselSelection"
        />
        include in carousel
      </label>
      <button class="icon-button" type="button" aria-label="Close preview" @click="closeDialog">&times;</button>
    </div>
    <div class="preview-tab-row">
      <div class="preview-tabs" role="tablist" aria-label="Photo version" @keydown="handleTabKeydown">
        <button
          id="preview-original-tab"
          class="tab-button"
          :class="{ active: tab === 'original' }"
          type="button"
          role="tab"
          :aria-selected="tab === 'original'"
          @click="setTab('original')"
        >
          original
        </button>
        <button
          id="preview-dithered-tab"
          class="tab-button"
          :class="{ active: tab === 'dithered' }"
          type="button"
          role="tab"
          :aria-selected="tab === 'dithered'"
          @click="setTab('dithered')"
        >
          dithered
        </button>
      </div>
      <div class="preview-rotation" aria-label="Rotate preview and downloads">
        <button class="icon-button" type="button" aria-label="Rotate left" @click="rotate(-1)">&#8630;</button>
        <button class="icon-button" type="button" aria-label="Rotate right" @click="rotate(1)">&#8631;</button>
        <span class="preview-zoom-controls" aria-label="Preview zoom">
          <button class="icon-button" type="button" aria-label="Zoom out" title="Zoom out" :disabled="zoom <= 1" @click="zoomOut">&minus;</button>
          <span class="preview-zoom-value" aria-live="polite">{{ zoom }}x</span>
          <button class="icon-button" type="button" aria-label="Zoom in" title="Zoom in" :disabled="zoom >= maxZoom" @click="zoomIn">+</button>
        </span>
      </div>
    </div>
    <div class="preview-panel" role="tabpanel">
      <div class="preview-content">
        <div class="preview-visual">
          <div
            ref="stageRef"
            class="preview-stage"
            :class="{ dragging }"
            :style="stageStyle"
            @pointerdown.prevent="startPan"
            @pointermove.prevent="movePan"
            @pointerup="stopPan"
            @pointercancel="stopPan"
            @lostpointercapture="stopPan"
          >
            <img
              v-if="imageSource"
              class="preview-image"
              :class="{ dithered: tab === 'dithered' }"
              :src="imageSource"
              :alt="`${tab} preview of ${photo?.filename || photo?.id}`"
              draggable="false"
              v-bind="imageAttributes()"
              :style="imageStyle"
              @load="handleImageLoad"
              @dragstart.prevent
            />
            <span v-else class="preview-placeholder">{{ loading ? 'generating preview...' : 'preview unavailable' }}</span>
          </div>
          <details v-if="tab === 'dithered'" class="preview-controls-disclosure" :open="ditherControlsOpen" @toggle="ditherControlsOpen = $event.currentTarget.open">
            <summary class="preview-controls-summary">
              <span>dither mode</span>
              <span class="preview-controls-summary-state">{{ modeLabel }}</span>
            </summary>
            <div class="preview-controls preview-control-content">
              <label>
                dither mode
                <select v-model="mode" :disabled="sending || saving">
                  <option value="saved" :disabled="!photo?.has_dithered">saved version</option>
                  <option value="floyd_steinberg">floyd steinberg</option>
                  <option value="ordered">ordered (bayer)</option>
                  <option value="bayer_natural_pair">bayer natural pair</option>
                  <option value="gb-default">game boy default</option>
                  <option value="gb-default-color">game boy default (color)</option>
                </select>
              </label>
              <label v-if="mode === 'gb-default-color'">
                color palette
                <select v-model="palette" :disabled="sending || saving">
                  <option value="blue_yellow">black / blue / yellow / white</option>
                  <option value="green_yellow">black / green / yellow / white</option>
                  <option value="red_yellow">black / red / yellow / white</option>
                  <option value="blue_red">black / blue / red / white</option>
                  <option value="blue_green">black / blue / green / white</option>
                </select>
              </label>
            </div>
          </details>
        </div>
        <details class="photo-metadata" :open="metadataOpen" @toggle="metadataOpen = $event.currentTarget.open">
          <summary>photo information</summary>
          <dl class="photo-metadata-list">
            <template v-for="([label, value]) in metadataRows" :key="label">
              <dt :class="{ 'photo-metadata-json-label': parseJsonValue(value) }">{{ label }}</dt>
              <dd v-if="parseJsonValue(value)" class="photo-metadata-json">
                <PhotoMetadataTree :value="parseJsonValue(value)" />
              </dd>
              <dd v-else :title="value">{{ value }}</dd>
            </template>
          </dl>
        </details>
      </div>
    </div>
    <p class="dialog-status" role="status" aria-live="polite">{{ error }}</p>
    <div class="dialog-actions">
      <button
        class="action-button primary"
        type="button"
        :disabled="!photo"
        @click="downloadImage('original', $event)"
      >
        original download
      </button>
      <button
        class="action-button secondary"
        type="button"
        :disabled="!ditheredDownload || loading"
        @click="downloadImage('dithered', $event)"
      >
        dithered download
      </button>
      <button class="action-button success" type="button" :disabled="loading || sending" @click="sendToDisplay">
        {{ sending ? 'sending...' : 'display' }}
      </button>
      <button class="action-button primary" type="button" :disabled="!hasChanges || loading || saving" @click="savePreview">
        {{ saving ? 'saving...' : 'save' }}
      </button>
      <button
        v-for="action in extensionActions.filter((item) => !item.requires_dithered || photo?.has_dithered)"
        v-show="!isGenerated && photo?.has_dithered"
        :key="action.id"
        class="action-button secondary"
        type="button"
        :disabled="extensionBusy === action.id"
        @click="runExtension(action)"
      >
        {{ extensionBusy === action.id ? 'working...' : action.action_label }}
      </button>
      <button class="action-button danger preview-delete-button" type="button" :disabled="deleting" @click="openDeleteConfirmation">
        delete
      </button>
    </div>
  </dialog>
  <dialog ref="deleteDialogRef" class="confirmation-dialog" aria-labelledby="delete-photo-title">
    <h2 id="delete-photo-title">delete photo?</h2>
    <p>This permanently deletes the original and dithered images.</p>
    <div class="dialog-actions confirmation-actions">
      <button class="action-button secondary" type="button" :disabled="deleting" @click="closeDeleteConfirmation">cancel</button>
      <button class="action-button danger" type="button" :disabled="deleting" @click="confirmDelete">
        {{ deleting ? 'deleting...' : 'delete photo' }}
      </button>
    </div>
  </dialog>
</template>
