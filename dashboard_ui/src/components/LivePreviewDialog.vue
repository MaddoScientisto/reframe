<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { stopPreview } from '../api/dashboardApi'

const props = defineProps({
  open: { type: Boolean, default: false },
  captureBusy: { type: Boolean, default: false },
  aspectRatio: { type: String, default: '3 / 2' },
})

const emit = defineEmits(['close', 'capture'])

const dialogRef = ref(null)
const streamUrl = ref('/api/preview/stream')
const streamKey = ref(0)
const streamState = ref('connecting')
const streamMounted = ref(false)
const showGuidelines = ref(false)
const previewClientId = ref('')
let streamReleasePromise = Promise.resolve()

watch(
  () => props.open,
  async (open) => {
    if (!open) {
      stopStream()
      if (dialogRef.value?.open) dialogRef.value.close()
      return
    }
    await streamReleasePromise
    if (!props.open) return
    previewClientId.value = createClientId()
    streamUrl.value = `/api/preview/stream?client_id=${encodeURIComponent(previewClientId.value)}`
    streamKey.value += 1
    streamState.value = 'connecting'
    streamMounted.value = true
    showGuidelines.value = false
    await nextTick()
    if (!dialogRef.value?.open) dialogRef.value?.showModal()
  },
  { immediate: true },
)

function closeDialog() {
  stopStream()
  if (dialogRef.value?.open) dialogRef.value.close()
  else emit('close')
}

function handleClose() {
  stopStream()
  emit('close')
}

function stopStream() {
  const clientId = previewClientId.value
  previewClientId.value = ''
  streamMounted.value = false
  streamKey.value += 1
  if (clientId) {
    streamReleasePromise = stopPreview(clientId).catch(() => {})
  }
}

function createClientId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function handleImageLoad() {
  streamState.value = 'connected'
}

function handleImageError() {
  streamState.value = 'error'
}

onUnmounted(() => {
  stopStream()
  if (dialogRef.value?.open) dialogRef.value.close()
})
</script>

<template>
  <dialog ref="dialogRef" class="preview-dialog live-preview-dialog" aria-labelledby="live-preview-title" @cancel="stopStream" @close="handleClose">
    <div class="dialog-header">
      <div>
        <p class="eyebrow">camera sensor</p>
        <h2 id="live-preview-title">live preview</h2>
      </div>
      <button class="icon-button" type="button" aria-label="Close live preview" @click="closeDialog">&times;</button>
    </div>
    <div class="preview-panel live-preview-panel">
      <div class="preview-stage live-preview-stage" :style="{ aspectRatio }">
        <img
          v-if="streamMounted && open && streamState !== 'error'"
          :key="streamKey"
          class="live-preview-image"
          :src="streamUrl"
          alt="Live camera preview"
          @load="handleImageLoad"
          @error="handleImageError"
        />
        <div v-if="showGuidelines" class="shooting-guidelines" aria-hidden="true">
          <span class="guideline-line guideline-vertical guideline-vertical-left"></span>
          <span class="guideline-line guideline-vertical guideline-vertical-right"></span>
          <span class="guideline-line guideline-horizontal guideline-horizontal-top"></span>
          <span class="guideline-line guideline-horizontal guideline-horizontal-bottom"></span>
          <span class="guideline-crosshair guideline-crosshair-horizontal"></span>
          <span class="guideline-crosshair guideline-crosshair-vertical"></span>
        </div>
        <span v-if="streamState === 'connecting'" class="preview-placeholder">connecting to camera...</span>
        <span v-else-if="streamState === 'error'" class="preview-placeholder">live preview unavailable</span>
      </div>
      <label class="guidelines-toggle">
        <input v-model="showGuidelines" type="checkbox" />
        shooting guidelines
      </label>
      <p class="dialog-status" role="status" aria-live="polite">
        {{ streamState === 'connected' ? 'camera ready' : streamState === 'error' ? 'check the camera connection' : 'starting camera stream...' }}
      </p>
    </div>
    <div class="dialog-actions live-preview-actions">
      <button class="action-button primary" type="button" :disabled="captureBusy" @click="emit('capture')">
        {{ captureBusy ? 'capturing...' : 'capture photo' }}
      </button>
      <button class="action-button secondary" type="button" @click="closeDialog">close</button>
    </div>
  </dialog>
</template>
