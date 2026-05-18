import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import TrialSuccessSheet from './TrialSuccessSheet'

// ── Mocks ──────────────────────────────────────────────────────────────────
// vi.mock factories хоистятся до объявлений — нужен vi.hoisted для переменных.

const { mockNavigate, mockOpenLink, mockHaptic } = vi.hoisted(() => ({
  mockNavigate: vi.fn(),
  mockOpenLink: vi.fn(),
  mockHaptic:   vi.fn(),
}))

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom')
  return { ...actual, useNavigate: () => mockNavigate }
})

vi.mock('@twa-dev/sdk', () => ({
  default: {
    openLink: mockOpenLink,
    HapticFeedback: { impactOccurred: mockHaptic },
  },
}))

// ── Helpers ────────────────────────────────────────────────────────────────

function renderSheet(props: { onClose?: () => void; days?: number } = {}) {
  const onClose = props.onClose ?? vi.fn()
  render(
    <MemoryRouter>
      <TrialSuccessSheet onClose={onClose} days={props.days} />
    </MemoryRouter>
  )
  return { onClose }
}

// ── Tests ──────────────────────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
})

describe('TrialSuccessSheet — рендер', () => {
  it('показывает заголовок', () => {
    renderSheet()
    expect(screen.getByText('Триал активирован!')).toBeInTheDocument()
  })

  it('показывает дефолтное количество дней (3)', () => {
    renderSheet()
    expect(screen.getByText(/3 дня бесплатно/)).toBeInTheDocument()
  })

  it('показывает переданное количество дней', () => {
    renderSheet({ days: 7 })
    expect(screen.getByText(/7 дня бесплатно/)).toBeInTheDocument()
  })

  it('рендерит все 4 кнопки скачивания', () => {
    renderSheet()
    expect(screen.getByText('🍎 Happ — iOS')).toBeInTheDocument()
    expect(screen.getByText('🤖 Happ — Android')).toBeInTheDocument()
    expect(screen.getByText('🛡 AmneziaWG — iOS')).toBeInTheDocument()
    expect(screen.getByText('🤖 AmneziaWG — Android')).toBeInTheDocument()
  })

  it('рендерит кнопку конфигов', () => {
    renderSheet()
    expect(screen.getByText('Мои конфиги')).toBeInTheDocument()
  })

  it('рендерит кнопку «Разберусь позже»', () => {
    renderSheet()
    expect(screen.getByText('Разберусь позже')).toBeInTheDocument()
  })
})

describe('TrialSuccessSheet — закрытие', () => {
  it('клик по оверлею вызывает onClose', async () => {
    const { onClose } = renderSheet()
    // Первый div — backdrop (fixed inset-0)
    const backdrop = document.querySelector('.fixed.inset-0') as HTMLElement
    await userEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledOnce()
  })

  it('«Разберусь позже» вызывает onClose', async () => {
    const { onClose } = renderSheet()
    await userEvent.click(screen.getByText('Разберусь позже'))
    expect(onClose).toHaveBeenCalledOnce()
  })
})

describe('TrialSuccessSheet — навигация в конфиги', () => {
  it('клик «Мои конфиги» вызывает onClose и навигирует в /configs', async () => {
    const { onClose } = renderSheet()
    await userEvent.click(screen.getByText('Мои конфиги'))
    expect(onClose).toHaveBeenCalledOnce()
    expect(mockNavigate).toHaveBeenCalledWith('/configs')
  })
})

describe('TrialSuccessSheet — ссылки на приложения', () => {
  const cases = [
    { label: '🍎 Happ — iOS',          url: 'https://apps.apple.com/app/happ-proxy-utility/id6504287215' },
    { label: '🤖 Happ — Android',       url: 'https://play.google.com/store/apps/details?id=com.happproxy' },
    { label: '🛡 AmneziaWG — iOS',      url: 'https://apps.apple.com/app/amneziawg/id6478942365' },
    { label: '🤖 AmneziaWG — Android',  url: 'https://play.google.com/store/apps/details?id=org.amnezia.awg' },
  ]

  for (const { label, url } of cases) {
    it(`${label} открывает правильную ссылку`, async () => {
      renderSheet()
      await userEvent.click(screen.getByText(label))
      expect(mockOpenLink).toHaveBeenCalledWith(url)
    })
  }
})
