<script setup>
import { ref, watch } from 'vue'
import { ditheredDownloadUrl } from '../api/dashboardApi'
import { forgetImagePreview, isImagePreviewLoaded, markImagePreviewLoaded } from '../composables/photoPreviewCache'

const props = defineProps({
  photo: { type: Object, required: true },
  extensionActions: { type: Array, default: () => [] },
  busyAction: { type: String, default: '' },
})

defineEmits(['select', 'display', 'extension'])

const imageLoading = ref(true)
const imageError = ref(false)
const imageKey = ref(0)

function actionKey(action) {
  return `${action.id}:${props.photo.id}`
}

function imageSource(photo) {
  const path = photo.dithered_path || photo.original_path
  if (!path || !photo.dithered_path || !photo.dithered_updated_at) return path
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}v=${photo.dithered_updated_at}`
}

function handleImageLoad() {
  markImagePreviewLoaded(imageSource(props.photo))
  imageLoading.value = false
  imageError.value = false
}

function handleImageError() {
  forgetImagePreview(imageSource(props.photo))
  imageLoading.value = false
  imageError.value = true
}

function retryImage() {
  imageLoading.value = !isImagePreviewLoaded(imageSource(props.photo))
  imageError.value = false
  imageKey.value += 1
}

watch(() => imageSource(props.photo), (source) => {
  imageLoading.value = Boolean(source) && !isImagePreviewLoaded(source)
  imageError.value = !source
  imageKey.value += 1
}, { immediate: true })
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
      <div v-if="imageLoading && !imageError" class="image-placeholder" aria-label="Loading image">
        <span class="loading-spinner"></span>
      </div>
      <div v-if="imageError" class="image-error" role="status">
        <span>image unavailable</span>
        <button class="pagination-button" type="button" @click.stop="retryImage">retry</button>
      </div>
      <img
        v-if="!imageError && imageSource(photo)"
        :key="imageKey"
        class="photo-image"
        draggable="false"
        :src="imageSource(photo)"
        :alt="`Photo ${photo.id}`"
        :loading="isImagePreviewLoaded(imageSource(photo)) ? 'eager' : 'lazy'"
        @load="handleImageLoad"
        @error="handleImageError"
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
