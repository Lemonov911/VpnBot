import { Component, type ReactNode } from 'react'

interface Props { children: ReactNode }
interface State { error: Error | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  render() {
    if (this.state.error) {
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
            background: 'rgba(255,59,48,0.1)',
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
            Что-то сломалось
          </div>

          {/* Описание */}
          <div style={{
            fontSize: 14, lineHeight: 1.5, marginBottom: 8,
            color: 'var(--tg-theme-hint-color, #707579)',
            textAlign: 'center', maxWidth: 280,
          }}>
            Страница упала с ошибкой. Попробуй перезагрузить — обычно помогает.
          </div>

          {/* Код ошибки */}
          <div style={{
            fontSize: 11, fontFamily: 'monospace',
            color: 'rgba(255,59,48,0.7)',
            background: 'rgba(255,59,48,0.07)',
            padding: '6px 12px', borderRadius: 8,
            marginBottom: 28, maxWidth: 300,
            wordBreak: 'break-all', textAlign: 'center',
          }}>
            {this.state.error.message || 'Unknown error'}
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
            Попробовать снова
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
