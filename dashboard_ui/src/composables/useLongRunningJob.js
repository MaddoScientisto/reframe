import { onUnmounted, ref } from 'vue'

export function useLongRunningJob({ start, getProgress, abort, label, maxAttempts = 600 }) {
  const state = ref({
    status: 'idle',
    processed: 0,
    total: 0,
    message: label,
    size_bytes: 0,
  })
  let timer = null
  let attempts = 0

  function clearTimer() {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function setState(next) {
    state.value = { ...state.value, ...next }
  }

  async function poll() {
    try {
      const progress = await getProgress()
      setState(progress)
      if (['completed', 'aborted', 'error', 'idle'].includes(progress.status)) return
      attempts += 1
      if (attempts >= maxAttempts) {
        setState({ status: 'error', message: `${label} timed out` })
        return
      }
      timer = setTimeout(poll, 1000)
    } catch (error) {
      setState({ status: 'error', message: error.message || `${label} failed` })
    }
  }

  async function startJob() {
    clearTimer()
    attempts = 0
    setState({ status: 'starting', processed: 0, total: 0, message: `starting ${label}...` })
    try {
      const result = await start()
      if (result.status === 'completed') {
        setState(result)
        return result
      }
      setState({ ...result, status: 'running', message: result.message || label })
      await poll()
      return result
    } catch (error) {
      setState({ status: 'error', message: error.message || `${label} failed` })
      return null
    }
  }

  async function abortJob() {
    clearTimer()
    try {
      const result = await abort()
      setState(result)
      return result
    } catch (error) {
      setState({ status: 'error', message: error.message || `could not abort ${label}` })
      return null
    }
  }

  function reset() {
    clearTimer()
    setState({ status: 'idle', processed: 0, total: 0, message: label, size_bytes: 0 })
  }

  onUnmounted(clearTimer)

  return { state, startJob, abortJob, reset, clearTimer }
}
