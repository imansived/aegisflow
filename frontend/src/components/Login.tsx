import { useState } from 'react'
import { Api, ApiError } from '../api'

export function Login({ onLogin }: { onLogin: (token: string, role: 'admin' | 'viewer') => void }) {
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const me = await new Api(token.trim()).me()
      onLogin(token.trim(), me.role)
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? 'That token was not accepted.' : err instanceof ApiError && err.status === 429 ? 'Too many attempts. Wait a moment and try again.' : 'Could not reach the server. Is it running?')
    } finally { setBusy(false) }
  }

  return (
    <main className="login">
      <div className="login-shell">
        <form className="login-card" onSubmit={submit}>
          <span className="eyebrow">AEGISFLOW</span>
          <h1>Sign in</h1>
          <p className="sub">Behavioural anomaly detection for connected devices. It watches location, network, device health and proximity, and speaks up when several of them change together. Enter your access token: the viewer token to watch, the admin token to also run the simulation.</p>
          <label className="field">Access token
            <input type="password" autoComplete="off" autoFocus value={token} onChange={(e) => setToken(e.target.value)} aria-invalid={!!error} />
          </label>
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn primary" disabled={busy || token.trim().length < 8} type="submit">{busy ? 'Checking…' : 'Sign in'}</button>
        </form>
        <div className="login-art" aria-hidden="true">
          <div className="dots" />
          <i className="streak a" />
          <i className="streak b" />
          <i className="orb small" />
          <i className="orb" />
          <div className="wordmark">AEGISFLOW</div>
        </div>
      </div>
    </main>
  )
}
