<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { getPreviewTelemetry, setPreviewFocus, stopPreview } from '../api/dashboardApi'

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
const telemetry = ref(null)
const telemetryError = ref('')
const focusMode = ref('continuous')
const focusPosition = ref(null)
const focusDragging = ref(false)
const focusBusy = ref(false)
const focusError = ref('')
let telemetryTimer = null
let telemetryPollToken = 0
let streamReleasePromise = Promise.resolve()

const focusRange = computed(() => telemetry.value?.focus_range || null)
const focusModeLabel = computed(() => telemetry.value?.focus_mode || focusMode.value)

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
    telemetry.value = null
    telemetryError.value = ''
    focusError.value = ''
    focusMode.value = 'continuous'
    focusPosition.value = null
    startTelemetryPolling()
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
  telemetryPollToken += 1
  clearTimeout(telemetryTimer)
  telemetryTimer = null
  previewClientId.value = ''
  streamMounted.value = false
  streamKey.value += 1
  telemetry.value = null
  telemetryError.value = ''
  focusError.value = ''
  focusDragging.value = false
  if (clientId) {
    streamReleasePromise = stopPreview(clientId).catch(() => {})
  }
}

function startTelemetryPolling() {
  telemetryPollToken += 1
  const token = telemetryPollToken
  clearTimeout(telemetryTimer)
  telemetryTimer = null
  pollTelemetry(token)
}

async function pollTelemetry(token) {
  if (token !== telemetryPollToken || !props.open || !previewClientId.value) return
  try {
    const result = await getPreviewTelemetry(previewClientId.value)
    if (token !== telemetryPollToken || !props.open) return
    telemetry.value = result
    telemetryError.value = ''
    if (!focusBusy.value && result.focus_mode) focusMode.value = result.focus_mode
    if (!focusDragging.value && result.lens_position !== null && result.lens_position !== undefined) {
      focusPosition.value = result.lens_position
    }
    if (result.frame_age_seconds === null || result.frame_age_seconds > 2.5) {
      streamState.value = streamState.value === 'connecting' ? 'connecting' : 'stalled'
    } else if (streamState.value !== 'error' && streamState.value !== 'disconnected') {
      streamState.value = 'live'
    }
  } catch (error) {
    if (token !== telemetryPollToken || !props.open) return
    telemetryError.value = error.message || 'camera telemetry unavailable'
    if (streamState.value !== 'error') streamState.value = 'stalled'
  } finally {
    if (token === telemetryPollToken && props.open) {
      telemetryTimer = setTimeout(() => pollTelemetry(token), 500)
    }
  }
}

function createClientId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}

function handleImageLoad() {
  if (streamState.value !== 'error' && streamState.value !== 'disconnected') streamState.value = 'live'
}

function handleImageError() {
  streamState.value = 'disconnected'
}

async function retryStream() {
  if (!props.open || !previewClientId.value) return
  const clientId = previewClientId.value
  streamMounted.value = false
  streamState.value = 'connecting'
  streamKey.value += 1
  await stopPreview(clientId).catch(() => {})
  if (!props.open || clientId !== previewClientId.value) return
  streamUrl.value = `/api/preview/stream?client_id=${encodeURIComponent(clientId)}`
  streamMounted.value = true
  startTelemetryPolling()
}

async function applyFocusMode() {
  if (!previewClientId.value) return
  focusBusy.value = true
  focusError.value = ''
  try {
    const result = await setPreviewFocus({
      client_id: previewClientId.value,
      action: 'set_mode',
      mode: focusMode.value,
    })
    if (result.focus_mode) focusMode.value = result.focus_mode
  } catch (error) {
    focusError.value = error.message || 'Could not change focus mode'
    if (telemetry.value?.focus_mode) focusMode.value = telemetry.value.focus_mode
  } finally {
    focusBusy.value = false
  }
}

function startFocusDrag(event) {
  focusDragging.value = true
  focusPosition.value = Number(event.target.value)
}

async function applyFocusPosition() {
  if (!previewClientId.value || focusMode.value !== 'manual' || focusPosition.value === null) {
    focusDragging.value = false
    return
  }
  focusBusy.value = true
  focusError.value = ''
  try {
    const result = await setPreviewFocus({
      client_id: previewClientId.value,
      action: 'set_position',
      lens_position: Number(focusPosition.value),
    })
    if (result.lens_position !== undefined) focusPosition.value = result.lens_position
  } catch (error) {
    focusError.value = error.message || 'Could not update manual focus'
  } finally {
    focusDragging.value = false
    focusBusy.value = false
  }
}

