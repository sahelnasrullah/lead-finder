import { useCallback, useEffect, useMemo, useState } from 'react'
import { api, SKIP_REASONS } from '../api'
import type { Campaign, CampaignDetail, Meta, PlannedMessage, Template } from '../api'

interface Props {
  meta: Meta | null
  selected: number[]
  refreshMeta: () => void
}

const TEMPLATE_LABELS: Record<string, string> = {
  interview_request: 'Interview request (student asking for 15 minutes)',
  pilot_offer: 'Pilot offer (fill in the [FYLL I / FILL IN] parts first)',
}

export function OutreachTab({ meta, selected, refreshMeta }: Props) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [tplKey, setTplKey] = useState('interview_request')
  const [language, setLanguage] = useState<'sv' | 'en'>('sv')
  const [subject, setSubject] = useState('')
  const [body, setBody] = useState('')
  const [campaignName, setCampaignName] = useState('Law firm interviews')
  const [includeSole, setIncludeSole] = useState(false)
  const [skipPrev, setSkipPrev] = useState(true)

  const [planned, setPlanned] = useState<PlannedMessage[] | null>(null)
  const [openPreview, setOpenPreview] = useState<number | null>(null)
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [active, setActive] = useState<CampaignDetail | null>(null)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .templates()
      .then((t) => {
        setTemplates(t)
        const first = t.find((x) => x.key === 'interview_request' && x.language === 'sv')
        if (first) {
          setSubject(first.subject)
          setBody(first.body)
        }
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  const applyTemplate = useCallback(
    (key: string, lang: 'sv' | 'en', all: Template[] = templates) => {
      const t = all.find((x) => x.key === key && x.language === lang)
      if (t) {
        setSubject(t.subject)
        setBody(t.body)
        setPlanned(null)
      }
    },
    [templates],
  )

  const loadCampaigns = useCallback(() => api.listCampaigns().then(setCampaigns).catch(() => undefined), [])
  useEffect(() => {
    loadCampaigns()
  }, [loadCampaigns])

  const activeId = active?.id
  const isSending = active?.status === 'sending' || active?.running === true
  useEffect(() => {
    if (activeId === undefined || !isSending) return
    const t = window.setInterval(() => {
      api
        .getCampaign(activeId)
        .then((c) => {
          setActive(c)
          if (c.status !== 'sending') {
            loadCampaigns()
            refreshMeta()
          }
        })
        .catch((e: Error) => setError(e.message))
    }, 3000)
    return () => window.clearInterval(t)
  }, [activeId, isSending, loadCampaigns, refreshMeta])

  const payload = useMemo(
    () => ({
      name: campaignName,
      subject,
      body,
      language,
      lead_ids: selected,
      include_sole_traders: includeSole,
      skip_previously_contacted: skipPrev,
    }),
    [campaignName, subject, body, language, selected, includeSole, skipPrev],
  )

  const willSend = planned?.filter((p) => p.status === 'pending').length ?? 0
  const skipped = planned?.filter((p) => p.status === 'skipped') ?? []
  const cannotSend = meta !== null && meta.missing_for_sending.length > 0

  async function guard(fn: () => Promise<void>) {
    setBusy(true)
    setError('')
    setInfo('')
    try {
      await fn()
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const onPreview = () =>
    guard(async () => {
      setPlanned(await api.preview(payload))
      setOpenPreview(null)
    })

  const onCreate = () =>
    guard(async () => {
      const c = await api.createCampaign(payload)
      setActive(await api.getCampaign(c.id))
      setPlanned(null)
      loadCampaigns()
    })

  const reload = async (id: number) => setActive(await api.getCampaign(id))

  const onDryRun = () =>
    active &&
    guard(async () => {
      const r = await api.send(active.id, true)
      setInfo(`Dry run: ${r.count ?? 0} email(s) would be sent. Nothing was sent.`)
    })

  const onSend = () =>
    active &&
    guard(async () => {
      const pending = active.counts.pending ?? 0
      const [dmin, dmax] = meta?.send_delay ?? [30, 60]
      const ok = window.confirm(
        `Send ${pending} email(s) from ${meta?.sender.email}?\n\n` +
          `They go out one at a time, about every ${dmin}–${dmax} seconds (daily cap ${meta?.daily_cap}). ` +
          'Keep the backend running until it finishes. Real emails will be sent.',
      )
      if (!ok) return
      await api.send(active.id, false)
      await reload(active.id)
      loadCampaigns()
    })

  const onStop = () =>
    active &&
    guard(async () => {
      await api.stop(active.id)
      await reload(active.id)
    })

  const onRetry = () =>
    active &&
    guard(async () => {
      const r = await api.retryFailed(active.id)
      setInfo(`${r.requeued} failed email(s) put back in the queue. Press Send to try again.`)
      await reload(active.id)
    })

  const open = (id: number) => guard(() => reload(id))

  const previewRow = planned?.find((p) => p.lead_id === openPreview)

  return (
    <section>
      <h2>Outreach</h2>

      {meta && cannotSend && (
        <div className="banner warn" role="alert">
          Real sending is disabled until you set these in <code>backend/.env</code>:{' '}
          <strong>{meta.missing_for_sending.join(', ')}</strong>. Preview and dry run still work.
        </div>
      )}
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {info && (
        <div className="banner info" role="status">
          {info}
        </div>
      )}

      <div className="card">
        <h3>1. Write the message</h3>
        <div className="form-grid">
          <label>
            Template
            <select
              value={tplKey}
              onChange={(e) => {
                setTplKey(e.target.value)
                applyTemplate(e.target.value, language)
              }}
            >
              {[...new Set(templates.map((t) => t.key))].map((k) => (
                <option key={k} value={k}>
                  {TEMPLATE_LABELS[k] ?? k}
                </option>
              ))}
            </select>
          </label>
          <label>
            Language
            <select
              value={language}
              onChange={(e) => {
                const l = e.target.value as 'sv' | 'en'
                setLanguage(l)
                applyTemplate(tplKey, l)
              }}
            >
              <option value="sv">Svenska</option>
              <option value="en">English</option>
            </select>
          </label>
          <label>
            Campaign name
            <input value={campaignName} onChange={(e) => setCampaignName(e.target.value)} />
          </label>
        </div>

        <label>
          Subject
          <input
            value={subject}
            onChange={(e) => {
              setSubject(e.target.value)
              setPlanned(null)
            }}
          />
        </label>
        <label>
          Message
          <textarea
            rows={12}
            value={body}
            onChange={(e) => {
              setBody(e.target.value)
              setPlanned(null)
            }}
          />
        </label>
        <p className="muted small">
          Placeholders: {meta?.placeholders.map((p) => `{{${p}}}`).join('  ') ?? '…'}. An identity footer (name, company,
          org number) and an opt-out line are added automatically.
        </p>

        <div className="form-grid">
          <label className="inline">
            <input type="checkbox" checked={includeSole} onChange={(e) => setIncludeSole(e.target.checked)} /> Include
            sole traders (may count as private persons – needs consent)
          </label>
          <label className="inline">
            <input type="checkbox" checked={skipPrev} onChange={(e) => setSkipPrev(e.target.checked)} /> Skip addresses
            I have already emailed
          </label>
        </div>
      </div>

      <div className="card">
        <h3>2. Check who gets it ({selected.length} leads selected)</h3>
        {selected.length === 0 && <p className="muted">Select leads in the Leads tab first.</p>}
        <div className="row">
          <button onClick={onPreview} disabled={busy || selected.length === 0}>
            Preview
          </button>
          <button className="primary" onClick={onCreate} disabled={busy || !planned || willSend === 0}>
            Create campaign ({willSend} emails)
          </button>
        </div>

        {planned && (
          <>
            <p>
              <strong>{willSend}</strong> will be sent, <strong>{skipped.length}</strong> skipped. One email per
              company.
            </p>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>To</th>
                    <th>Result</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {planned.map((p) => (
                    <tr key={p.lead_id} className={p.status === 'skipped' ? 'skipped' : ''}>
                      <td>{p.company}</td>
                      <td>{p.to_email || '–'}</td>
                      <td>{p.status === 'pending' ? 'Will send' : `Skipped: ${SKIP_REASONS[p.skip_reason] ?? p.skip_reason}`}</td>
                      <td>
                        {p.status === 'pending' && (
                          <button onClick={() => setOpenPreview(openPreview === p.lead_id ? null : p.lead_id)}>
                            {openPreview === p.lead_id ? 'Hide' : 'Show email'}
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {previewRow && (
              <div className="email-preview">
                <div className="muted small">To: {previewRow.to_email}</div>
                <strong>{previewRow.subject}</strong>
                <pre>{previewRow.body}</pre>
              </div>
            )}
          </>
        )}
      </div>

      <div className="card">
        <h3>3. Send</h3>
        <label>
          Open an earlier campaign
          <select value={active?.id ?? ''} onChange={(e) => e.target.value && open(Number(e.target.value))}>
            <option value="">–</option>
            {campaigns.map((c) => (
              <option key={c.id} value={c.id}>
                #{c.id} {c.name} ({c.status}, {c.counts.sent ?? 0} sent)
              </option>
            ))}
          </select>
        </label>

        {active && (
          <div>
            <h4>
              #{active.id} {active.name} – <span className={`badge ${active.status}`}>{active.status}</span>
            </h4>
            <p>
              Sent {active.counts.sent ?? 0} · waiting {active.counts.pending ?? 0} · failed {active.counts.failed ?? 0} ·
              skipped {active.counts.skipped ?? 0}
              {meta && ` · sent today ${meta.sent_today}/${meta.daily_cap}`}
            </p>
            {active.note && <p className="note">{active.note}</p>}
            <div className="row">
              <button onClick={onDryRun} disabled={busy}>
                Dry run
              </button>
              <button className="primary" onClick={onSend} disabled={busy || isSending || cannotSend || !(active.counts.pending > 0)}>
                {isSending ? 'Sending…' : 'Send now'}
              </button>
              <button onClick={onStop} disabled={!isSending}>
                Stop
              </button>
              <button onClick={onRetry} disabled={busy || !(active.counts.failed > 0)}>
                Retry failed
              </button>
              <button onClick={() => reload(active.id)}>Refresh</button>
            </div>

            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Company</th>
                    <th>To</th>
                    <th>Status</th>
                    <th>Details</th>
                  </tr>
                </thead>
                <tbody>
                  {active.messages.map((m) => (
                    <tr key={m.id} className={m.status}>
                      <td>{active.company_names[String(m.lead_id)] ?? m.lead_id}</td>
                      <td>{m.to_email || '–'}</td>
                      <td>{m.status}</td>
                      <td>
                        {m.status === 'skipped' && (SKIP_REASONS[m.skip_reason] ?? m.skip_reason)}
                        {m.status === 'failed' && m.error}
                        {m.status === 'sent' && m.sent_at && new Date(m.sent_at + 'Z').toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      <p className="muted small">
        LinkedIn messages are sent manually by you. Tick “LinkedIn (manual)” on a lead after you contact them so you do
        not message the same person twice.
      </p>
    </section>
  )
}
