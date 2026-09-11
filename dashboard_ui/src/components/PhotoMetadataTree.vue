<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  label: { type: String, default: '' },
  value: { default: null },
  depth: { type: Number, default: 0 },
})

const expanded = ref(props.depth < 1)

const normalizedValue = computed(() => {
  if (typeof props.value !== 'string') return props.value
  try {
    return JSON.parse(props.value)
  } catch {
    return props.value
  }
})

const entries = computed(() => {
  if (Array.isArray(normalizedValue.value)) {
    return normalizedValue.value.map((value, index) => ({ key: `[${index}]`, value }))
  }
  if (normalizedValue.value && typeof normalizedValue.value === 'object') {
    return Object.entries(normalizedValue.value).map(([key, value]) => ({ key, value }))
  }
  return []
})

const expandable = computed(() => entries.value.length > 0)

function isBranch(value) {
  return value !== null && typeof value === 'object'
}

function primitiveLabel(value) {
  if (value === null) return 'null'
  if (value === undefined) return 'undefined'
  if (typeof value === 'boolean') return value ? 'true' : 'false'
  return String(value)
}

function toggle() {
  if (expandable.value) expanded.value = !expanded.value
}
</script>

<template>
  <div class="metadata-tree-node" :class="{ branch: expandable }">
    <button
      v-if="expandable"
      class="metadata-tree-toggle"
      type="button"
      :aria-expanded="expanded"
      :aria-label="`${expanded ? 'Collapse' : 'Expand'} ${label || 'metadata'}`"
      @click="toggle"
    >
      {{ expanded ? '-' : '+' }}
    </button>
    <span v-else class="metadata-tree-spacer" aria-hidden="true"></span>
    <span v-if="label" class="metadata-tree-key">{{ label }}</span>
    <span v-if="!expandable" class="metadata-tree-value">{{ primitiveLabel(normalizedValue) }}</span>
    <span v-else class="metadata-tree-count">{{ entries.length }} {{ entries.length === 1 ? 'property' : 'properties' }}</span>
    <div v-if="expandable && expanded" class="metadata-tree-children">
      <PhotoMetadataTree
        v-for="entry in entries"
        :key="entry.key"
        :label="entry.key"
        :value="entry.value"
        :depth="depth + 1"
      />
    </div>
  </div>
</template>
