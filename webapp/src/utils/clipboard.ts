// Копирование текста в clipboard с fallback для старых WebView и insecure
// контекстов (iOS<16, HTTP-страница, sandboxed iframe). `navigator.clipboard`
// там либо отсутствует, либо отдаёт rejected Promise — поэтому пробуем
// сначала modern API, потом execCommand через временный textarea.

export function legacyCopy(text: string): boolean {
  const selection = document.getSelection()
  const previousRange = selection && selection.rangeCount > 0
    ? selection.getRangeAt(0).cloneRange()
    : null

  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.top = '0'
    ta.style.left = '-9999px'
    ta.style.width = '1px'
    ta.style.height = '1px'
    ta.style.opacity = '0'
    ta.style.pointerEvents = 'none'
    ta.style.userSelect = 'text'
    ta.setAttribute('readonly', '')
    ta.setAttribute('aria-hidden', 'true')
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, text.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  } finally {
    if (selection) {
      selection.removeAllRanges()
      if (previousRange) selection.addRange(previousRange)
    }
  }
}

export async function copyText(text: string, onSuccess?: () => void): Promise<boolean> {
  if (!text) return false

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      onSuccess?.()
      return true
    }
  } catch {
    // Telegram/iOS WebView may expose navigator.clipboard but reject writes.
  }

  const ok = legacyCopy(text)
  if (ok) onSuccess?.()
  return ok
}
