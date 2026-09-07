<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  settings: { type: Object, default: null },
  saving: { type: Boolean, default: false },
  updateState: { type: Object, default: () => ({}) },
  displayState: { type: String, default: '' },
  downloadState: { type: Object, default: () => ({}) },
  deleteState: { type: Object, default: () => ({}) },
})

const emit = defineEmits([
  'close',
  'save',
  'check-updates',
  'install-update',
  'show-qr',
  'display-control',
  'download-all',
  'abort-download',
  'delete-all',
  'abort-delete',
])

function defaultSettings() {
  return {
    camera: {
      resolution: { width: 1200, height: 800 },
      exposure_value: 0,
      sharpness: 3,
      autofocus_mode: 2,
    },
    processing: {
      saturation: 0.6,
      brightness_factor: 1.1,
      color_factor: 1.4,
      dithering_method: 'floyd_steinberg',
      bayer_size: 4,
      threshold_scale: 1,
      tone_map: 'percentile',
      gb_color_palette: 'blue_yellow',
    },
    display: {
      auto_display: true,
      display_timeout: 0,
      interrupt_refresh_on_capture: false,
      refresh_interrupt_action: 'reset',
    },
    system: {
      auto_refresh_interval: 30,
      auto_timeout_minutes: 10,
      auto_timeout_enabled: true,
      show_dashboard_qr_on_wifi_connect: true,
      camera_name: '',
    },
    exports: { upscale_dithered_2x: false },
    extensions: {
      arena: { enabled: false, channel: '', access_token: '' },
    },
  }
}

function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

function mergeSettings(base, override) {
  const merged = clone(base)
  for (const [key, value] of Object.entries(override || {})) {
    if (value && typeof value === 'object' && !Array.isArray(value) && merged[key]) {
      merged[key] = mergeSettings(merged[key], value)
    } else {
      merged[key] = value
    }
  }
  return merged
}

const draft = ref(defaultSettings())
const initialSnapshot = ref('')
const arenaTokenShouldClear = ref(false)

const method = computed(() => draft.value.processing.dithering_method)
const showBayerSettings = computed(() => ['ordered', 'bayer_natural_pair'].includes(method.value))
const showThresholdSettings = computed(() => [
  'ordered',
  'bayer_natural_pair',
  'gb-default',
  'gb-default-color',
].includes(method.value))
const showToneMap = computed(() => method.value === 'bayer_natural_pair')
const showGameBoyPalette = computed(() => method.value === 'gb-default-color')
const dirty = computed(() => initialSnapshot.value !== currentSnapshot())
const downloadPercent = computed(() => {
  const job = props.downloadState || {}
  return job.total ? Math.round((job.processed / job.total) * 100) : 0
})
const deletePercent = computed(() => {
  const job = props.deleteState || {}
  return job.total ? Math.round((job.processed / job.total) * 100) : 0
})

function currentSnapshot() {
  return JSON.stringify({ draft: draft.value, arenaTokenShouldClear: arenaTokenShouldClear.value })
}

function loadSettings() {
  const loaded = mergeSettings(defaultSettings(), props.settings || {})
  loaded.extensions.arena.access_token = ''
  draft.value = loaded
  arenaTokenShouldClear.value = false
  initialSnapshot.value = currentSnapshot()
}

watch(() => props.open, (open) => {
  if (open) loadSettings()
})

watch(() => props.settings, (settings) => {
  if (props.open && settings) loadSettings()
}, { deep: true })

function preventLeavingWithUnsavedSettings(event) {
  if (!props.open || !dirty.value) return
  event.preventDefault()
  event.returnValue = ''
}

onMounted(() => window.addEventListener('beforeunload', preventLeavingWithUnsavedSettings))
onBeforeUnmount(() => window.removeEventListener('beforeunload', preventLeavingWithUnsavedSettings))

function close() {
  if (dirty.value && !window.confirm('You have unsaved settings. Close without saving?')) return
  emit('close')
}

function save() {
  const payload = clone(draft.value)
  payload.extensions.arena.access_token_clear = arenaTokenShouldClear.value
  emit('save', payload)
}

function resetSettings() {
  if (!window.confirm('Are you sure you want to reset all settings to defaults?')) return
  draft.value = defaultSettings()
  arenaTokenShouldClear.value = true
}

function clearArenaToken() {
  arenaTokenShouldClear.value = true
  draft.value.extensions.arena.access_token = ''
}

