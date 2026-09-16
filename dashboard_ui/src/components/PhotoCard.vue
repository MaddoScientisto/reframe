<script setup>
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { downloadUrl } from '../api/fileDownload'
import { ditheredDownloadUrl } from '../api/dashboardApi'
import { forgetImagePreview, isImagePreviewLoaded, markImagePreviewLoaded } from '../composables/photoPreviewCache'

const props = defineProps({
  photo: { type: Object, required: true },
  extensionActions: { type: Array, default: () => [] },
  busyAction: { type: String, default: '' },
})

const imageLoading = ref(true)
const imageError = ref(false)
const imageKey = ref(0)
const menuOpen = ref(false)
const menuRef = ref(null)
const downloading = ref('')
const downloadError = ref('')

const emit = defineEmits(['select', 'display', 'extension', 'context-action'])

function actionKey(action) {
  return `${action.id}:${props.photo.id}`
}

function busyKey(action) {
  return `${action}:${props.photo.id}`
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

function toggleMenu() {
  if (downloading.value) return
  menuOpen.value = !menuOpen.value
}

function closeMenu() {
  menuOpen.value = false
}

async function downloadPhoto(kind) {
  if (downloading.value) return
  const href = kind === 'original' ? props.photo.original_path : ditheredDownloadUrl(props.photo)
  if (!href || (kind === 'dithered' && !props.photo.has_dithered)) return
  const filename = kind === 'original'
    ? props.photo.filename || `${props.photo.id}.jpg`
    : `${props.photo.id}_dithered.png`
  downloading.value = kind
  downloadError.value = ''
  closeMenu()
  try {
    await downloadUrl(href, filename)
  } catch (error) {
    downloadError.value = error.message || 'Could not download photo'
  } finally {
    downloading.value = ''
  }
}

function handleMenuAction(action) {
  if (action === 'download-original' || action === 'download-dithered') {
    void downloadPhoto(action === 'download-original' ? 'original' : 'dithered')
    return
  }
  closeMenu()
  emit('context-action', { action, photo: props.photo })
}

function handleDocumentPointerDown(event) {
  if (!menuRef.value?.contains(event.target)) closeMenu()
}

watch(() => imageSource(props.photo), (source) => {
  imageLoading.value = Boolean(source) && !isImagePreviewLoaded(source)
  imageError.value = !source
  imageKey.value += 1
}, { immediate: true })

onMounted(() => document.addEventListener('pointerdown', handleDocumentPointerDown))
onUnmounted(() => document.removeEventListener('pointerdown', handleDocumentPointerDown))
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
        :class="{ dithered: Boolean(photo.dithered_path) }"
        draggable="false"
        :src="imageSource(photo)"
        :alt="`Photo ${photo.id}`"
        :style="{ '--photo-rotation': `${Number(photo.rotation || 0) * 90}deg` }"
        :loading="isImagePreviewLoaded(imageSource(photo)) ? 'eager' : 'lazy'"
        @load="handleImageLoad"
        @error="handleImageError"
      />
    </div>
    <div class="photo-info">
      <p class="photo-name">{{ photo.filename || photo.id }}</p>
      <div class="photo-actions" @click.stop>
        <button
          class="action-button primary"
          type="button"
          :disabled="Boolean(downloading)"
          :aria-busy="downloading === 'original'"
          @click="downloadPhoto('original')"
        >
          <span v-if="downloading === 'original'" class="button-spinner" aria-hidden="true"></span>
          {{ downloading === 'original' ? 'downloading...' : 'original' }}
        </button>
        <button
          v-if="photo.has_dithered"
          class="action-button secondary"
          type="button"
          :disabled="Boolean(downloading)"
          :aria-busy="downloading === 'dithered'"
          @click="downloadPhoto('dithered')"
        >
          <span v-if="downloading === 'dithered'" class="button-spinner" aria-hidden="true"></span>
          {{ downloading === 'dithered' ? 'downloading...' : 'dithered' }}
        </button>
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
        <div ref="menuRef" class="photo-action-menu">
          <button
            class="icon-button photo-menu-button"
            type="button"
            aria-label="More photo options"
            aria-haspopup="menu"
            :aria-expanded="menuOpen"
            :disabled="Boolean(downloading)"
            @click="toggleMenu"
          >
            ...
          </button>
          <div v-if="menuOpen" class="photo-context-menu" role="menu">
            <button class="photo-context-item" type="button" role="menuitem" @click="handleMenuAction('preview')">preview</button>
            <button class="photo-context-item" type="button" role="menuitem" :disabled="Boolean(downloading)" @click="handleMenuAction('download-original')">download original</button>
            <button v-if="photo.has_dithered" class="photo-context-item" type="button" role="menuitem" :disabled="Boolean(downloading)" @click="handleMenuAction('download-dithered')">download dithered</button>
            <button class="photo-context-item" type="button" role="menuitem" :disabled="busyAction === busyKey('display')" @click="handleMenuAction('display')">display</button>
            <button class="photo-context-item" type="button" role="menuitem" :disabled="busyAction === busyKey('carousel')" @click="handleMenuAction(photo.carousel_enabled ? 'remove-carousel' : 'add-carousel')">
              {{ photo.carousel_enabled ? 'remove from carousel' : 'add to carousel' }}
            </button>
            <button v-if="photo.has_dithered" class="photo-context-item" type="button" role="menuitem" :disabled="busyAction === busyKey('rotate-left')" @click="handleMenuAction('rotate-left')">rotate left</button>
            <button v-if="photo.has_dithered" class="photo-context-item" type="button" role="menuitem" :disabled="busyAction === busyKey('rotate-right')" @click="handleMenuAction('rotate-right')">rotate right</button>
            <button class="photo-context-item danger" type="button" role="menuitem" :disabled="busyAction === busyKey('delete')" @click="handleMenuAction('delete')">delete</button>
          </div>
        </div>
      </div>
      <p v-if="downloadError" class="photo-download-error" role="status">{{ downloadError }}</p>
    </div>
  </article>
</template>
