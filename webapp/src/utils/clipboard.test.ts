import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { copyText } from './clipboard'

// `copyText` пробует navigator.clipboard, потом execCommand-fallback. Тут мы
// подменяем оба бэкенда, чтобы прогнать каждую ветку детерминированно (в
// Telegram/iOS WebView clipboard часто либо отсутствует, либо реджектит запись).

const execCommand = vi.fn()

function setClipboard(value: unknown) {
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value })
}

beforeEach(() => {
  execCommand.mockReset()
  // Своя реализация вместо jsdom-стаба (тот логирует «Not implemented»).
  Object.defineProperty(document, 'execCommand', {
    configurable: true,
    writable: true,
    value: execCommand,
  })
})

afterEach(() => {
  vi.restoreAllMocks()
  // Снимаем own-property override, чтобы следующий тест стартовал чисто.
  delete (navigator as { clipboard?: unknown }).clipboard
})

describe('copyText — modern clipboard API', () => {
  it('returns true and fires onSuccess once when writeText resolves', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })
    const onSuccess = vi.fn()

    const ok = await copyText('vless://abc', onSuccess)

    expect(ok).toBe(true)
    expect(writeText).toHaveBeenCalledWith('vless://abc')
    expect(onSuccess).toHaveBeenCalledOnce()
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('falls back to legacyCopy (execCommand) when writeText rejects', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('blocked')) })
    execCommand.mockReturnValue(true)
    const onSuccess = vi.fn()

    const ok = await copyText('vless://abc', onSuccess)

    expect(ok).toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(onSuccess).toHaveBeenCalledOnce()
  })

  it('returns false and skips onSuccess when both backends fail', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('blocked')) })
    execCommand.mockReturnValue(false)
    const onSuccess = vi.fn()

    const ok = await copyText('vless://abc', onSuccess)

    expect(ok).toBe(false)
    expect(onSuccess).not.toHaveBeenCalled()
  })
})

describe('copyText — legacy-only environments', () => {
  it('uses legacyCopy when navigator.clipboard is absent', async () => {
    setClipboard(undefined)
    execCommand.mockReturnValue(true)
    const onSuccess = vi.fn()

    const ok = await copyText('hello', onSuccess)

    expect(ok).toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
    expect(onSuccess).toHaveBeenCalledOnce()
  })
})

describe('copyText — guards', () => {
  it('returns false for empty text without touching any backend', async () => {
    const writeText = vi.fn()
    setClipboard({ writeText })
    const onSuccess = vi.fn()

    const ok = await copyText('', onSuccess)

    expect(ok).toBe(false)
    expect(writeText).not.toHaveBeenCalled()
    expect(execCommand).not.toHaveBeenCalled()
    expect(onSuccess).not.toHaveBeenCalled()
  })
})
