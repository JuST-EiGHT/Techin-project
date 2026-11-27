'use client'
// «Товары → Заявки на скидку» (шаг 4.3a): покупатели Ozon просят скидку — прибыль при их цене, пометки, решение по одной
// заявке и списком (предпросмотр → подтверждение → Ozon → журнал). Вкладка, поиск, пометки и сортировка — в адресе:
// сюда ведут уведомления (?task=), страница товара (?search=) и «Требует внимания».
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { DecisionModal, DiscountsEmpty, DiscountsTable, type DiscountSort } from '@/components/discounts'
import { CheckOption, DateRangePicker, FilterBar, FilterButton, FilterField, SearchBox, lastDays } from '@/components/filters'
import { type SortDir } from '@/components/table'
import { HelpTip, PageHeader, Panel, QuickTabs, Skeleton, buttonClass } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { MARK_FILTERS, type DecisionAction, type DiscountList, type DiscountMarkFilter, type DiscountRow, type DiscountTab } from '@/lib/discounts'
import { saveFile } from '@/lib/download'
import { type Range, todayMsk } from '@/lib/period'
import { refreshSyncStatus } from '@/lib/sync'
import { RefreshData } from '@/components/sync'

type KindStatus = { last_run: { status: string; error: string | null } | null; last_success_at: string | null }
type SyncStatus = { has_active_key: boolean; discounts?: KindStatus | null }

const TABS: DiscountTab[] = ['new', 'approved', 'declined', 'expired', 'all']
const ACTIVE = ['queued', 'running']
const POLL_MS = 3000
const ALL_TIME: Range = { from: '2020-01-01', to: todayMsk() }
const PERIODS = [
  { value: 'all', label: 'Всё время', range: ALL_TIME },
  { value: '7', label: '7 дней', range: lastDays(7) },
  { value: '30', label: '30 дней', range: lastDays(30) },
  { value: '90', label: '90 дней', range: lastDays(90) },
]

export default function DiscountsPage() {
  return (
    <Suspense>
      <Discounts />
    </Suspense>
  )
}

