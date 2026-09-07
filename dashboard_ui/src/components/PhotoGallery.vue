<script setup>
import PhotoCard from './PhotoCard.vue'
import Pagination from './Pagination.vue'

defineProps({
  photos: { type: Array, default: () => [] },
  pagination: { type: Object, required: true },
  currentPage: { type: Number, required: true },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  extensionActions: { type: Array, default: () => [] },
  busyAction: { type: String, default: '' },
})

defineEmits(['select', 'display', 'extension', 'change-page'])
</script>

<template>
  <section class="gallery" aria-live="polite">
    <div v-if="loading && !photos.length" class="gallery-message">loading photos...</div>
    <div v-else-if="error && !photos.length" class="gallery-message error-message">{{ error }}</div>
    <div v-else-if="!photos.length" class="gallery-message">no photos found. capture your first photo!</div>
    <div v-else class="photo-grid">
      <PhotoCard
        v-for="photo in photos"
        :key="photo.id"
        :photo="photo"
        :extension-actions="extensionActions"
        :busy-action="busyAction"
        @select="$emit('select', $event)"
        @display="$emit('display', $event)"
        @extension="(action, photo) => $emit('extension', action, photo)"
      />
    </div>
    <p v-if="error && photos.length" class="inline-error">{{ error }}</p>
    <Pagination
      :pagination="pagination"
      :current-page="currentPage"
      @change="$emit('change-page', $event)"
    />
  </section>
</template>
