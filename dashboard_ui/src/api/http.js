export async function request(path, options = {}) {
  const response = await fetch(path, options)
  let data = null

  try {
    data = await response.json()
  } catch {
    data = null
  }

  if (!response.ok) {
    const detail = data?.detail || data?.message || `Request failed (${response.status})`
    const error = new Error(detail)
    error.status = response.status
    error.data = data
    throw error
  }

  return data
}

export function jsonRequest(path, method, body) {
  return request(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function getPhotoFilename(path) {
  return path ? path.split('/').pop() : ''
}
