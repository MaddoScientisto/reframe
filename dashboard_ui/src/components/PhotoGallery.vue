<script setup>
import PhotoCard from './PhotoCard.vue'
import Pagination from './Pagination.vue'

defineProps({
  photos: { type: Array, default: () => [] },
  pages: { type: Array, default: () => [] },
  pagination: { type: Object, required: true },
  currentPage: { type: Number, required: true },
  pageSize: { type: Number, required: true },
  pageSizeOptions: { type: Array, default: () => [] },
  loading: { type: Boolean, default: false },
  fetching: { type: Boolean, default: false },
  fetchingNextPage: { type: Boolean, default: false },
  pageJumpBusy: { type: Boolean, default: false },
  error: { type: String, default: '' },
  extensionActions: { type: Array, default: () => [] },
  busyAction: { type: String, default: '' },
  setSentinel: { type: Function, required: true },
})

defineEmits(['select', 'display', 'extension', 'change-page', 'change-page-size', 'retry'])
</script>

<template>
  <section class="gallery" aria-live="polite">
    <Pagination
      placement="top"
      :pagination="pagination"
      :current-page="currentPage"
      :page-size="pageSize"
      :page-size-options="pageSizeOptions"
      :loading="pageJumpBusy"
      @change="$emit('change-page', $event)"
      @change-page-size="$emit('change-page-size', $event)"
    />
    <div v-if="loading && !photos.length" class="photo-grid skeleton-grid" aria-label="Loading photos">
      <div v-for="index in Math.min(pageSize, 12)" :key="index" class="photo-card skeleton-card" aria-hidden="true">
        <div class="photo-frame skeleton-frame"></div>
        <div class="photo-info"><div class="skeleton-line"></div><div class="skeleton-actions"></div></div>
      </div>
    </div>
    <div v-else-if="error && !photos.length" class="gallery-message error-message">
      <div>
        <p>{{ error }}</p>
        <button class="pagination-button" type="button" @click="$emit('retry')">retry</button>
      </div>
    </div>
    <div v-else-if="!photos.length" class="gallery-message">no photos found. capture your first photo!</div>
    <div v-else class="gallery-pages">
      <div v-if="error && photos.length" class="gallery-service-error" role="alert">
        <p>{{ error }}</p>
        <button class="pagination-button" type="button" @click="$emit('retry')">retry connection</button>
      </div>
      <div v-if="fetching" class="gallery-fetching" role="status">refreshing gallery...</div>
      <section
        v-for="page in pages"
        :key="page.pageKey"
        class="gallery-page"
        :data-page-marker="page.page"
        :aria-label="`Photo page ${page.page}`"
      >
        <div class="photo-grid">
          <PhotoCard
            v-for="photo in page.photos"
            :key="photo.entityKey"
            :photo="photo"
            :extension-actions="extensionActions"
            :busy-action="busyAction"
            @select="$emit('select', $event)"
            @display="$emit('display', $event)"
            @extension="(action, photo) => $emit('extension', action, photo)"
          />
        </div>
      </section>
      <div :ref="setSentinel" class="gallery-sentinel" aria-live="polite">
        <span v-if="fetchingNextPage" class="loading-spinner" aria-label="Loading more photos"></span>
        <span v-if="fetchingNextPage">loading more photos...</span>
      </div>
    </div>
    <Pagination
      placement="bottom"
      :pagination="pagination"
      :current-page="currentPage"
      :page-size="pageSize"
      :page-size-options="pageSizeOptions"
      :loading="pageJumpBusy"
      @change="$emit('change-page', $event)"
      @change-page-size="$emit('change-page-size', $event)"
    />
  </section>
</template>
