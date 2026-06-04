import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { SubscriptionUrlCard } from './SubscriptionUrlCard'

// vi.mock-фабрики хоистятся выше объявлений — переменные через vi.hoisted.
const { mockCopyText, mockImpact, mockNotify } = vi.hoisted(() => ({
  mockCopyText: vi.fn(),
  mockImpact:   vi.fn(),
  mockNotify:   vi.fn(),
}))

vi.mock('../utils/clipboard', () => ({
  copyText: mockCopyText,
}))

vi.mock('@twa-dev/sdk', () => ({
  default: {
    HapticFeedback: {
      impactOccurred:       mockImpact,
      notificationOccurred: mockNotify,
    },
  },
}))

// Без LanguageProvider useT() отдаёт RU (дефолт LangCtx) — ассертим рус. строки.
const SUB = 'vless://860989d4@frankfurt.example:443?type=tcp#Frankfurt'

beforeEach(() => {
  vi.clearAllMocks()
})

describe('SubscriptionUrlCard — успешное копирование', () => {
  it('копирует ссылку, показывает «Скопировано» и не открывает ручной лист', async () => {
    mockCopyText.mockImplementation(async (_text: string, onSuccess?: () => void) => {
      onSuccess?.()
      return true
    })
    render(<SubscriptionUrlCard subUrl={SUB} />)

    await userEvent.click(screen.getByText('Копировать'))

    expect(mockCopyText).toHaveBeenCalledWith(SUB, expect.any(Function))
    expect(mockImpact).toHaveBeenCalledWith('light')
    expect(await screen.findByText('Скопировано')).toBeInTheDocument()
    // ручной лист НЕ открыт, warning-haptic НЕ дёрнут
    expect(screen.queryByText('Скопируй ссылку вручную')).not.toBeInTheDocument()
    expect(mockNotify).not.toHaveBeenCalled()
  })
})

describe('SubscriptionUrlCard — провал копирования', () => {
  beforeEach(() => {
    mockCopyText.mockResolvedValue(false)
  })

  it('открывает ручной лист с подписочной ссылкой и шлёт warning-haptic', async () => {
    render(<SubscriptionUrlCard subUrl={SUB} />)

    await userEvent.click(screen.getByText('Копировать'))

    expect(await screen.findByText('Скопируй ссылку вручную')).toBeInTheDocument()
    // textarea содержит именно подписочную ссылку (юзер копирует её руками)
    expect(screen.getByDisplayValue(SUB)).toBeInTheDocument()
    expect(mockNotify).toHaveBeenCalledWith('warning')
    // основная кнопка НЕ ушла в состояние «Скопировано»
    expect(screen.queryByText('Скопировано')).not.toBeInTheDocument()
  })

  it('закрывается по кнопке «Закрыть»', async () => {
    render(<SubscriptionUrlCard subUrl={SUB} />)
    await userEvent.click(screen.getByText('Копировать'))
    await screen.findByText('Скопируй ссылку вручную')

    await userEvent.click(screen.getByText('Закрыть'))

    await waitFor(() =>
      expect(screen.queryByText('Скопируй ссылку вручную')).not.toBeInTheDocument(),
    )
  })

  it('закрывается по клику на оверлей', async () => {
    render(<SubscriptionUrlCard subUrl={SUB} />)
    await userEvent.click(screen.getByText('Копировать'))
    await screen.findByText('Скопируй ссылку вручную')

    await userEvent.click(screen.getByTestId('sub-url-manual-backdrop'))

    await waitFor(() =>
      expect(screen.queryByText('Скопируй ссылку вручную')).not.toBeInTheDocument(),
    )
  })

  it('авто-фокусит и выделяет textarea при открытии листа', async () => {
    render(<SubscriptionUrlCard subUrl={SUB} />)
    await userEvent.click(screen.getByText('Копировать'))

    const ta = (await screen.findByDisplayValue(SUB)) as HTMLTextAreaElement
    // эффект фокусит/выделяет через setTimeout(50) — ждём через waitFor
    await waitFor(() => {
      expect(ta).toHaveFocus()
      expect(ta.selectionStart).toBe(0)
      expect(ta.selectionEnd).toBe(SUB.length)
    })
  })

  it('кнопка «Выделить» возвращает фокус и выделение на textarea', async () => {
    render(<SubscriptionUrlCard subUrl={SUB} />)
    await userEvent.click(screen.getByText('Копировать'))

    const ta = (await screen.findByDisplayValue(SUB)) as HTMLTextAreaElement
    // Сначала дождёмся авто-фокуса от эффекта (его one-shot таймер отыграет),
    // затем сбросим — иначе таймер мог бы повторно сфокусировать после blur.
    await waitFor(() => expect(ta).toHaveFocus())
    ta.blur()
    ta.setSelectionRange(0, 0)

    await userEvent.click(screen.getByText('Выделить'))

    expect(ta).toHaveFocus()
    expect(ta.selectionStart).toBe(0)
    expect(ta.selectionEnd).toBe(SUB.length)
  })
})
