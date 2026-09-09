<script setup>
import { computed, nextTick, onUnmounted, ref, watch } from 'vue'
import { getPreviewTelemetry, setPreviewControls, setPreviewFocus, stopPreview } from '../api/dashboardApi'

const previewPreferencesKey = 'reframe.livePreviewPreferences'

function loadPreviewPreferences() {
  try {
    const saved = JSON.parse(localStorage.getItem(previewPreferencesKey) || '{}')
    return saved && typeof saved === 'object' ? saved : {}
  } catch {
    return {}
  }
}

function savePreviewPreferences(preferences) {
  try {
    localStorage.setItem(previewPreferencesKey, JSON.stringify(preferences))
  } catch {}
}

function storedNumber(value, fallback) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

const savedPreferences = loadPreviewPreferences()

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
const showGuidelines = ref(savedPreferences.showGuidelines === true)
const previewClientId = ref('')
const telemetry = ref(null)
const telemetryError = ref('')
const focusMode = ref(['manual', 'auto', 'continuous'].includes(savedPreferences.focusMode) ? savedPreferences.focusMode : 'continuous')
const focusPosition = ref(storedNumber(savedPreferences.focusPosition, null))
const focusDragging = ref(false)
const focusBusy = ref(false)
const focusError = ref('')
const focusControlsOpen = ref(false)
const exposureMode = ref('auto')
const exposureModeBusy = ref(false)
const exposureValue = ref(0)
const exposureBusy = ref(false)
const exposureError = ref('')
const exposureControlsOpen = ref(false)
const savedWhiteBalanceMode = ['auto', 'preset', 'manual'].includes(savedPreferences.whiteBalanceMode)
  ? savedPreferences.whiteBalanceMode
  : 'auto'
const savedWhiteBalancePreset = savedPreferences.whiteBalancePreset || 'daylight'
const whiteBalanceMode = ref(savedWhiteBalanceMode === 'preset' && savedWhiteBalancePreset === 'custom' ? 'manual' : savedWhiteBalanceMode)
const whiteBalancePreset = ref(savedWhiteBalancePreset)
const redGain = ref(storedNumber(savedPreferences.redGain, 1))
const blueGain = ref(storedNumber(savedPreferences.blueGain, 1))
const whiteBalanceDragging = ref(false)
const whiteBalanceBusy = ref(false)
const whiteBalanceError = ref('')
const whiteBalanceControlsOpen = ref(false)
let telemetryTimer = null
let telemetryPollToken = 0
let streamReleasePromise = Promise.resolve()

const focusRange = computed(() => telemetry.value?.focus_range || null)
const focusModeLabel = computed(() => telemetry.value?.focus_mode || focusMode.value)
const whiteBalanceCapabilities = computed(() => telemetry.value?.white_balance_capabilities || null)
const whiteBalancePresets = computed(() => (whiteBalanceCapabilities.value?.supported_presets || [])
  .filter((preset) => preset !== 'custom'))
const cameraControlBusy = computed(() => focusBusy.value || exposureModeBusy.value || exposureBusy.value || whiteBalanceBusy.value)