function Discounts() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const taskParam = params.get('task')
  const tabParam = params.get('tab') as DiscountTab | null
  const tab: DiscountTab = taskParam ? 'all' : tabParam && TABS.includes(tabParam) ? tabParam : 'new'
  const marks = (params.getAll('marks') as DiscountMarkFilter[]).filter((m) => MARK_FILTERS.some((f) => f.value === m))
  const sortKey = params.get('sort')
  const sort: DiscountSort = sortKey ? { key: sortKey, dir: params.get('order') === 'asc' ? 'asc' : 'desc' } : null
  const setQuery = useCallback(
    (patch: Record<string, string | string[] | null>) => {
      const next = new URLSearchParams(params.toString())
      for (const [k, v] of Object.entries(patch)) {
        next.delete(k)
        if (Array.isArray(v)) v.forEach((x) => next.append(k, x))
        else if (v !== null && v !== '') next.set(k, v)
      }
      const s = next.toString()
      router.replace(s ? `${pathname}?${s}` : pathname, { scroll: false })
    },
    [params, pathname, router],
  )

  const [search, setSearch] = useState(params.get('search') ?? '')
  const [debounced, setDebounced] = useState(search)
  useEffect(() => {
    const t = setTimeout(() => setDebounced(search), 300)
    return () => clearTimeout(t)
  }, [search])
  const [range, setRange] = useState<Range>(ALL_TIME)
  const [preset, setPreset] = useState<string | null>('all')

  const [data, setData] = useState<DiscountList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [starting, setStarting] = useState(false)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [deciding, setDeciding] = useState<{ rows: DiscountRow[]; action: DecisionAction } | null>(null)
  const [reload, setReload] = useState(0)
  const [ruleText, setRuleText] = useState('')
  const wasActive = useRef(false)

  const query = useMemo(() => {
    const q = new URLSearchParams({ tab })
    if (debounced.trim()) q.set('search', debounced.trim())
    if (preset !== 'all') {
      q.set('date_from', range.from)
      q.set('date_to', range.to)
    }
    marks.forEach((m) => q.append('marks', m))
    if (sort) {
      q.set('sort', sort.key)
      q.set('order', sort.dir)
    }
    return q.toString()
    // marks и sort — из адреса: сравниваем строкой
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, debounced, preset, range.from, range.to, marks.join(','), sort?.key, sort?.dir])

  useEffect(() => {
    api
      .get<DiscountList>(`/discounts?${query}`)
      .then((r) => {
        setData(r.data)
        setError(null)
        setRuleText(r.data.rule_margin_pct === null ? '' : Number(r.data.rule_margin_pct).toString())
      })
      .catch((err) => setError(errorMessage(err)))
  }, [query, reload])
  useEffect(() => setSelected(new Set()), [query])

  const loadSync = useCallback(async () => {
    try {
      const { data: s } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ACTIVE.includes(s.discounts?.last_run?.status ?? '')
      if (wasActive.current && !nowActive) setReload((n) => n + 1) // загрузка закончилась — перечитываем
      wasActive.current = nowActive
      setSync(s)
    } catch {
      /* статус необязателен — страница работает и без него */
    }
  }, [])
  useEffect(() => {
    loadSync()
  }, [loadSync])
  const active = ACTIVE.includes(sync?.discounts?.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    try {
      await api.post('/sync/discounts')
      refreshSyncStatus()
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  async function saveRule(value: string) {
    const v = value.trim().replace(',', '.')
    if (v !== '' && !Number.isFinite(Number(v))) return setError('Порог правила — число, например 15')
    try {
      await api.put('/discounts/rule', { margin_pct: v === '' ? null : Number(v).toFixed(2) })
      setReload((n) => n + 1)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const rows = useMemo(() => {
    const all = data?.items ?? []
    return taskParam ? all.filter((r) => r.id === taskParam) : all
  }, [data, taskParam])
  const c = data?.counts
  const chosen = rows.filter((r) => selected.has(r.id) && r.can_decide)
  const lastRun = sync?.discounts?.last_run
  const lastSuccess = sync?.discounts?.last_success_at
  const filtersCount = marks.length + (preset !== 'all' ? 1 : 0)
  const ruleOn = data?.rule_margin_pct !== null && data?.rule_margin_pct !== undefined

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Заявки на скидку"
        description={
          sync && !sync.has_active_key
            ? 'Заявки приходят из Ozon API — подключите ключ в «Интеграциях»'
            : 'Покупатели просят скидку — решите, выгодно ли. Заявки обновляются сами каждые 30 минут'
        }
        refresh={
          sync?.has_active_key && (
            <RefreshData at={lastSuccess} running={active || starting} onRefresh={syncNow} failed={lastRun?.status === 'failed'} what="заявки на скидку" testid="discounts-sync" />
          )
        }
        actions={
          <>
            <button
              type="button"
              onClick={() => saveFile(api.get(`/discounts/export?${query}`, { responseType: 'blob' }), 'discounts.xlsx').catch((err) => setError(errorMessage(err)))}
              className={buttonClass('secondary', 'sm')}
              data-testid="discounts-export"
            >
              Excel
            </button>
          </>
        }
      />

      <div className="space-y-3">
        <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />
        <div className="flex flex-wrap items-center gap-2">
          <QuickTabs
            label="Какие заявки показать"
            value={tab}
            onChange={(v) => setQuery({ tab: v === 'new' ? null : v, task: null })}
            items={[
              { value: 'new', label: 'Ждут решения', count: c?.new ?? null, tone: 'warn' },
              { value: 'approved', label: 'Одобренные', count: c?.approved ?? null },
              { value: 'declined', label: 'Отклонённые', count: c?.declined ?? null },
              { value: 'expired', label: 'Истёкшие', count: c?.expired ?? null },
              { value: 'all', label: 'Все', count: c?.all ?? null },
            ]}
            itemTestid={(v) => `discounts-tab-${v}`}
          />
          <HelpTip label="Как считаем">
            Маржа и прибыль — при цене, которую просит покупатель, тем же расчётом, что «Цены» (комиссия, логистика, реклама,
            себестоимость, налог). Минимальная для участия — цена, при которой прибыль с продажи 0: ниже — каждая продажа в
            минус. «Истёкшие» — новые заявки, срок которых прошёл (статуса «истекла» у Ozon нет).
          </HelpTip>
        </div>

        {tab === 'new' && c && c.loss > 0 && !marks.includes('loss') && (
          <p className="flex flex-wrap items-center gap-x-2 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="discounts-loss-banner">
            <span>
              <b>{c.loss}</b> {c.loss === 1 ? 'заявка просит' : 'заявок просят'} цену ниже минимальной для участия — продажа в минус
            </span>
            <button type="button" onClick={() => setQuery({ marks: ['loss'] })} className="underline">
              Показать
            </button>
          </p>
        )}

        {taskParam && (
          <p className="text-sm text-muted">
            Показана одна заявка из уведомления.{' '}
            <button type="button" onClick={() => setQuery({ task: null })} className="text-accent underline" data-testid="discounts-show-all">
              Показать все
            </button>
          </p>
        )}

        <Panel flush testid="discounts-panel">
          <div className="px-5 pt-4">
            <FilterBar
              end={
                <FilterButton count={filtersCount} onReset={() => { setQuery({ marks: [] }); setPreset('all'); setRange(ALL_TIME) }} testid="discounts-filters">
                  <FilterField label="Пометки">
                    <div className="space-y-2">
                      {MARK_FILTERS.map((m) => (
                        <CheckOption
                          key={m.value}
                          checked={marks.includes(m.value)}
                          onChange={(on) => setQuery({ marks: on ? [...marks, m.value] : marks.filter((x) => x !== m.value) })}
                          label={m.label}
                          hint={m.hint}
                          testid={`discounts-mark-${m.value}`}
                        />
                      ))}
                    </div>
                  </FilterField>
                  <FilterField label="Правило подсказки" hint="Сервис сам ничего не одобряет — только помечает подходящие заявки">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="text-muted">Одобрять, если маржа ≥</span>
                      <input
                        inputMode="decimal"
                        value={ruleText}
                        onChange={(e) => setRuleText(e.target.value)}
                        onBlur={(e) => saveRule(e.target.value)}
                        placeholder="—"
                        className="h-8 w-16 rounded-lg border border-line bg-bg px-2 text-right outline-none focus:border-accent"
                        aria-label="Порог маржи правила, %"
                        data-testid="discounts-rule"
                      />
                      <span className="text-muted">%</span>
                    </div>
                    <p className="text-xs text-faint">Пусто — подсказки нет. Заготовка автоматизации: пока только пометка «По правилу — одобрить».</p>
                  </FilterField>
                </FilterButton>
              }
            >
              <SearchBox value={search} onChange={(v) => { setSearch(v); setQuery({ search: v || null }) }} testid="discounts-search" className="w-full sm:w-72" />
              <DateRangePicker
                value={range}
                preset={preset}
                presets={PERIODS}
                maxDays={3700}
                onChange={({ range: r, preset: p }) => {
                  setRange(r)
                  setPreset(p)
                }}
                testid="discounts-period"
              />
            </FilterBar>
          </div>

          {chosen.length > 0 && (
            <div className="mx-5 mt-3 flex flex-wrap items-center gap-2 rounded-lg bg-accent/5 px-3 py-2 text-sm" data-testid="discounts-bulk">
              <span>
                Выбрано: <b>{chosen.length}</b>
              </span>
              <button type="button" onClick={() => setDeciding({ rows: chosen, action: 'approve' })} className={buttonClass('primary', 'sm')} data-testid="discounts-bulk-approve">
                Одобрить выбранные
              </button>
              <button type="button" onClick={() => setDeciding({ rows: chosen, action: 'decline' })} className={buttonClass('secondary', 'sm')} data-testid="discounts-bulk-decline">
                Отклонить выбранные
              </button>
              <button type="button" onClick={() => setSelected(new Set())} className="ml-auto text-muted hover:text-fg">
                Снять выбор
              </button>
            </div>
          )}

          <div className="mt-3">
            {data === null ? (
              <div className="space-y-2 px-5 pb-5">
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
                <Skeleton className="h-12" />
              </div>
            ) : rows.length === 0 ? (
              <div className="px-5 pb-5">
                <DiscountsEmpty hasKey={sync?.has_active_key ?? true} filtered={Boolean(debounced || marks.length || preset !== 'all' || tab !== 'all' || taskParam)} />
              </div>
            ) : (
              <DiscountsTable
                rows={rows}
                sort={sort}
                onSort={(key, dir: SortDir | null) => setQuery(dir ? { sort: key, order: dir } : { sort: null, order: null })}
                selected={selected}
                onSelect={(ids, on) =>
                  setSelected((prev) => {
                    const next = new Set(prev)
                    ids.forEach((id) => (on ? next.add(id) : next.delete(id)))
                    return next
                  })
                }
                onDecide={(r, action) => setDeciding({ rows: r, action })}
                ruleOn={ruleOn}
              />
            )}
          </div>
        </Panel>
      </div>

      {deciding && (
        <DecisionModal
          rows={deciding.rows}
          action={deciding.action}
          onClose={() => setDeciding(null)}
          onDone={() => {
            setSelected(new Set())
            setReload((n) => n + 1)
          }}
        />
      )}
    </div>
  )
}
