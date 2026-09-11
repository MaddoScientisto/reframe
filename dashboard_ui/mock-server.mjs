import http from 'node:http'
import { URL } from 'node:url'

const port = Number(process.env.MOCK_PORT || process.argv[2] || 8090)
const imageBytes = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFElEQVR42mNkYPgPAAEDAQAI/Ac+XgAAAABJRU5ErkJggg==',
  'base64',
)

const photos = Array.from({ length: 37 }, (_, index) => {
  const sequence = String(index + 1).padStart(5, '0')
  const id = (index + 1).toString(16).padStart(8, '0')
  const filename = `${sequence}_20260911_1430${String(index).padStart(2, '0')}_${id}.jpg`
  return {
    id,
    id_kind: 'content_hash',
    legacy_id: null,
    filename,
    original_path: `/photos/${filename}`,
    dithered_path: `/dithered/${id}_dithered.png`,
    has_dithered: index % 6 !== 0,
    dithered_updated_at: 1726065015000000000 + index,
    dithering_method: index % 2 ? 'ordered' : 'floyd_steinberg',
    gb_color_palette: 'blue_yellow',
    carousel_enabled: index % 3 === 0,
    file_size: imageBytes.length,
    created_at: `2026-09-11T14:30:${String(index).padStart(2, '0')}+00:00`,
  }
})
photos.push({
  id: 'legacy-00042',
  id_kind: 'legacy',
  legacy_id: '00042',
  filename: '00042.jpg',
  original_path: '/photos/00042.jpg',
  dithered_path: '/dithered/00042_dithered.png',
  has_dithered: true,
  dithered_updated_at: 1726065015999999999,
  dithering_method: 'floyd_steinberg',
  gb_color_palette: 'blue_yellow',
  carousel_enabled: false,
  file_size: imageBytes.length,
  created_at: '2026-09-10T12:00:00+00:00',
})

let settings = {
  system: { auto_refresh_interval: 30 },
  camera: { resolution: { width: 1200, height: 800 } },
  exports: { upscale_dithered_2x: false },
}
let carouselActive = false

function send(response, status, body, headers = {}) {
  const payload = Buffer.isBuffer(body) ? body : Buffer.from(JSON.stringify(body))
  response.writeHead(status, {
    'Content-Type': Buffer.isBuffer(body) ? 'image/png' : 'application/json',
    'Content-Length': payload.length,
    'Cache-Control': 'no-store',
    ...headers,
  })
  response.end(payload)
}

function readJson(request) {
  return new Promise((resolve, reject) => {
    let body = ''
    request.on('data', (chunk) => { body += chunk })
    request.on('end', () => {
      try { resolve(body ? JSON.parse(body) : {}) } catch (error) { reject(error) }
    })
    request.on('error', reject)
  })
}

function findPhoto(id) {
  return photos.find((photo) => photo.id === id || photo.legacy_id === id)
}