watch(
  [showGuidelines, focusMode, focusPosition, whiteBalanceMode, whiteBalancePreset, redGain, blueGain],
  () => savePreviewPreferences({
    showGuidelines: showGuidelines.value,
    focusMode: focusMode.value,
    focusPosition: focusPosition.value,
    whiteBalanceMode: whiteBalanceMode.value,
    whiteBalancePreset: whiteBalancePreset.value,
    redGain: redGain.value,
    blueGain: blueGain.value,
  }),
)

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
    focusControlsOpen.value = false
    whiteBalanceControlsOpen.value = false
    exposureControlsOpen.value = false
    telemetry.value = null
    telemetryError.value = ''
    focusError.value = ''
    exposureMode.value = 'auto'
    exposureModeBusy.value = false
    exposureError.value = ''
    whiteBalanceError.value = ''
    exposureValue.value = 0
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
  exposureModeBusy.value = false
  exposureError.value = ''
  whiteBalanceError.value = ''
  focusDragging.value = false
  whiteBalanceDragging.value = false
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
    if (!exposureBusy.value && result.exposure_value !== null && result.exposure_value !== undefined) {
      exposureValue.value = Number(result.exposure_value)
    }
    if (!exposureModeBusy.value && result.exposure_mode) exposureMode.value = result.exposure_mode
    if (!whiteBalanceBusy.value && !whiteBalanceDragging.value) {
      if (result.white_balance_mode) whiteBalanceMode.value = result.white_balance_mode
      if (result.white_balance_preset) whiteBalancePreset.value = result.white_balance_preset
      if (result.white_balance_mode === 'preset' && result.white_balance_preset === 'custom') {
        whiteBalanceMode.value = 'manual'
      }
      if (result.colour_gains) {
        redGain.value = Number(result.colour_gains.red)
        blueGain.value = Number(result.colour_gains.blue)
      }
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

async function applyExposureValue() {
  if (!previewClientId.value || exposureMode.value !== 'auto') return
  exposureBusy.value = true
  exposureError.value = ''
  try {
    const result = await setPreviewControls({
      client_id: previewClientId.value,
      action: 'set_exposure_value',
      exposure_value: Number(exposureValue.value),
    })
    if (result.exposure_value !== undefined) exposureValue.value = Number(result.exposure_value)
  } catch (error) {
    exposureError.value = error.message || 'Could not update exposure value'
    if (telemetry.value?.exposure_value !== undefined) exposureValue.value = Number(telemetry.value.exposure_value)
  } finally {
    exposureBusy.value = false
  }
}

async function applyExposureMode() {
  if (!previewClientId.value) return
  exposureModeBusy.value = true
  exposureError.value = ''
  try {
    const result = await setPreviewControls({
      client_id: previewClientId.value,
      action: 'set_exposure_mode',
      mode: exposureMode.value,
    })
    if (result.exposure_mode) exposureMode.value = result.exposure_mode
  } catch (error) {
    exposureError.value = error.message || 'Could not change exposure mode'
    if (telemetry.value?.exposure_mode) exposureMode.value = telemetry.value.exposure_mode
  } finally {
    exposureModeBusy.value = false
  }
}

function supportedWhiteBalancePreset() {
  return whiteBalancePresets.value.includes(whiteBalancePreset.value)
    ? whiteBalancePreset.value
    : whiteBalancePresets.value[0]
}

async function applyWhiteBalance() {
  if (!previewClientId.value) return
  if (whiteBalanceMode.value === 'preset' && !supportedWhiteBalancePreset()) {
    whiteBalanceError.value = 'No white-balance preset is available'
    return
  }
  whiteBalanceBusy.value = true
  whiteBalanceError.value = ''
  try {
    const preset = supportedWhiteBalancePreset() || whiteBalancePreset.value
    const result = await setPreviewControls({
      client_id: previewClientId.value,
      action: 'set_white_balance',
      mode: whiteBalanceMode.value,
      preset,
      red_gain: Number(redGain.value),
      blue_gain: Number(blueGain.value),
    })
    if (result.white_balance_mode) whiteBalanceMode.value = result.white_balance_mode
    if (result.white_balance_preset) whiteBalancePreset.value = result.white_balance_preset
    if (result.colour_gains) {
      redGain.value = Number(result.colour_gains.red)
      blueGain.value = Number(result.colour_gains.blue)
    }
  } catch (error) {
    whiteBalanceError.value = error.message || 'Could not update white balance'
    if (telemetry.value?.white_balance_mode) whiteBalanceMode.value = telemetry.value.white_balance_mode
  } finally {
    whiteBalanceBusy.value = false
    whiteBalanceDragging.value = false
  }
}

function startWhiteBalanceDrag(channel, event) {
  whiteBalanceDragging.value = true
  if (channel === 'red') redGain.value = Number(event.target.value)
  if (channel === 'blue') blueGain.value = Number(event.target.value)
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
      <div class="live-preview-sticky-header">
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
        <div class="preview-quick-actions">
          <label class="guidelines-toggle">
            <input v-model="showGuidelines" type="checkbox" />
            shooting guidelines
          </label>
          <button class="action-button secondary" type="button" :disabled="cameraControlBusy || !telemetry" @click="focusCenter">
            {{ focusBusy ? 'focusing...' : 'focus center' }}
          </button>
          <button class="action-button primary" type="button" :disabled="captureBusy" @click="emit('capture')">
            {{ captureBusy ? 'capturing...' : 'capture photo' }}
          </button>
        </div>
      </div>
      <div class="preview-utility-row">
        <p class="dialog-status" role="status" aria-live="polite">
          {{ streamState === 'live' ? 'camera live' : streamState === 'stalled' ? 'waiting for a fresh frame' : streamState === 'disconnected' ? 'camera disconnected' : 'starting camera stream...' }}
        </p>
      </div>
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
      <details class="preview-controls-disclosure" :open="focusControlsOpen" @toggle="focusControlsOpen = $event.currentTarget.open">
        <summary class="preview-controls-summary">
          <span>focus mode</span>
          <span class="preview-controls-summary-state">{{ focusModeLabel }}</span>
        </summary>
        <div class="focus-controls preview-control-content">
          <label>
            focus mode
            <select v-model="focusMode" :disabled="cameraControlBusy" @change="applyFocusMode">
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
              :disabled="focusMode !== 'manual' || cameraControlBusy || !focusRange"
              @input="startFocusDrag"
              @change="applyFocusPosition"
            />
            <span class="focus-position-value">{{ focusPosition === null ? '--' : Number(focusPosition).toFixed(1) }}</span>
          </label>
        </div>
        <p class="focus-status" role="status" aria-live="polite">
          {{ focusError || `mode: ${focusModeLabel}` }}
        </p>
      </details>
      <details class="preview-controls-disclosure" :open="whiteBalanceControlsOpen" @toggle="whiteBalanceControlsOpen = $event.currentTarget.open">
        <summary class="preview-controls-summary">
          <span>white balance mode</span>
          <span class="preview-controls-summary-state">{{ whiteBalanceMode }}</span>
        </summary>
        <div v-if="whiteBalanceCapabilities?.supported" class="white-balance-controls preview-control-content">
          <label>
            white balance mode
            <select v-model="whiteBalanceMode" :disabled="cameraControlBusy" @change="applyWhiteBalance">
              <option value="auto">auto</option>
              <option v-if="whiteBalanceCapabilities.preset_supported" value="preset">preset</option>
              <option v-if="whiteBalanceCapabilities.manual_supported" value="manual">custom</option>
            </select>
          </label>
          <label v-if="whiteBalanceMode === 'preset'">
            preset
            <select v-model="whiteBalancePreset" :disabled="cameraControlBusy" @change="applyWhiteBalance">
              <option v-for="preset in whiteBalancePresets" :key="preset" :value="preset">{{ preset }}</option>
            </select>
          </label>
          <div v-if="whiteBalanceMode === 'manual' && whiteBalanceCapabilities.manual_supported" class="custom-white-balance">
            <span class="control-section-label">custom white balance</span>
            <label>
              red gain
              <input
                type="range"
                :min="whiteBalanceCapabilities.colour_gains_range.red.min"
                :max="whiteBalanceCapabilities.colour_gains_range.red.max"
                :step="whiteBalanceCapabilities.colour_gains_range.red.step"
                :value="redGain"
                :disabled="cameraControlBusy"
                @input="startWhiteBalanceDrag('red', $event)"
                @change="applyWhiteBalance"
              />
              <span class="control-value">{{ formatValue(redGain, 'x') }}</span>
            </label>
            <label>
              blue gain
              <input
                type="range"
                :min="whiteBalanceCapabilities.colour_gains_range.blue.min"
                :max="whiteBalanceCapabilities.colour_gains_range.blue.max"
                :step="whiteBalanceCapabilities.colour_gains_range.blue.step"
                :value="blueGain"
                :disabled="cameraControlBusy"
                @input="startWhiteBalanceDrag('blue', $event)"
                @change="applyWhiteBalance"
              />
              <span class="control-value">{{ formatValue(blueGain, 'x') }}</span>
            </label>
          </div>
        </div>
        <p v-else class="control-unavailable">white-balance capabilities are not available yet</p>
        <p v-if="whiteBalanceError" class="inline-error">{{ whiteBalanceError }}</p>
      </details>
      <details class="preview-controls-disclosure" :open="exposureControlsOpen" @toggle="exposureControlsOpen = $event.currentTarget.open">
        <summary class="preview-controls-summary">
          <span>exposure</span>
          <span class="preview-controls-summary-state">{{ exposureMode }}</span>
        </summary>
        <div class="exposure-controls preview-control-content">
          <label>
            exposure mode
            <select v-model="exposureMode" :disabled="cameraControlBusy || !telemetry?.exposure_capabilities?.supported" @change="applyExposureMode">
              <option value="auto">auto</option>
              <option v-if="telemetry?.exposure_capabilities?.manual_supported" value="manual">manual (lock current)</option>
            </select>
            <span v-if="exposureMode === 'manual' && telemetry" class="control-value">
              {{ formatShutter(telemetry.exposure_time_us) }} / {{ formatValue(telemetry.analogue_gain, 'x') }}
            </span>
          </label>
          <label>
            exposure value
            <input
              v-model.number="exposureValue"
              type="range"
              min="-2"
              max="2"
              step="0.25"
              :disabled="cameraControlBusy || !telemetry || exposureMode !== 'auto'"
              @change="applyExposureValue"
            />
            <span class="control-value">EV {{ Number(exposureValue).toFixed(2) }}</span>
          </label>
        </div>
        <p v-if="exposureError" class="inline-error">{{ exposureError }}</p>
      </details>
    </div>
    <div class="dialog-actions live-preview-actions">
      <button v-if="streamState === 'disconnected'" class="action-button secondary" type="button" @click="retryStream">retry</button>
      <button class="action-button secondary" type="button" @click="closeDialog">close</button>
    </div>
  </dialog>
</template>
