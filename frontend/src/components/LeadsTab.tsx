import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, LEAD_STATUSES } from '../api'
import type { ImportReport, Lead, LeadStatus } from '../api'

interface Props {
  selected: number[]
  setSelected: (ids: number[]) => void
  searchFilter: number | null
  clearSearchFilter: () => void
  goToOutreach: () => void
}

/** Text input that saves when the user leaves the field. */
function EditableCell({
  value,
  label,
  onSave,
  multiline = false,
}: {
  value: string
  label: string
  onSave: (v: string) => void
  multiline?: boolean
}) {
  const [draft, setDraft] = useState(value)
  const [prevValue, setPrevValue] = useState(value)
  if (value !== prevValue) {
    setPrevValue(value)
    setDraft(value)
  }
  const commit = () => {
    if (draft !== value) onSave(draft)
  }
  return multiline ? (
    <textarea aria-label={label} rows={2} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
  ) : (
    <input aria-label={label} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} />
  )
}

export function LeadsTab({ selected, setSelected, searchFilter, clearSearchFilter, goToOutreach }: Props) {
  const [leads, setLeads] = useState<Lead[]>([])
  const [q, setQ] = useState('')
  const [status, setStatus] = useState('')
  const [onlyWithEmail, setOnlyWithEmail] = useState(false)
  const [error, setError] = useState('')
  const [report, setReport] = useState<ImportReport | null>(null)
  const [loading, setLoading] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    const params: Record<string, string> = {}
    if (q.trim()) params.q = q.trim()
    if (status) params.status = status
    if (onlyWithEmail) params.has_email = 'true'
    if (searchFilter !== null) params.search_id = String(searchFilter)
    setLoading(true)
    api
      .listLeads(params)
      .then((rows) => {
        setLeads(rows)
        setError('')
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false))
  }, [q, status, onlyWithEmail, searchFilter])

  useEffect(() => {
    const t = window.setTimeout(load, 250)
    return () => window.clearTimeout(t)
  }, [load])

  const selectedSet = useMemo(() => new Set(selected), [selected])
  const allVisibleSelected = leads.length > 0 && leads.every((l) => selectedSet.has(l.id))

  function toggle(id: number) {
    setSelected(selectedSet.has(id) ? selected.filter((i) => i !== id) : [...selected, id])
  }

  function toggleAll() {
    const visible = leads.map((l) => l.id)
    setSelected(allVisibleSelected ? selected.filter((i) => !visible.includes(i)) : [...new Set([...selected, ...visible])])
  }

  function selectReady() {
    const ready = leads.filter((l) => l.email && l.is_sole_trader !== true && !l.linkedin_contacted).map((l) => l.id)
    setSelected(ready)
  }

  async function patch(id: number, change: Partial<Lead>) {
    try {
      const updated = await api.updateLead(id, change)
      setLeads((rows) => rows.map((r) => (r.id === id ? updated : r)))
      setError('')
    } catch (e) {
      setError((e as Error).message)
      load()
    }
  }

  async function onImport(file: File | undefined) {
    if (!file) return
    setReport(null)
    try {
      setReport(await api.importLeads(file))
      load()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  async function onDelete() {
    if (!selected.length || !window.confirm(`Delete ${selected.length} lead(s)? This cannot be undone.`)) return
    try {
      await api.deleteLeads(selected)
      setSelected([])
      load()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  async function addToDnc(lead: Lead) {
    if (!lead.email) return
    if (!window.confirm(`Never email ${lead.email} again?`)) return
    try {
      await api.addDnc({ email: lead.email, reason: `Added from lead "${lead.name}"` })
      setError('')
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <section>
      <h2>Leads</h2>
      <div className="toolbar">
        <input
          type="search"
          aria-label="Filter leads"
          placeholder="Filter by name, city, email, website…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select aria-label="Status filter" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {LEAD_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s.replace('_', ' ')}
            </option>
          ))}
        </select>
        <label className="inline">
          <input type="checkbox" checked={onlyWithEmail} onChange={(e) => setOnlyWithEmail(e.target.checked)} /> Has email
        </label>
        {searchFilter !== null && (
          <button onClick={clearSearchFilter}>Showing one search – show all</button>
        )}
      </div>

      <div className="toolbar">
        <button onClick={selectReady} title="Select leads with an email that are not sole traders or contacted on LinkedIn">
          Select ready to email
        </button>
        <a className="button" href={api.exportUrl(selected)} download>
          Export Excel{selected.length ? ` (${selected.length} selected)` : ' (all)'}
        </a>
        <button onClick={() => fileInput.current?.click()}>Import Excel</button>
        <input
          ref={fileInput}
          type="file"
          accept=".xlsx"
          hidden
          onChange={(e) => onImport(e.target.files?.[0])}
          aria-label="Import Excel file"
        />
        <button onClick={onDelete} disabled={!selected.length}>
          Delete selected
        </button>
        <button className="primary" onClick={goToOutreach} disabled={!selected.length}>
          Email {selected.length} selected →
        </button>
      </div>

      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {report && (
        <div className="banner info" role="status">
          Import finished: {report.created} new, {report.updated} updated, {report.skipped} skipped.
          {report.errors.length > 0 && (
            <ul>
              {report.errors.slice(0, 8).map((e, i) => (
                <li key={i}>
                  Row {e.row}: {e.message}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <p className="muted">
        {loading ? 'Loading…' : `${leads.length} leads`}, {selected.length} selected. Edit cells directly; changes save
        when you leave the field.
      </p>

      <div className="table-wrap">
        <table className="leads">
          <thead>
            <tr>
              <th>
                <input type="checkbox" aria-label="Select all" checked={allVisibleSelected} onChange={toggleAll} />
              </th>
              <th>Company</th>
              <th>City</th>
              <th>Email</th>
              <th>Phone</th>
              <th>Website</th>
              <th>Org number</th>
              <th>Legal form</th>
              <th>Status</th>
              <th title="Tick when you have messaged them on LinkedIn manually">LinkedIn (manual)</th>
              <th>Notes</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {leads.map((l) => (
              <tr key={l.id} className={selectedSet.has(l.id) ? 'selected' : ''}>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Select ${l.name}`}
                    checked={selectedSet.has(l.id)}
                    onChange={() => toggle(l.id)}
                  />
                </td>
                <td>
                  <strong>{l.name}</strong>
                  <div className="muted small">{l.industry}</div>
                </td>
                <td>{l.city}</td>
                <td>
                  <EditableCell label={`Email for ${l.name}`} value={l.email} onSave={(v) => patch(l.id, { email: v })} />
                </td>
                <td>
                  <EditableCell label={`Phone for ${l.name}`} value={l.phone} onSave={(v) => patch(l.id, { phone: v })} />
                </td>
                <td>
                  {l.website ? (
                    <a href={l.website} target="_blank" rel="noreferrer noopener">
                      {l.domain}
                    </a>
                  ) : (
                    '–'
                  )}
                </td>
                <td>{l.org_number || '–'}</td>
                <td>
                  {l.is_sole_trader === true && (
                    <span className="badge warn" title="Emails to sole traders may count as emails to private persons. Excluded by default.">
                      Sole trader
                    </span>
                  )}
                  {l.is_sole_trader === false && <span className="badge ok">{l.legal_form || 'Company'}</span>}
                  {l.is_sole_trader === null && <span className="muted">unknown</span>}
                </td>
                <td>
                  <select
                    aria-label={`Status for ${l.name}`}
                    value={l.status}
                    onChange={(e) => patch(l.id, { status: e.target.value as LeadStatus })}
                  >
                    {LEAD_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s.replace('_', ' ')}
                      </option>
                    ))}
                  </select>
                </td>
                <td>
                  <input
                    type="checkbox"
                    aria-label={`Contacted ${l.name} on LinkedIn`}
                    checked={l.linkedin_contacted}
                    onChange={(e) => patch(l.id, { linkedin_contacted: e.target.checked })}
                  />
                </td>
                <td>
                  <EditableCell multiline label={`Notes for ${l.name}`} value={l.notes} onSave={(v) => patch(l.id, { notes: v })} />
                </td>
                <td>
                  <button disabled={!l.email} onClick={() => addToDnc(l)} title="Never email this address">
                    Do not contact
                  </button>
                </td>
              </tr>
            ))}
            {!loading && leads.length === 0 && (
              <tr>
                <td colSpan={12} className="muted">
                  No leads yet. Run a search or import an Excel file.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}
