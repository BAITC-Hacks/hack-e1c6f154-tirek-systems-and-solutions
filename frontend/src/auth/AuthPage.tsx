import { useState, type FormEvent } from 'react'
import {
  ArrowRight,
  ChartLineUp,
  CheckCircle,
  CircleNotch,
  Eye,
  EyeSlash,
  LockKey,
  Package,
  ShieldCheck,
} from '@phosphor-icons/react'
import { api, type Session } from '../api/client'

export default function AuthPage({
  onAuthenticated,
  expired,
}: {
  onAuthenticated: (session: Session) => void
  expired: boolean
}) {
  const [registering, setRegistering] = useState(false)
  const [name, setName] = useState('')
  const [workspace, setWorkspace] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [visible, setVisible] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const session = registering
        ? await api.register({
            name: name.trim(),
            workspace_name: workspace.trim(),
            email: email.trim(),
            password,
          })
        : await api.login(email.trim(), password)
      setPassword('')
      onAuthenticated(session)
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className="auth-shell">
      <section className="auth-story" aria-label="О сервисе Tirek">
        <div className="brand auth-brand">
          <div className="brand-mark">
            <span />
            <span />
            <span />
          </div>
          <span>
            tirek<span className="brand-period">.</span>
          </span>
        </div>
        <div className="auth-story-body">
          <span className="auth-eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО ЗАКУПЩИКА</span>
          <h1>
            Каждая закупка. <br />С обоснованием.
          </h1>
          <p>
            Из файлов Excel — в прогноз спроса и проверенный план пополнения. Последнее слово всегда
            за вами.
          </p>
          <ol className="auth-steps">
            <li>
              <Package size={23} />
              <span>
                <strong>Соберите источники</strong>
                <small>Продажи, остатки, поступления и партии</small>
              </span>
            </li>
            <li>
              <ChartLineUp size={23} />
              <span>
                <strong>Проверьте прогноз</strong>
                <small>Метод расчёта, ограничения и причины по SKU</small>
              </span>
            </li>
            <li>
              <CheckCircle size={23} />
              <span>
                <strong>Утвердите решение</strong>
                <small>Корректировка, сохранённый снимок и CSV</small>
              </span>
            </li>
          </ol>
        </div>
        <p className="auth-story-footer">
          <ShieldCheck size={18} />
          Ваши файлы и решения доступны в вашем пространстве.
        </p>
      </section>
      <section className="auth-form-section">
        <div className="auth-card">
          <div className="auth-icon">
            <LockKey size={26} />
          </div>
          <h2>{registering ? 'Создайте своё пространство' : 'С возвращением'}</h2>
          <p>
            {registering
              ? 'Начните с демонстрации или загрузите свои данные после регистрации.'
              : 'Войдите, чтобы продолжить работу с закупками.'}
          </p>
          {expired && (
            <div className="notice warning" role="status">
              Сессия завершилась. Войдите снова, чтобы продолжить.
            </div>
          )}
          <form onSubmit={(event) => void submit(event)}>
            {registering && (
              <>
                <label className="field">
                  Ваше имя
                  <input
                    name="name"
                    autoComplete="name"
                    required
                    maxLength={80}
                    value={name}
                    disabled={busy}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Как к вам обращаться"
                  />
                </label>
                <label className="field">
                  Название компании или команды
                  <input
                    name="workspace"
                    autoComplete="organization"
                    required
                    maxLength={120}
                    value={workspace}
                    disabled={busy}
                    onChange={(e) => setWorkspace(e.target.value)}
                    placeholder="Например, Электрокомплект"
                  />
                </label>
              </>
            )}
            <label className="field">
              Электронная почта
              <input
                name="email"
                type="email"
                autoComplete="username"
                required
                maxLength={254}
                value={email}
                disabled={busy}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.kz"
              />
            </label>
            <label className="field">
              Пароль
              <span className="password-field">
                <input
                  name="password"
                  aria-label="Пароль"
                  type={visible ? 'text' : 'password'}
                  autoComplete={registering ? 'new-password' : 'current-password'}
                  required
                  minLength={registering ? 12 : undefined}
                  maxLength={128}
                  value={password}
                  disabled={busy}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-describedby={registering ? 'password-hint' : undefined}
                />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={visible ? 'Скрыть пароль' : 'Показать пароль'}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? <EyeSlash size={19} /> : <Eye size={19} />}
                </button>
              </span>
              {registering && (
                <span id="password-hint" className="field-hint">
                  От 12 до 128 символов. Подойдёт длинная фраза.
                </span>
              )}
            </label>
            {error && (
              <p className="inline-error" role="alert">
                {error}
              </p>
            )}
            <button className="button button-primary auth-submit" disabled={busy} type="submit">
              {busy ? <CircleNotch size={18} className="spin" /> : <ArrowRight size={18} />}
              {busy ? 'Подождите…' : registering ? 'Создать аккаунт' : 'Войти'}
            </button>
          </form>
          <div className="auth-switch">
            <span>{registering ? 'Уже есть аккаунт?' : 'Первый раз в Tirek?'}</span>
            <button
              type="button"
              className="text-button"
              disabled={busy}
              onClick={() => {
                setRegistering(!registering)
                setError('')
                setPassword('')
              }}
            >
              {registering ? 'Войти в аккаунт' : 'Зарегистрироваться'}
            </button>
          </div>
          <p className="auth-note">
            Каждая регистрация создаёт отдельное рабочее пространство. Приглашение коллег пока не
            поддерживается.
          </p>
        </div>
      </section>
    </main>
  )
}
