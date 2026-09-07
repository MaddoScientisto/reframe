<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import {
  displayPhoto,
  displayPreview,
  ditheredDownloadUrl,
  generatePreview,
  runExtensionAction,
  setCarouselPhoto,
} from '../api/dashboardApi'

const props = defineProps({
  photo: { type: Object, default: null },
  extensionActions: { type: Array, default: () => [] },
})

const emit = defineEmits(['close', 'notify', 'carousel-updated'])

const dialogRef = ref(null)
const tab = ref('dithered')
const mode = ref('saved')
const palette = ref('blue_yellow')
const generatedPng = ref('')
const downloadPng = ref('')
const loading = ref(false)
const sending = ref(false)
const error = ref('')
const rotation = ref(0)
const extensionBusy = ref('')
const carouselIncluded = ref(false)
const carouselSaving = ref(false)
const revision = ref(0)
let requestController = null

const isGenerated = computed(() => mode.value !== 'saved')
const previewSource = computed(() => {
  if (!props.photo) return ''
  if (!isGenerated.value) return props.photo.dithered_path || ''
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

function cancelPreviewRequest() {
  requestController?.abort()
  requestController = null
}

function resetPreviewState(photo) {
  cancelPreviewRequest()
  tab.value = photo.has_dithered ? 'dithered' : 'original'
  mode.value = photo.has_dithered ? 'saved' : 'floyd_steinberg'
  palette.value = 'blue_yellow'
  generatedPng.value = ''
  downloadPng.value = ''
  loading.value = false
  sending.value = false
  error.value = ''
  rotation.value = 0
  extensionBusy.value = ''
  carouselIncluded.value = Boolean(photo.carousel_enabled)
  carouselSaving.value = false
}

watch(
  () => props.photo,
  async (photo) => {
    if (!photo) {
      cancelPreviewRequest()
      if (dialogRef.value?.open) dialogRef.value.close()
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
  requestController = new AbortController()
  loading.value = true
  error.value = ''
  generatedPng.value = ''
  downloadPng.value = ''
  try {
    const result = await generatePreview(
      props.photo.id,
      { dithering_method: mode.value, gb_color_palette: palette.value },
      requestController.signal,
    )
    if (currentRevision !== revision.value || !props.photo) return
    generatedPng.value = result.png || ''
    downloadPng.value = result.download_png || result.png || ''
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

function handleClose() {
  cancelPreviewRequest()
  emit('close')
}

function rotate(direction) {
  rotation.value = (rotation.value + direction + 4) % 4
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

onUnmounted(cancelPreviewRequest)
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
    <div class="preview-panel" role="tabpanel">
      <div class="preview-stage">
        <img
          v-if="imageSource"
          class="preview-image"
          :class="{ dithered: tab === 'dithered' }"
          :src="imageSource"
          :alt="`${tab} preview of ${photo?.filename || photo?.id}`"
          :style="{ transform: `rotate(${rotation * 90}deg)` }"
        />
        <span v-else class="preview-placeholder">{{ loading ? 'generating preview...' : 'preview unavailable' }}</span>
      </div>
      <div class="preview-rotation" aria-label="Rotate preview and downloads">
        <button class="icon-button" type="button" aria-label="Rotate left" @click="rotate(-1)">&#8630;</button>
        <button class="icon-button" type="button" aria-label="Rotate right" @click="rotate(1)">&#8631;</button>
      </div>
      <div v-if="tab === 'dithered'" class="preview-controls">
        <label>
          dither mode
          <select v-model="mode" :disabled="sending">
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
          <select v-model="palette" :disabled="sending">
            <option value="blue_yellow">black / blue / yellow / white</option>
            <option value="green_yellow">black / green / yellow / white</option>
            <option value="red_yellow">black / red / yellow / white</option>
            <option value="blue_red">black / blue / red / white</option>
            <option value="blue_green">black / blue / green / white</option>
          </select>
        </label>
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
    </div>
  </dialog>
</template>