async function focusCenter() {
  if (!previewClientId.value) return
  focusBusy.value = true
  focusError.value = ''
  try {
    const result = await setPreviewFocus({
      client_id: previewClientId.value,
      action: 'focus_center',
    })
    if (result.lens_position !== undefined) focusPosition.value = result.lens_position
  } catch (error) {
    focusError.value = error.message || 'Could not focus the center'
  } finally {
    focusBusy.value = false
  }
}

function formatShutter(exposureTime) {
  if (exposureTime === null || exposureTime === undefined || exposureTime <= 0) return '--'
  const seconds = exposureTime / 1_000_000
  if (seconds >= 1) return `${seconds.toFixed(1)} s`
  return `1/${Math.max(1, Math.round(1 / seconds))} s`
}

function formatValue(value, suffix = '') {
  return value === null || value === undefined ? '--' : `${Number(value).toFixed(1)}${suffix}`
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
        <span v-else-if="streamState === 'stalled'" class="preview-placeholder">camera feed stalled</span>
        <span v-else-if="streamState === 'disconnected'" class="preview-placeholder">camera feed disconnected</span>
      </div>
      <label class="guidelines-toggle">
        <input v-model="showGuidelines" type="checkbox" />
        shooting guidelines
      </label>
      <p class="dialog-status" role="status" aria-live="polite">
        {{ streamState === 'live' ? 'camera live' : streamState === 'stalled' ? 'waiting for a fresh frame' : streamState === 'disconnected' ? 'camera disconnected' : 'starting camera stream...' }}
      </p>
      <div v-if="telemetry" class="preview-telemetry" aria-label="Camera telemetry">
        <span><strong>shutter</strong> {{ formatShutter(telemetry.exposure_time_us) }}</span>
        <span><strong>gain</strong> {{ formatValue(telemetry.analogue_gain, 'x') }}</span>
        <span><strong>EV</strong> {{ formatValue(telemetry.exposure_value, '') }}</span>
        <span><strong>focus</strong> {{ telemetry.focus_state || '--' }}</span>
        <span><strong>rate</strong> {{ formatValue(telemetry.frame_rate, ' fps') }}</span>
        <span v-if="telemetry.colour_temperature !== undefined"><strong>WB</strong> {{ formatValue(telemetry.colour_temperature, ' K') }}</span>
        <span v-if="telemetry.lux !== undefined"><strong>lux</strong> {{ formatValue(telemetry.lux) }}</span>
      </div>
      <p v-if="telemetryError" class="inline-error">{{ telemetryError }}</p>
      <div class="focus-controls">
        <label>
          focus mode
          <select v-model="focusMode" :disabled="focusBusy" @change="applyFocusMode">
            <option value="manual">manual</option>
            <option value="auto">auto</option>
            <option value="continuous">continuous</option>
          </select>
        </label>
        <label class="focus-position-control">
          lens position
          <input
            type="range"
            :min="focusRange?.min ?? 0"
            :max="focusRange?.max ?? 1"
            :step="focusRange?.step ?? 0.1"
            :value="focusPosition ?? focusRange?.min ?? 0"
            :disabled="focusMode !== 'manual' || focusBusy || !focusRange"
            @input="startFocusDrag"
            @change="applyFocusPosition"
          />
          <span class="focus-position-value">{{ focusPosition === null ? '--' : Number(focusPosition).toFixed(1) }}</span>
        </label>
        <button class="action-button secondary" type="button" :disabled="focusBusy || !telemetry" @click="focusCenter">
          {{ focusBusy ? 'focusing...' : 'focus center' }}
        </button>
      </div>
      <p class="focus-status" role="status" aria-live="polite">
        {{ focusError || `mode: ${focusModeLabel}` }}
      </p>
    </div>
    <div class="dialog-actions live-preview-actions">
      <button class="action-button primary" type="button" :disabled="captureBusy" @click="emit('capture')">
        {{ captureBusy ? 'capturing...' : 'capture photo' }}
      </button>
      <button v-if="streamState === 'disconnected'" class="action-button secondary" type="button" @click="retryStream">retry</button>
      <button class="action-button secondary" type="button" @click="closeDialog">close</button>
    </div>
  </dialog>
</template>
