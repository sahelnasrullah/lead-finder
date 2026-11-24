export type LeadStatus =
  | 'new'
  | 'contacted'
  | 'replied'
  | 'interested'
  | 'not_interested'
  | 'customer'

export const LEAD_STATUSES: LeadStatus[] = [
  'new',
  'contacted',
  'replied',
  'interested',
  'not_interested',
  'customer',
]

export interface Lead {
  id: number
  name: string
  org_number: string
  address: string
  city: string
  country: string
  industry: string
  website: string
  domain: string
  phone: string
  email: string
  email_status: string
  legal_form: string
  is_sole_trader: boolean | null
  source: string
  status: LeadStatus
  notes: string
  linkedin_contacted: boolean
  search_id: number | null
}

export interface Country {
  code: string
  name: string
  language: string
  compliance_note: string
}

export interface Meta {
  countries: Country[]
  niches: { key: string; label: string }[]
  placeholders: string[]
  sender: { name: string; email: string; company: string; org_number: string }
  smtp_configured: boolean
  missing_for_sending: string[]
  bolagsverket_configured: boolean
  daily_cap: number
  send_delay: [number, number]
  sent_today: number
}

export interface Search {
  id: number
  country: string
  niche: string
  city: string
  result_limit: number
  status: 'pending' | 'running' | 'done' | 'failed'
  message: string
  found_count: number
}

export interface Template {
  key: string
  language: 'sv' | 'en'
  subject: string
  body: string
}

export interface PlannedMessage {
  lead_id: number
  company: string
  to_email: string
  subject: string
  body: string
  status: 'pending' | 'skipped'
  skip_reason: string
}

export interface MessageRow {
  id: number
  lead_id: number
  to_email: string
  subject: string
  body: string
  status: 'pending' | 'sent' | 'failed' | 'skipped'
  skip_reason: string
  error: string
  sent_at: string | null
}

export interface Campaign {
  id: number
  name: string
  subject: string
  body: string
  language: string
  status: string
  note: string
  counts: Record<string, number>
  running: boolean
  created_at: string
}

export interface CampaignDetail extends Campaign {
  messages: MessageRow[]
  company_names: Record<string, string>
}

export interface DncRow {
  id: number
  email: string
  domain: string
  reason: string
}

export interface ImportReport {
  created: number
  updated: number
  skipped: number
  errors: { row: number; message: string }[]
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch {
    throw new Error('Cannot reach the backend. Is it running on port 8000?')
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
      else if (Array.isArray(body.detail)) detail = body.detail.map((d: { msg: string }) => d.msg).join('; ')
    } catch {
      /* keep the status text */
    }
    throw new Error(detail)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export const api = {
  meta: () => request<Meta>('/meta'),
  randomNiche: () => request<{ key: string; label: string }>('/niches/random'),

  startSearch: (b: { country: string; niche: string; city: string; limit: number }) =>
    request<Search>('/searches', json('POST', b)),
  getSearch: (id: number) => request<Search>(`/searches/${id}`),
  listSearches: () => request<Search[]>('/searches'),

  listLeads: (params: Record<string, string>) =>
    request<Lead[]>(`/leads?${new URLSearchParams(params).toString()}`),
  updateLead: (id: number, patch: Partial<Lead>) => request<Lead>(`/leads/${id}`, json('PATCH', patch)),
  deleteLeads: (ids: number[]) => request<{ deleted: number }>('/leads/delete', json('POST', { ids })),
  importLeads: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<ImportReport>('/leads/import', { method: 'POST', body: form })
  },
  exportUrl: (ids: number[]) => `/api/leads/export${ids.length ? `?ids=${ids.join(',')}` : ''}`,

  templates: () => request<Template[]>('/templates'),
  preview: (b: unknown) => request<PlannedMessage[]>('/campaigns/preview', json('POST', b)),
  createCampaign: (b: unknown) => request<Campaign>('/campaigns', json('POST', b)),
  listCampaigns: () => request<Campaign[]>('/campaigns'),
  getCampaign: (id: number) => request<CampaignDetail>(`/campaigns/${id}`),
  send: (id: number, dryRun: boolean) =>
    request<{ started?: boolean; dry_run?: boolean; count?: number; would_send?: { to: string; subject: string }[] }>(
      `/campaigns/${id}/send`,
      json('POST', { dry_run: dryRun }),
    ),
  stop: (id: number) => request<{ stopped: boolean }>(`/campaigns/${id}/stop`, { method: 'POST' }),
  retryFailed: (id: number) => request<{ requeued: number }>(`/campaigns/${id}/retry-failed`, { method: 'POST' }),

  listDnc: () => request<DncRow[]>('/do-not-contact'),
  addDnc: (b: { email?: string; domain?: string; reason?: string }) =>
    request<DncRow>('/do-not-contact', json('POST', b)),
  removeDnc: (id: number) => request<void>(`/do-not-contact/${id}`, { method: 'DELETE' }),
}

export const SKIP_REASONS: Record<string, string> = {
  no_email: 'No email address',
  do_not_contact: 'On the do-not-contact list',
  sole_trader: 'Sole trader (may count as a private person)',
  duplicate_email: 'Same email already in this campaign',
  duplicate_domain: 'Another contact at the same company is already included',
  already_contacted: 'Already emailed in an earlier campaign',
}
