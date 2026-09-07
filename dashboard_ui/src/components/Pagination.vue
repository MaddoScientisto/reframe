<script setup>
import { computed } from 'vue'

const props = defineProps({
  pagination: { type: Object, required: true },
  currentPage: { type: Number, required: true },
})

defineEmits(['change'])

const visiblePages = computed(() => {
  const totalPages = Math.max(1, props.pagination.total_pages || 1)
  const maxVisiblePages = 5
  let start = Math.max(1, props.currentPage - Math.floor(maxVisiblePages / 2))
  let end = Math.min(totalPages, start + maxVisiblePages - 1)
  if (end - start + 1 < maxVisiblePages) {
    start = Math.max(1, end - maxVisiblePages + 1)
  }

  const pages = []
  if (start > 1) {
    pages.push(1)
    if (start > 2) pages.push('gap-start')
  }
  for (let page = start; page <= end; page += 1) pages.push(page)
  if (end < totalPages) {
    if (end < totalPages - 1) pages.push('gap-end')
    pages.push(totalPages)
  }
  return pages
})

const rangeStart = computed(() => ((props.currentPage - 1) * (props.pagination.limit || 12)) + 1)
const rangeEnd = computed(() => Math.min(
  props.currentPage * (props.pagination.limit || 12),
  props.pagination.total_photos || 0,
))
</script>

<template>
  <nav v-if="pagination.total_pages > 1" class="pagination" aria-label="Photo pages">
    <button
      class="pagination-button"
      type="button"
      :disabled="!pagination.has_prev"
      @click="$emit('change', currentPage - 1)"
    >
      previous
    </button>
    <div class="page-numbers">
      <template v-for="page in visiblePages" :key="page">
        <span v-if="String(page).startsWith('gap')" class="pagination-gap">...</span>
        <button
          v-else
          class="pagination-button"
          :class="{ active: page === currentPage }"
          type="button"
          @click="$emit('change', page)"
        >
          {{ page }}
        </button>
      </template>
    </div>
    <span class="pagination-info">{{ rangeStart }}-{{ rangeEnd }} of {{ pagination.total_photos }}</span>
    <form class="page-jump" @submit.prevent="$emit('change', Number($event.target.elements.page.value))">
      <label for="page-jump-input">page</label>
      <input id="page-jump-input" name="page" type="number" min="1" :max="pagination.total_pages" :value="currentPage" />
      <button class="pagination-button" type="submit">go</button>
    </form>
    <button
      class="pagination-button"
      type="button"
      :disabled="!pagination.has_next"
      @click="$emit('change', currentPage + 1)"
    >
      next
    </button>
  </nav>
</template>