function listPhotos(url) {
  const requestedPage = Math.max(1, Number(url.searchParams.get('page') || 1))
  const requestedLimit = Number(url.searchParams.get('limit') || 12)
  const limit = [12, 24, 48, 100].includes(requestedLimit) ? requestedLimit : 12
  const carouselOnly = url.searchParams.get('carousel_only') === 'true'
  const filtered = carouselOnly ? photos.filter((photo) => photo.carousel_enabled) : photos
  const totalPages = Math.max(1, Math.ceil(filtered.length / limit))
  const page = Math.min(requestedPage, totalPages)
  const start = (page - 1) * limit
  return {
    photos: filtered.slice(start, start + limit),
    pagination: {
      page,
      limit,
      total_photos: filtered.length,
      total_pages: totalPages,
      has_prev: page > 1,
      has_next: page < totalPages,
    },
  }
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url, `http://${request.headers.host}`)
  const method = request.method
  try {
    if (method === 'GET' && url.pathname === '/api/photos') return send(response, 200, listPhotos(url))
    if (method === 'GET' && url.pathname === '/api/photos/download-all/progress') {
      return send(response, 200, { status: 'idle', processed: 0, total: 0, files_added: 0, files_skipped: 0, message: '' })
    }
    if (method === 'POST' && url.pathname === '/api/photos/download-all/start') {
      return send(response, 200, { status: 'completed', processed: photos.length, total: photos.length, message: 'Mock download complete' })
    }
    if (method === 'POST' && url.pathname === '/api/photos/download-all/abort') {
      return send(response, 200, { status: 'aborted', message: 'Mock download aborted' })
    }
    if (method === 'GET' && url.pathname === '/api/photos/delete-all/progress') {
      return send(response, 200, { status: 'idle', processed: 0, total: 0, files_added: 0, files_skipped: 0, message: '' })
    }
    if (method === 'POST' && url.pathname === '/api/photos/delete-all/start') {
      return send(response, 200, { status: 'completed', processed: photos.length, total: photos.length, message: 'Mock deletion complete' })
    }
    if (method === 'POST' && url.pathname === '/api/photos/delete-all/abort') {
      return send(response, 200, { status: 'aborted', message: 'Mock deletion aborted' })
    }
    if (method === 'GET' && url.pathname === '/api/photos/download-all/result') {
      return send(response, 200, Buffer.from('mock zip archive'), {
        'Content-Type': 'application/zip',
        'Content-Disposition': 'attachment; filename="reframe-photos-mock.zip"',
      })
    }
    if (method === 'GET' && url.pathname === '/api/preview/stream') {
      return send(response, 200, imageBytes)
    }
    if (method === 'POST' && url.pathname === '/api/preview/stop') {
      return send(response, 200, { success: true, message: 'Mock preview stopped' })
    }
    if (method === 'GET' && url.pathname === '/api/preview/telemetry') {
      return send(response, 200, {
        success: true,
        frame_sequence: 1,
        frame_age_seconds: 0.05,
        frame_rate: 5,
        focus_mode: 'continuous',
        focus_mode_value: 2,
        focus_state: 'focused',
        focus_state_value: 2,
        lens_position: 1.5,
        focus_range: { min: 0, max: 10, step: 0.1 },
        exposure_mode: 'auto',
        exposure_value: 0,
        exposure_capabilities: { supported: true, manual_supported: true, exposure_time_range: { min: 100, max: 1000000, step: 1 } },
        white_balance_mode: 'auto',
        white_balance_preset: 'daylight',
        colour_gains: { red: 1, blue: 1 },
        white_balance_capabilities: { supported: true, manual_supported: true, preset_supported: true, supported_presets: ['daylight', 'cloudy', 'incandescent', 'fluorescent'] },
      })
    }
    if (method === 'POST' && url.pathname === '/api/preview/focus') {
      const body = await readJson(request)
      return send(response, 200, {
        success: true,
        focus_mode: body.action === 'set_mode' ? body.mode : 'continuous',
        lens_position: body.lens_position ?? 1.5,
        message: 'Mock focus updated',
      })
    }
    if (method === 'POST' && url.pathname === '/api/preview/controls') {
      const body = await readJson(request)
      return send(response, 200, {
        success: true,
        exposure_mode: body.action === 'set_exposure_mode' ? body.mode : 'auto',
        exposure_value: body.exposure_value ?? 0,
        white_balance_mode: body.white_balance_mode || 'auto',
        message: 'Mock preview controls updated',
      })
    }
    if (method === 'GET' && url.pathname.startsWith('/api/photos/')) {
      const photo = findPhoto(decodeURIComponent(url.pathname.split('/')[3]))
      return photo ? send(response, 200, photo) : send(response, 404, { detail: 'Photo not found' })
    }
    if (method === 'DELETE' && url.pathname.startsWith('/api/photos/')) {
      const id = decodeURIComponent(url.pathname.split('/')[3])
      const index = photos.findIndex((photo) => photo.id === id || photo.legacy_id === id)
      if (index < 0) return send(response, 404, { detail: 'Photo not found' })
      photos.splice(index, 1)
      return send(response, 200, { success: true, deleted_files: [] })
    }
    if (method === 'POST' && url.pathname.endsWith('/save')) {
      const id = decodeURIComponent(url.pathname.split('/')[3])
      const photo = findPhoto(id)
      if (!photo) return send(response, 404, { detail: 'Photo not found' })
      const body = await readJson(request)
      photo.dithering_method = body.dithering_method || photo.dithering_method
      photo.has_dithered = true
      photo.dithered_updated_at += 1
      return send(response, 200, { success: true, photo, message: 'Dithered photo saved' })
    }
    if (method === 'POST' && url.pathname === '/api/capture') {
      const id = Math.floor(Date.now() / 1000).toString(16).slice(-8).padStart(8, '0')
      const filename = `00099_20260911_150000_${id}.jpg`
      const photo = {
        id, id_kind: 'content_hash', legacy_id: null, filename,
        original_path: `/photos/${filename}`, dithered_path: `/dithered/${id}_dithered.png`,
        has_dithered: true, dithered_updated_at: Date.now(), dithering_method: 'floyd_steinberg',
        gb_color_palette: 'blue_yellow', carousel_enabled: false, file_size: imageBytes.length,
        created_at: new Date().toISOString(),
      }
      photos.unshift(photo)
      return send(response, 200, { success: true, photo_id: id, filename, message: 'Mock capture complete' })
    }
    if (method === 'POST' && url.pathname.endsWith('/preview')) {
      const id = decodeURIComponent(url.pathname.split('/')[3])
      if (!findPhoto(id)) return send(response, 404, { detail: 'Photo not found' })
      return send(response, 200, {
        png: imageBytes.toString('base64'),
        download_png: imageBytes.toString('base64'),
        message: 'Mock preview generated',
      })
    }
    if (method === 'GET' && url.pathname === '/api/settings') return send(response, 200, settings)
    if (method === 'POST' && url.pathname === '/api/settings') {
      settings = await readJson(request)
      return send(response, 200, { success: true, settings })
    }
    if (method === 'GET' && url.pathname === '/api/extensions/actions') return send(response, 200, { actions: [] })
    if (method === 'GET' && url.pathname === '/api/update/status') return send(response, 200, { busy: false, can_update: false, message: 'Mock updates are disabled' })
    if (method === 'POST' && url.pathname === '/api/update/install') return send(response, 200, { busy: false, can_update: false, message: 'Mock update complete' })
    if (method === 'POST' && url.pathname === '/api/dashboard/qr') return send(response, 200, { success: true, message: 'Mock QR displayed' })
    if (method === 'GET' && url.pathname === '/api/carousel/status') return send(response, 200, { active: carouselActive })
    if (method === 'POST' && ['/api/carousel/start', '/api/carousel/stop'].includes(url.pathname)) {
      carouselActive = url.pathname.endsWith('start')
      return send(response, 200, { active: carouselActive, message: carouselActive ? 'Carousel started' : 'Carousel stopped' })
    }
    if (method === 'POST' && url.pathname.startsWith('/api/carousel/photos/')) {
      const photo = findPhoto(decodeURIComponent(url.pathname.split('/').pop()))
      if (!photo) return send(response, 404, { detail: 'Photo not found' })
      const body = await readJson(request)
      photo.carousel_enabled = Boolean(body.included)
      return send(response, 200, { success: true, included: photo.carousel_enabled })
    }
    if (method === 'GET' && url.pathname === '/api/battery') return send(response, 200, { battery_level: 87 })
    if (method === 'POST' && url.pathname === '/api/timeout/reset') return send(response, 200, { success: true })
    if (method === 'POST' && url.pathname.startsWith('/api/display/')) return send(response, 200, { success: true, message: 'Mock display complete' })
    if (method === 'POST' && url.pathname === '/api/preview/display') return send(response, 200, { success: true, message: 'Mock preview displayed' })
    if (method === 'POST' && url.pathname.startsWith('/api/extensions/')) return send(response, 200, { success: true, message: 'Mock extension complete' })
    if (method === 'GET' && url.pathname.startsWith('/photos/')) return send(response, 200, imageBytes)
    if (method === 'GET' && url.pathname.startsWith('/dithered/')) return send(response, 200, imageBytes)
    if (method === 'GET' && url.pathname.startsWith('/api/download/dithered/')) {
      return send(response, 200, imageBytes, { 'Content-Disposition': 'attachment; filename="mock-download.png"' })
    }
    return send(response, 404, { detail: 'Mock route not found' })
  } catch (error) {
    return send(response, 500, { detail: error.message })
  }
})

server.listen(port, '127.0.0.1', () => {
  console.log(`Mock dashboard API listening at http://127.0.0.1:${port}`)
})