function formatJobMessage(job, percent) {
  if (!job || job.status === 'idle') return ''
  if (job.status === 'running' || job.status === 'starting') return `${job.message || 'working...'} ${percent}%`
  return job.message || ''
}
</script>

<template>
  <div v-if="open" class="modal-backdrop" @click.self="close">
    <section class="settings-modal" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <div class="settings-header">
        <div>
          <p class="eyebrow">configuration</p>
          <h2 id="settings-title">settings</h2>
        </div>
        <div class="button-row">
          <button class="button" type="button" :disabled="saving" @click="save">{{ saving ? 'saving...' : 'save settings' }}</button>
          <button class="button button-light" type="button" @click="close">close</button>
        </div>
      </div>

      <form class="settings-grid" @submit.prevent="save">
        <section class="settings-section">
          <h3>camera settings</h3>
          <label class="setting-row disabled-setting">resolution width <input v-model.number="draft.camera.resolution.width" type="number" min="100" max="4000" disabled /></label>
          <label class="setting-row disabled-setting">resolution height <input v-model.number="draft.camera.resolution.height" type="number" min="100" max="4000" disabled /></label>
          <label class="setting-row">exposure value <input v-model.number="draft.camera.exposure_value" type="number" min="-2" max="2" step="0.25" /></label>
          <label class="setting-row">sharpness <input v-model.number="draft.camera.sharpness" type="number" min="0" max="10" /></label>
          <label class="setting-row">autofocus mode
            <select v-model.number="draft.camera.autofocus_mode">
              <option :value="0">manual</option>
              <option :value="1">auto</option>
              <option :value="2">continuous</option>
            </select>
          </label>
        </section>

        <section class="settings-section">
          <h3>processing settings</h3>
          <label class="setting-row">saturation <input v-model.number="draft.processing.saturation" type="number" min="0" max="2" step="0.1" /></label>
          <p class="setting-help">Blend between muted and saturated six-color palettes.</p>
          <label class="setting-row">brightness factor <input v-model.number="draft.processing.brightness_factor" type="number" min="0.1" max="3" step="0.1" /></label>
          <label class="setting-row">color factor <input v-model.number="draft.processing.color_factor" type="number" min="0.1" max="3" step="0.1" /></label>
          <label class="setting-row">dithering method
            <select v-model="draft.processing.dithering_method">
              <option value="floyd_steinberg">floyd steinberg</option>
              <option value="ordered">ordered (bayer)</option>
              <option value="bayer_natural_pair">bayer natural pair</option>
              <option value="gb-default">game boy default</option>
              <option value="gb-default-color">game boy default (color)</option>
            </select>
          </label>
          <label v-if="showBayerSettings" class="setting-row">bayer matrix size
            <select v-model.number="draft.processing.bayer_size">
              <option :value="2">2x2</option>
              <option :value="4">4x4</option>
              <option :value="8">8x8</option>
            </select>
          </label>
          <label v-if="showThresholdSettings" class="setting-row">threshold scale <input v-model.number="draft.processing.threshold_scale" type="number" min="0.1" max="2" step="0.1" /></label>
          <label v-if="showToneMap" class="setting-row">natural pair tone map
            <select v-model="draft.processing.tone_map">
              <option value="percentile">percentile contrast</option>
              <option value="none">none</option>
            </select>
          </label>
          <label v-if="showGameBoyPalette" class="setting-row">game boy color palette
            <select v-model="draft.processing.gb_color_palette">
              <option value="blue_yellow">black / blue / yellow / white</option>
              <option value="green_yellow">black / green / yellow / white</option>
              <option value="red_yellow">black / red / yellow / white</option>
              <option value="blue_red">black / blue / red / white</option>
              <option value="blue_green">black / blue / green / white</option>
            </select>
          </label>
        </section>

        <section class="settings-section">
          <h3>system settings</h3>
          <label class="setting-row">camera name <input v-model.trim="draft.system.camera_name" type="text" maxlength="80" placeholder="optional" /></label>
          <p class="setting-help">Used in Are.na upload descriptions when set.</p>
          <label class="setting-row">auto refresh interval (seconds) <input v-model.number="draft.system.auto_refresh_interval" type="number" min="5" max="300" /></label>
          <label class="setting-row">auto timeout enabled <select v-model="draft.system.auto_timeout_enabled"><option :value="true">enabled</option><option :value="false">disabled</option></select></label>
          <label class="setting-row">auto timeout duration (minutes) <input v-model.number="draft.system.auto_timeout_minutes" type="number" min="1" max="60" /></label>
          <label class="setting-row">dashboard QR on Wi-Fi connect <select v-model="draft.system.show_dashboard_qr_on_wifi_connect"><option :value="true">enabled</option><option :value="false">disabled</option></select></label>
          <button class="button button-light" type="button" @click="$emit('show-qr')">show dashboard QR</button>
        </section>

        <section class="settings-section">
          <h3>display controls</h3>
          <label class="setting-row">auto display <select v-model="draft.display.auto_display"><option :value="true">enabled</option><option :value="false">disabled</option></select></label>
          <label class="setting-row">display timeout <input v-model.number="draft.display.display_timeout" type="number" min="0" max="3600" /></label>
          <label class="setting-row">interrupt refresh on capture <select v-model="draft.display.interrupt_refresh_on_capture"><option :value="false">disabled</option><option :value="true">enabled</option></select></label>
          <label class="setting-row">capture interrupt action <select v-model="draft.display.refresh_interrupt_action"><option value="reset">force reset</option><option value="stop">force stop</option></select></label>
          <div class="button-row wrap-row">
            <button class="button button-light" type="button" @click="$emit('display-control', 'force-reset')">force reset</button>
            <button class="button danger" type="button" @click="$emit('display-control', 'force-stop')">force stop</button>
            <button class="button button-light" type="button" @click="$emit('display-control', 'redraw')">redraw image</button>
          </div>
          <p class="setting-help" role="status">{{ displayState || 'Manual display controls are experimental.' }}</p>
        </section>

        <section class="settings-section">
          <h3>advanced exports</h3>
          <label class="setting-row">2x dithered downloads and uploads <select v-model="draft.exports.upscale_dithered_2x"><option :value="false">disabled</option><option :value="true">enabled</option></select></label>
          <p class="setting-help">Stored photos and ZIP downloads are unchanged.</p>
        </section>

        <section class="settings-section">
          <h3>software updates</h3>
          <div class="button-row wrap-row">
            <button class="button button-light" type="button" :disabled="updateState.busy" @click="$emit('check-updates')">check for updates</button>
            <button v-if="updateState.can_update" class="button" type="button" :disabled="updateState.busy" @click="$emit('install-update')">install update</button>
          </div>
          <p class="setting-help" role="status">{{ updateState.message || 'Updates code, dependencies, and service files while preserving settings and photos.' }}</p>
        </section>

        <section class="settings-section">
          <h3>extensions</h3>
          <label class="setting-row">are.na upload <select v-model="draft.extensions.arena.enabled"><option :value="false">disabled</option><option :value="true">enabled</option></select></label>
          <label class="setting-row">are.na channel slug or id <input v-model.trim="draft.extensions.arena.channel" type="text" placeholder="my-channel" /></label>
          <label class="setting-row">are.na access token <input v-model.trim="draft.extensions.arena.access_token" type="password" placeholder="leave blank to keep saved token" /></label>
          <p class="setting-help">{{ arenaTokenShouldClear ? 'Token will be cleared when settings are saved.' : settings?.extensions?.arena?.access_token_configured ? 'Token saved. Leave blank to keep it.' : 'No token configured.' }}</p>
          <button class="button danger" type="button" :disabled="!settings?.extensions?.arena?.access_token_configured || arenaTokenShouldClear" @click="clearArenaToken">clear are.na token</button>
        </section>
      </form>

      <footer class="settings-footer">
        <div class="button-row wrap-row">
          <button class="button" type="button" :disabled="saving" @click="save">{{ saving ? 'saving...' : 'save settings' }}</button>
          <button class="button button-light" type="button" @click="resetSettings">reset to defaults</button>
        </div>
        <div class="button-row wrap-row">
          <button class="button button-light" type="button" :disabled="['starting', 'running'].includes(downloadState.status)" @click="$emit('download-all')">
            {{ formatJobMessage(downloadState, downloadPercent) || 'download all photos' }}
          </button>
          <button v-if="['starting', 'running'].includes(downloadState.status)" class="button danger" type="button" @click="$emit('abort-download')">abort download</button>
          <button class="button danger" type="button" :disabled="['starting', 'running'].includes(deleteState.status)" @click="$emit('delete-all')">
            {{ formatJobMessage(deleteState, deletePercent) || 'delete all photos' }}
          </button>
          <button v-if="['starting', 'running'].includes(deleteState.status)" class="button danger" type="button" @click="$emit('abort-delete')">abort deletion</button>
        </div>
      </footer>
    </section>
  </div>
</template>
