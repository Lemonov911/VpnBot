import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

// W3 #13: ErrorBoundary — class component, hooks недоступны → useT() звать
// нельзя. Достаём lang напрямую из localStorage (то же место куда пишет
// LanguageProvider в i18n.tsx). Hardcoded RU/EN — две строки на каждый
// label, дешевле чем тянуть весь T-объект сюда (boundary упал — значит
// что-то в app сломалось, чем меньше зависимостей тем лучше).
function getLang(): 'ru' | 'en' {
  try {
    const v = localStorage.getItem('lang')
    if (v === 'en' || v === 'ru') return v
  } catch { /* private mode / sandboxed iframe */ }
  // Fallback на Telegram language_code если localStorage пуст (юзер открыл
  // app первый раз и упал до того как LanguageProvider записал ключ).
  try {
    const tg = (window as unknown as { Telegram?: { WebApp?: { initDataUnsafe?: { user?: { language_code?: string } } } } }).Telegram
    const code = tg?.WebApp?.initDataUnsafe?.user?.language_code
    if (code && code.startsWith('en')) return 'en'
  } catch { /* noop */ }
  return 'ru'
}

const TEXTS = {
  ru: {
    title: 'Что-то сломалось',
    sub: 'Страница упала с ошибкой. Попробуй перезагрузить — обычно помогает.',
    retry: 'Попробовать снова',
    unknown: 'Unknown error',
  },
  en: {
    title: 'Something went wrong',
    sub: 'The page crashed. Try reloading — it usually fixes it.',
    retry: 'Try again',
    unknown: 'Unknown error',
  },
} as const

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
      // W3 #5: ошибка-палитра идёт через --color-danger (определён в @theme
      // index.css), раньше тут был literal rgba(255,59,48,…) который не
      // следовал теме при будущей перекраске danger-цвета.
      const txt = TEXTS[getLang()]
      return (
        <div style={{
          minHeight: '100dvh',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          padding: '32px 24px',
          background: 'var(--tg-theme-bg-color, #fff)',
        }}>
          {/* Иконка */}
          <div style={{
            width: 80, height: 80, borderRadius: 24,
            background: 'color-mix(in srgb, var(--color-danger, #ff3b30) 10%, transparent)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: 36, marginBottom: 20,
          }}>
            🔌
          </div>

          {/* Заголовок */}
          <div style={{
            fontWeight: 800, fontSize: 22, marginBottom: 8,
            color: 'var(--tg-theme-text-color, #000)',
            letterSpacing: '-0.3px',
          }}>
            {txt.title}
          </div>

          {/* Описание */}
          <div style={{
            fontSize: 14, lineHeight: 1.5, marginBottom: 8,
            color: 'var(--tg-theme-hint-color, #707579)',
            textAlign: 'center', maxWidth: 280,
          }}>
            {txt.sub}
          </div>

          {/* Код ошибки */}
          <div style={{
            fontSize: 11, fontFamily: 'monospace',
            color: 'color-mix(in srgb, var(--color-danger, #ff3b30) 70%, transparent)',
            background: 'color-mix(in srgb, var(--color-danger, #ff3b30) 7%, transparent)',
            padding: '6px 12px', borderRadius: 8,
            marginBottom: 28, maxWidth: 300,
            wordBreak: 'break-all', textAlign: 'center',
          }}>
            {this.state.error.message || txt.unknown}
          </div>

          {/* Кнопка */}
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              width: '100%', maxWidth: 280,
              padding: '14px 24px', borderRadius: 14, border: 'none',
              background: 'var(--tg-theme-button-color, #2481cc)',
              color: 'var(--tg-theme-button-text-color, #fff)',
              fontWeight: 700, fontSize: 15, cursor: 'pointer',
              letterSpacing: '-0.1px',
            }}
          >
            {txt.retry}
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
