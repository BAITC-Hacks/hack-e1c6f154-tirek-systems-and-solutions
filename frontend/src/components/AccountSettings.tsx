import { useState, type FormEvent } from 'react'
import { CircleNotch, ShieldCheck, SignOut, UserCircle } from '@phosphor-icons/react'
import { api } from '../api/client'
import { useSession } from '../auth/SessionProvider'

export default function AccountSettings() {
  const { session, updateSession, logout } = useSession()
  const [current, setCurrent] = useState('')
  const [password, setPassword] = useState('')
  const [confirmation, setConfirmation] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  async function change(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setError('')
    setSuccess('')
    if (password !== confirmation) {
      setError('Новые пароли не совпадают.')
      return
    }
    setBusy(true)
    try {
      updateSession(await api.changePassword(current, password))
      setCurrent('')
      setPassword('')
      setConfirmation('')
      setSuccess('Пароль обновлён. Другие сессии завершены.')
    } catch (cause) {
      setError((cause as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!session.user)
    return (
      <div className="notice warning">
        Авторизация отключена настройкой сервера. Этот режим предназначен только для изолированных
        локальных проверок.
      </div>
    )

  return (
    <div className="settings-grid account-settings">
      <section className="panel settings-panel">
        <div className="panel-heading">
          <h2>Ваш аккаунт</h2>
          <UserCircle size={23} />
        </div>
        <div className="settings-row">
          <span>Имя</span>
          <strong>{session.user.name}</strong>
        </div>
        <div className="settings-row">
          <span>Почта</span>
          <strong>{session.user.email}</strong>
        </div>
        <div className="settings-row">
          <span>Пространство</span>
          <strong>{session.user.workspace_name}</strong>
        </div>
        <div className="account-actions">
          <button
            className="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true)
              setError('')
              try {
                await logout()
              } catch (cause) {
                setError((cause as Error).message)
                setBusy(false)
              }
            }}
          >
            <SignOut size={18} />
            Выйти из аккаунта
          </button>
          <p className="field-hint">Файлы, расчёты и утверждённые решения сохранятся.</p>
        </div>
      </section>
      <section className="panel settings-panel">
        <div className="panel-heading">
          <h2>Безопасность</h2>
          <ShieldCheck size={22} />
        </div>
        <form className="password-change-form" onSubmit={(event) => void change(event)}>
          <label className="field">
            Текущий пароль
            <input
              type="password"
              autoComplete="current-password"
              required
              maxLength={128}
              value={current}
              disabled={busy}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </label>
          <label className="field">
            Новый пароль
            <input
              aria-label="Новый пароль"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={password}
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
            />
            <span className="field-hint">От 12 до 128 символов.</span>
          </label>
          <label className="field">
            Повторите новый пароль
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={128}
              value={confirmation}
              disabled={busy}
              onChange={(e) => setConfirmation(e.target.value)}
            />
          </label>
          {error && (
            <p className="inline-error" role="alert">
              {error}
            </p>
          )}
          {success && (
            <p className="notice" role="status">
              {success}
            </p>
          )}
          <button className="button" type="submit" disabled={busy}>
            {busy && <CircleNotch size={17} className="spin" />}Изменить пароль
          </button>
        </form>
      </section>
    </div>
  )
}
