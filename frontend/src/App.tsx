import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { Meta } from './api'
import { DoNotContactTab } from './components/DoNotContactTab'
import { LeadsTab } from './components/LeadsTab'
import { OutreachTab } from './components/OutreachTab'
import { SearchTab } from './components/SearchTab'

type Tab = 'search' | 'leads' | 'outreach' | 'dnc'

const TABS: { id: Tab; label: string }[] = [
  { id: 'search', label: '1. Find leads' },
  { id: 'leads', label: '2. Leads' },
  { id: 'outreach', label: '3. Outreach' },
  { id: 'dnc', label: 'Do not contact' },
]

export default function App() {
  const [tab, setTab] = useState<Tab>('search')
  const [meta, setMeta] = useState<Meta | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<number[]>([])
  const [searchFilter, setSearchFilter] = useState<number | null>(null)

  const loadMeta = useCallback(() => {
    api
      .meta()
      .then((m) => {
        setMeta(m)
        setError('')
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(loadMeta, [loadMeta])

  return (
    <div className="app">
      <header className="header">
        <h1>Lead Finder</h1>
        <p className="muted">
          Find companies, collect contact details, and send one personal email per company.
          {meta && ` Sending as ${meta.sender.name}, ${meta.sender.company}.`}
        </p>
      </header>

      {error && (
        <div className="banner error" role="alert">
          {error} <button onClick={loadMeta}>Retry</button>
        </div>
      )}

      <nav className="tabs" aria-label="Sections">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? 'tab active' : 'tab'}
            aria-current={tab === t.id ? 'page' : undefined}
            onClick={() => setTab(t.id)}
          >
            {t.label}
            {t.id === 'outreach' && selected.length > 0 ? ` (${selected.length})` : ''}
          </button>
        ))}
      </nav>

      <main>
        {tab === 'search' && (
          <SearchTab
            meta={meta}
            onViewLeads={(searchId) => {
              setSearchFilter(searchId)
              setTab('leads')
            }}
          />
        )}
        {tab === 'leads' && (
          <LeadsTab
            selected={selected}
            setSelected={setSelected}
            searchFilter={searchFilter}
            clearSearchFilter={() => setSearchFilter(null)}
            goToOutreach={() => setTab('outreach')}
          />
        )}
        {tab === 'outreach' && <OutreachTab meta={meta} selected={selected} refreshMeta={loadMeta} />}
        {tab === 'dnc' && <DoNotContactTab />}
      </main>
    </div>
  )
}
