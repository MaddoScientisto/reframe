export async function downloadUrl(url, filename) {
  const response = await fetch(url)
  if (!response.ok) throw new Error(`Could not download ${filename || 'file'}`)
  return downloadBlob(await response.blob(), filename)
}

export function downloadBlob(blob, filename) {
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = filename || 'download'
  link.style.display = 'none'
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000)
}