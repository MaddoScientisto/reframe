import assert from 'node:assert/strict'
import test from 'node:test'
import { galleryQueryKey, normalizePage } from '../src/composables/photoGalleryIdentity.js'

test('gallery query keys isolate page size and view', () => {
  assert.deepEqual(galleryQueryKey(12, false), ['photos', { pageSize: 12, carouselOnly: false }])
  assert.notDeepEqual(galleryQueryKey(12, false), galleryQueryKey(24, false))
  assert.notDeepEqual(galleryQueryKey(12, false), galleryQueryKey(12, true))
  assert.notDeepEqual(galleryQueryKey(12, false, 3), galleryQueryKey(12, false))
  assert.deepEqual(galleryQueryKey(12, false, 3), ['photos', { pageSize: 12, carouselOnly: false, startPage: 3 }])
})

test('normalized pages preserve server page and slot identity', () => {
  const page = normalizePage({
    photos: [{ id: '16b9cc47', filename: 'photo.jpg' }],
    pagination: { page: 3, limit: 12, total_photos: 40, total_pages: 4, has_next: true },
  }, 3, 'view:all:limit:12')

  assert.equal(page.pageKey, 'view:all:limit:12:page:3')
  assert.equal(page.photos[0].entityKey, 'photo:16b9cc47')
  assert.equal(page.photos[0].slotKey, 'view:all:limit:12:page:3:slot:0')
})

test('records without stable ids are rejected', () => {
  assert.throws(() => normalizePage({ photos: [{ filename: 'missing-id.jpg' }] }, 1, 'view:all:limit:12'), /without an id/)
})
