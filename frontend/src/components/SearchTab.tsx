import { useEffect, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api'
import type { Meta, Search } from '../api'

interface Props {
  meta: Meta | null
  onViewLeads: (searchId: number) => void
}

export function SearchTab({ meta, onViewLeads }: Props) {
  const [country, setCountry] = useState('SE')
  const [niche, setNiche] = useState('law firms')
  const [city, setCity] = useState('')
  const [limit, setLimit] = useState(30)
  const [search, setSearch] = useState<Search | null>(null)
  const [history, setHistory] = useState<Search[]>([])
  const [error, setError] = useState('')
  const timer = useRef<number | undefined>(undefined)

  const running = search !== null && (search.status === 'pending' || search.status === 'running')
  const countryInfo = meta?.countries.find((c) => c.code === country)

  const loadHistory = () => api.listSearches().then(setHistory).catch(() => undefined)
  useEffect(() => {
    loadHistory()
  }, [])

  const searchId = search?.id
  useEffect(() => {
    if (!running || searchId === undefined) return
    timer.current = window.setInterval(() => {
      api
        .getSearch(searchId)
        .then((s) => {
          setSearch(s)
          if (s.status === 'done' || s.status === 'failed') loadHistory()
        })
        .catch((e: Error) => setError(e.message))
    }, 2000)
    return () => window.clearInterval(timer.current)
  }, [running, searchId])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      setSearch(await api.startSearch({ country, niche: niche.trim(), city: city.trim(), limit }))
    } catch (err) {
      setError((err as Error).message)
    }
  }

  async function onRandom() {
    setError('')
    try {
      const n = await api.randomNiche()
      setNiche(n.label)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <section>
      <h2>Find leads</h2>
      <form className="card form-grid" onSubmit={onSubmit}>
        <label>
          Country
          <select value={country} onChange={(e) => setCountry(e.target.value)}>
            {(meta?.countries ?? [{ code: 'SE', name: 'Sweden' }]).map((c) => (
              <option key={c.code} value={c.code}>
                {c.name} ({c.code})
              </option>
            ))}
          </select>
        </label>

        <label>
          Niche or keyword
          <div className="row">
            <input
              value={niche}
              onChange={(e) => setNiche(e.target.value)}
              list="niche-list"
              required
              minLength={2}
              placeholder="e.g. law firms, accountants, dentists"
            />
            <button type="button" onClick={onRandom} title="Pick a random niche">
              Random
            </button>
          </div>
          <datalist id="niche-list">
            {meta?.niches.map((n) => (
              <option key={n.key} value={n.label} />
            ))}
          </datalist>
        </label>

        <label>
          City or region (optional)
          <input value={city} onChange={(e) => setCity(e.target.value)} placeholder="e.g. Göteborg" />
        </label>

        <label>
          Max results
          <input
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) => setLimit(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
          />
        </label>

        <div className="form-actions">
          <button className="primary" type="submit" disabled={running}>
            {running ? 'Searching…' : 'Search'}
          </button>
        </div>
      </form>

      {countryInfo?.compliance_note && <p className="note">{countryInfo.compliance_note}</p>}
      {!meta?.bolagsverket_configured && country === 'SE' && (
        <p className="note">
          Bolagsverket is not configured, so legal form and sole-trader flags come only from organisation numbers
          found on websites. Add BOLAGSVERKET_CLIENT_ID and BOLAGSVERKET_CLIENT_SECRET to backend/.env.
        </p>
      )}

      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}

      {search && (
        <div className={`card status ${search.status}`} aria-live="polite">
          <strong>
            {search.niche}
            {search.city ? ` in ${search.city}` : ''} ({search.country})
          </strong>
          <p>{search.message || 'Starting…'}</p>
          <p className="muted">Searches can take a minute or two because the free map servers are slow.</p>
          {search.status === 'done' && (
            <button className="primary" onClick={() => onViewLeads(search.id)}>
              View {search.found_count} leads
            </button>
          )}
        </div>
      )}

      {history.length > 0 && (
        <>
          <h3>Recent searches</h3>
          <table>
            <thead>
              <tr>
                <th>Niche</th>
                <th>City</th>
                <th>Country</th>
                <th>Status</th>
                <th>Leads</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {history.map((s) => (
                <tr key={s.id}>
                  <td>{s.niche}</td>
                  <td>{s.city || '–'}</td>
                  <td>{s.country}</td>
                  <td>{s.status}</td>
                  <td>{s.found_count}</td>
                  <td>
                    <button onClick={() => onViewLeads(s.id)}>Show</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  )
}
