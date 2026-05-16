// Копирование текста в clipboard с fallback для старых WebView и insecure
// контекстов (iOS<16, HTTP-страница, sandboxed iframe). `navigator.clipboard`
// там либо отсутствует, либо отдаёт rejected Promise — поэтому пробуем
// сначала modern API, потом execCommand через временный textarea.

export function legacyCopy(text: string): boolean {
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

export function copyText(text: string, onSuccess?: () => void): void {
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(
      () => onSuccess?.(),
      () => { if (legacyCopy(text)) onSuccess?.() },
    )
  } else {
    if (legacyCopy(text)) onSuccess?.()
  }
}
