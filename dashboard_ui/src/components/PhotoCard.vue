<script setup>
import { ditheredDownloadUrl } from '../api/dashboardApi'

const props = defineProps({
  photo: { type: Object, required: true },
  extensionActions: { type: Array, default: () => [] },
  busyAction: { type: String, default: '' },
})

defineEmits(['select', 'display', 'extension'])

function actionKey(action) {
  return `${action.id}:${props.photo.id}`
}

function imageSource(photo) {
  const path = photo.dithered_path || photo.original_path
  if (!path || !photo.dithered_path || !photo.dithered_updated_at) return path
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}v=${photo.dithered_updated_at}`
}
</script>

<template>
  <article
    class="photo-card"
    :class="{ 'carousel-enabled': photo.carousel_enabled }"
    tabindex="0"
    role="button"
    :aria-label="`Preview photo ${photo.id}`"
    @click="$emit('select', photo)"
    @keydown.enter="$emit('select', photo)"
    @keydown.space.prevent="$emit('select', photo)"
  >
    <div class="photo-frame">
      <img
        class="photo-image"
        :src="imageSource(photo)"
        :alt="`Photo ${photo.id}`"
        loading="lazy"
      />
    </div>
    <div class="photo-info">
      <p class="photo-name">{{ photo.filename || photo.id }}</p>
      <div class="photo-actions" @click.stop>
        <a class="action-button primary" :href="photo.original_path" download>original</a>
        <a
          v-if="photo.has_dithered"
          class="action-button secondary"
          :href="ditheredDownloadUrl(photo)"
          download
        >
          dithered
        </a>
        <button class="action-button success" type="button" :disabled="busyAction === `display:${photo.id}`" @click="$emit('display', photo)">
          {{ busyAction === `display:${photo.id}` ? 'sending...' : 'display' }}
        </button>
        <button
          v-for="action in extensionActions.filter((item) => !item.requires_dithered || photo.has_dithered)"
          :key="actionKey(action)"
          class="action-button secondary"
          type="button"
          :disabled="busyAction === actionKey(action)"
          @click="$emit('extension', action, photo)"
        >
          {{ busyAction === actionKey(action) ? 'working...' : action.action_label }}
        </button>
      </div>
    </div>
  </article>
</template>
