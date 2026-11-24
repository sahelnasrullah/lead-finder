import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { DncRow } from '../api'

export function DoNotContactTab() {
  const [rows, setRows] = useState<DncRow[]>([])
  const [email, setEmail] = useState('')
  const [domain, setDomain] = useState('')
  const [reason, setReason] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(() => {
    api
      .listDnc()
      .then(setRows)
      .catch((e: Error) => setError(e.message))
  }, [])
  useEffect(load, [load])

  async function onAdd(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api.addDnc({ email: email.trim(), domain: domain.trim(), reason: reason.trim() })
      setEmail('')
      setDomain('')
      setReason('')
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function onRemove(id: number) {
    try {
      await api.removeDnc(id)
      load()
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <section>
      <h2>Do not contact</h2>
      <p className="muted">
        Anyone who replies “unsubscribe” or asks not to be contacted goes here. These addresses and domains are never
        emailed, even if a campaign was created earlier.
      </p>
      <form className="card form-grid" onSubmit={onAdd}>
        <label>
          Email address
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="info@firm.se" />
        </label>
        <label>
          …or whole domain
          <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="firm.se" />
        </label>
        <label>
          Reason (optional)
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="form-actions">
          <button className="primary" type="submit" disabled={!email.trim() && !domain.trim()}>
            Add
          </button>
        </div>
      </form>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      <table>
        <thead>
          <tr>
            <th>Email</th>
            <th>Domain</th>
            <th>Reason</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>{r.email || '–'}</td>
              <td>{r.domain || '–'}</td>
              <td>{r.reason}</td>
              <td>
                <button onClick={() => onRemove(r.id)}>Remove</button>
              </td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={4} className="muted">
                Nobody is blocked yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
