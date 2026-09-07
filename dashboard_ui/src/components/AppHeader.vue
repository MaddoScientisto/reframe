<script setup>
defineProps({
  batteryLevel: { type: Number, default: null },
  photoCount: { type: Number, default: 0 },
  captureBusy: { type: Boolean, default: false },
  carouselActive: { type: Boolean, default: false },
  carouselBusy: { type: Boolean, default: false },
})

defineEmits(['refresh', 'capture', 'live-preview', 'settings', 'toggle-carousel'])
</script>

<template>
  <header class="app-header">
    <div class="brand-block">
      <p class="eyebrow">reframe.camera</p>
      <h1>dashboard</h1>
    </div>
    <div class="header-actions">
      <div class="status-strip" aria-live="polite">
        <span class="status-dot" aria-hidden="true"></span>
        <span>system online</span>
        <span> battery: {{ batteryLevel === null ? '--%' : `${batteryLevel}%` }}</span>
        <span>{{ photoCount }} photos</span>
      </div>
      <div class="button-row">
        <button class="button button-light" type="button" @click="$emit('refresh')">refresh</button>
        <button class="button button-light" type="button" :disabled="carouselBusy" @click="$emit('toggle-carousel')">
          {{ carouselBusy ? 'updating...' : carouselActive ? 'stop carousel' : 'start carousel' }}
        </button>
        <button class="button button-light" type="button" @click="$emit('live-preview')">live preview</button>
        <button class="button" type="button" :disabled="captureBusy" @click="$emit('capture')">
          {{ captureBusy ? 'capturing...' : 'capture photo' }}
        </button>
        <button class="button" type="button" @click="$emit('settings')">settings</button>
      </div>
    </div>
  </header>
</template>
