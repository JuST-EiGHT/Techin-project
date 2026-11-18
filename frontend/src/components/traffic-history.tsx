'use client'

// «Продвижение → Ссылки с UTM → Созданные ссылки» (шаг 11.2): история ссылок — версии (правка — новая запись), диплинк и
// подменный артикул из кабинета Ozon, срок диплинка (30 дней без переходов) и «Открыть» — отсчёт заново.
import { ExternalLink, FileSpreadsheet, History, Pencil, QrCode, RotateCcw, Tag, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { CopyText } from '@/components/copy'
import { DateRangePicker, FilterButton, FilterField, SearchBox, lastDays, selectClass } from '@/components/filters'
import { Modal, input } from '@/components/planning/ui'
import { SortTh, useSort } from '@/components/table'
import { Field, QrModal, SuggestInput, plural } from '@/components/traffic'
import { Badge, Button, EmptyState, HelpTip, Panel, QuickTabs, Skeleton, Sub } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { type Range, todayMsk } from '@/lib/period'
import {
  DEEPLINK_WHY,
  type LinkFilters,
  type LinkList,
  type LinkRow,
  type LinkTab,
  type LinkVersion,
  SUBSTITUTE_WHY,
  type TrafficSettings,
  deleteLink,
  exportLinkHistory,
  linkVersions,
  listLinks,
  markOpened,
  restoreLink,
  updateLink,
} from '@/lib/traffic'

const fmtDay = (iso: string) => new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })
const fmtTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

/** Срок диплинка плашкой: «до 12.12», «осталось 3 дня», «не работает». */
export function DeeplinkBadge({ row }: { row: LinkRow }) {
  const s = row.deeplink_status
  if (!row.deeplink || !s) return <span className="text-faint">—</span>
  if (s.state === 'expired')
    return (
      <Badge tone="bad" dot title={`30 дней без переходов — с ${fmtDay(s.expires_on)} не ведёт на страницу`}>
        Не работает
      </Badge>
    )
  if (s.state === 'soon')
    return (
      <Badge tone="warn" dot title={`Работает до ${fmtDay(s.expires_on)}, если никто не перейдёт`}>
        {s.days_left <= 0 ? 'Истекает сегодня' : `Осталось ${s.days_left} ${plural(s.days_left, 'день', 'дня', 'дней')}`}
      </Badge>
    )
  return (
    <Badge tone="ok" dot title="Срок отсчитывается от последнего перехода">
      До {fmtDay(s.expires_on)}
    </Badge>
  )
}

function Versions({ id }: { id: string }) {
  const [list, setList] = useState<LinkVersion[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    linkVersions(id).then(setList, (err) => setError(errorMessage(err)))
  }, [id])
  if (error) return <p className="text-sm text-bad">{error}</p>
  if (!list) return <Skeleton className="h-16" />
  return (
    <ol className="space-y-2 text-sm" data-testid="traffic-versions">
      {list.map((v) => (
        <li key={v.version} className="rounded-lg border border-line p-2.5">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span className="font-medium text-fg">Версия {v.version}</span>
            <span>{fmtTime(v.created_at)}</span>
            {!v.superseded_at && <Badge tone="accent">Сейчас</Badge>}
            {v.deleted_at && <Badge tone="muted">Удалена</Badge>}
          </div>
          <p className="mt-1 break-all font-mono text-xs">
            {v.utm_source} / {v.utm_medium} · {v.campaign}
            {v.utm_content && ` · content: ${v.utm_content}`}
            {v.utm_term && ` · term: ${v.utm_term}`}
          </p>
          {(v.deeplink || v.substitute_code) && (
            <p className="mt-0.5 text-xs text-muted">
              {v.deeplink && 'с диплинком'}
              {v.deeplink && v.substitute_code && ' · '}
              {v.substitute_code && `подменный артикул ${v.substitute_code}`}
            </p>
          )}
        </li>
      ))}
    </ol>
  )
}

function EditModal({ row, settings, onClose, onSaved }: {
  row: LinkRow
  settings: TrafficSettings
  onClose: () => void
  onSaved: (row: LinkRow, warnings: string[]) => void
}) {
  const channel = settings.channels.find((c) => c.key === row.channel) ?? settings.channels[settings.channels.length - 1]
  const [f, setF] = useState({
    campaign: row.campaign_name,
    source: row.utm_source,
    medium: row.utm_medium,
    term: row.utm_term,
    content: row.utm_content,
    deeplink: row.deeplink ?? '',
    substitute_code: row.substitute_code ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [showVersions, setShowVersions] = useState(false)
  const set = (patch: Partial<typeof f>) => setF((x) => ({ ...x, ...patch }))
  const labelsChanged =
    f.campaign !== row.campaign_name || f.source !== row.utm_source || f.medium !== row.utm_medium || f.term !== row.utm_term || f.content !== row.utm_content

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    const body: Record<string, string> = {}
    for (const [k, v] of Object.entries(f)) {
      const before = { campaign: row.campaign_name, source: row.utm_source, medium: row.utm_medium, term: row.utm_term, content: row.utm_content, deeplink: row.deeplink ?? '', substitute_code: row.substitute_code ?? '' }[k]
      if (v !== before) body[k] = v
    }
    if (channel.fixed_source) delete body.source
    try {
      const res = await updateLink(row.link_id, body)
      onSaved(res.link, res.warnings)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title="Изменить ссылку" onClose={onClose}>
      <form onSubmit={save} className="space-y-4" data-testid="traffic-edit">
        <p className="break-all text-xs text-muted">
          {row.product ? `${row.product.name ?? 'Без названия'} · ${row.product.offer_id}` : row.source_url}
        </p>
        <Field label="Название кампании" htmlFor="edit-campaign" required>
          <input id="edit-campaign" value={f.campaign} onChange={(e) => set({ campaign: e.target.value })} className={input} autoComplete="off" spellCheck={false} data-testid="edit-campaign" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Источник" htmlFor="edit-source" required hint="utm_source">
            <SuggestInput id="edit-source" value={f.source} onChange={(v) => set({ source: v })} options={channel.source_options} disabled={channel.fixed_source} required placeholder="Выберите или впишите" testid="edit-source" />
          </Field>
          <Field label="Тип трафика" htmlFor="edit-medium" required hint="utm_medium">
            <SuggestInput id="edit-medium" value={f.medium} onChange={(v) => set({ medium: v })} options={channel.medium_options} required placeholder="Выберите или впишите" testid="edit-medium" />
          </Field>
          <Field label="Содержание" htmlFor="edit-content" hint="utm_content, необязательно">
            <input id="edit-content" value={f.content} onChange={(e) => set({ content: e.target.value })} className={input} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Ключевое слово" htmlFor="edit-term" hint="utm_term, необязательно">
            <input id="edit-term" value={f.term} onChange={(e) => set({ term: e.target.value })} className={input} autoComplete="off" spellCheck={false} />
          </Field>
        </div>
        <div className="space-y-4 border-t border-line pt-4">
          <p className="text-sm font-medium">Из кабинета Ozon — необязательно</p>
          <Field label="Диплинк" htmlFor="edit-deeplink" help={DEEPLINK_WHY} hint="https://ozon.onelink.me/…">
            <input id="edit-deeplink" value={f.deeplink} onChange={(e) => set({ deeplink: e.target.value })} placeholder="Вставьте ссылку с диплинком" className={`${input} font-mono text-xs`} autoComplete="off" spellCheck={false} data-testid="edit-deeplink" />
          </Field>
          {labelsChanged && row.deeplink && f.deeplink === row.deeplink && (
            <p className="text-xs text-warn">Метки меняются — диплинк сделан для прежних, сервис его уберёт. Создайте новый в кабинете Ozon и вставьте сюда.</p>
          )}
          <Field label="Подменный артикул" htmlFor="edit-substitute" help={SUBSTITUTE_WHY} hint="Например O-4374100">
            <input id="edit-substitute" value={f.substitute_code} onChange={(e) => set({ substitute_code: e.target.value })} placeholder="O-…" className={`${input} sm:max-w-[12rem]`} autoComplete="off" spellCheck={false} data-testid="edit-substitute" />
          </Field>
        </div>
        {error && (
          <p className="text-sm text-bad" role="alert" data-testid="edit-error">
            {error}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={busy} data-testid="edit-save">
            Сохранить — новая версия
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
          {row.versions > 1 && (
            <Button type="button" variant="ghost" size="sm" onClick={() => setShowVersions((v) => !v)} data-testid="edit-versions">
              <History className="h-4 w-4" aria-hidden /> История · {row.versions}
            </Button>
          )}
        </div>
        {showVersions && <Versions id={row.link_id} />}
      </form>
    </Modal>
  )
}

const TAB_ITEMS = (c: LinkList['counts'] | undefined): { value: LinkTab; label: string; count?: number; tone?: 'warn' }[] => [
  { value: 'all', label: 'Все', count: c?.all },
  { value: 'deeplink', label: 'С диплинком', count: c?.deeplink },
  { value: 'expiring', label: 'Диплинк истекает', count: c?.expiring, tone: 'warn' },
  { value: 'substitute', label: 'С подменным артикулом', count: c?.substitute },
  { value: 'deleted', label: 'Удалённые', count: c?.deleted },
]

export function LinkHistory({ settings, focus, version }: { settings: TrafficSettings; focus: string | null; version: number }) {
  const [tab, setTab] = useState<LinkTab>('all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 250)
  const [range, setRange] = useState<Range | null>(null) // null — за всё время
  const [preset, setPreset] = useState<string | null>('all')
  const [extra, setExtra] = useState<{ campaign: string; source: string; medium: string; channel: string }>({ campaign: '', source: '', medium: '', channel: '' })
  const [data, setData] = useState<LinkList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [edit, setEdit] = useState<LinkRow | null>(null)
  const [qr, setQr] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [earliest, setEarliest] = useState<string | null>(null) // первая созданная ссылка — «За всё время» с этого дня

  const filters: LinkFilters = useMemo(
    () => ({ tab, search: q.trim() || undefined, date_from: range?.from, date_to: range?.to, ...Object.fromEntries(Object.entries(extra).filter(([, v]) => v)) }),
    [tab, q, range, extra],
  )
  const load = useCallback(() => {
    listLinks(filters).then(
      (d) => {
        setData(d)
        setError(null)
        if (!filters.date_from && d.items.length)
          setEarliest((e) => [e, ...d.items.map((r) => r.created_at.slice(0, 10))].filter(Boolean).sort()[0] as string)
      },
      (err) => setError(errorMessage(err)),
    )
  }, [filters])
  useEffect(load, [load, version])

  // пришли по ссылке из уведомления (?link=) — сразу открыть её
  useEffect(() => {
    if (!focus || !data) return
    const row = data.items.find((r) => r.link_id === focus)
    if (row) {
      setEdit(row)
      document.getElementById(`link-${focus}`)?.scrollIntoView({ block: 'center' })
    }
    // только при первом появлении данных
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, data === null])

  const by = useMemo(
    () => ({
      created: (r: LinkRow) => r.created_at,
      what: (r: LinkRow) => r.product?.name ?? r.source_url,
      campaign: (r: LinkRow) => r.campaign_name,
      source: (r: LinkRow) => `${r.utm_source}/${r.utm_medium}`,
      deeplink: (r: LinkRow) => (r.deeplink_status ? r.deeplink_status.days_left : null),
    }),
    [],
  )
  const t = useSort(data?.items, by)
  const extraCount = Object.values(extra).filter(Boolean).length
  const act = (p: Promise<unknown>, done?: string) =>
    p.then(
      () => (setNote(done ?? null), load()),
      (err) => setNote(errorMessage(err)),
    )

  async function open(row: LinkRow) {
    if (!row.deeplink) return
    window.open(row.deeplink, '_blank', 'noopener')
    await act(markOpened(row.link_id), 'Открыли ссылку — отсчёт 30 дней начался заново')
  }

  const today = todayMsk()
  const all = { from: earliest && earliest < today ? earliest : today, to: today }
  return (
    <div className="space-y-4" data-testid="traffic-history">
      <QuickTabs value={tab} onChange={setTab} items={TAB_ITEMS(data?.counts)} testid="traffic-history-tabs" itemTestid={(v) => `traffic-history-tab-${v}`} label="Срезы ссылок" />
      <Panel flush testid="traffic-history-panel">
        <div className="flex flex-wrap items-center gap-2 px-5 pb-3 pt-4">
          <SearchBox value={search} onChange={setSearch} placeholder="Артикул, название, ссылка или кампания" testid="traffic-history-search" className="min-w-0 flex-1 sm:max-w-xs" />
          <DateRangePicker
            value={range ?? all}
            preset={range ? preset : 'all'}
            presets={[
              { value: 'all', label: 'За всё время', range: all },
              { value: '7', label: '7 дней', range: lastDays(7) },
              { value: '30', label: '30 дней', range: lastDays(30) },
            ]}
            onChange={({ range: r, preset: p }) => {
              setPreset(p)
              setRange(p === 'all' ? null : r)
            }}
            testid="traffic-history-period"
          />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <FilterButton count={extraCount} onReset={() => setExtra({ campaign: '', source: '', medium: '', channel: '' })} testid="traffic-history-filters">
              <FilterField label="Кампания">
                <select value={extra.campaign} onChange={(e) => setExtra((x) => ({ ...x, campaign: e.target.value }))} className={selectClass} data-testid="filter-campaign">
                  <option value="">Все</option>
                  {data?.facets.campaigns.map((c) => <option key={c}>{c}</option>)}
                </select>
              </FilterField>
              <FilterField label="Источник (utm_source)">
                <select value={extra.source} onChange={(e) => setExtra((x) => ({ ...x, source: e.target.value }))} className={selectClass}>
                  <option value="">Все</option>
                  {data?.facets.sources.map((c) => <option key={c}>{c}</option>)}
                </select>
              </FilterField>
              <FilterField label="Тип трафика (utm_medium)">
                <select value={extra.medium} onChange={(e) => setExtra((x) => ({ ...x, medium: e.target.value }))} className={selectClass}>
                  <option value="">Все</option>
                  {data?.facets.mediums.map((c) => <option key={c}>{c}</option>)}
                </select>
              </FilterField>
              <FilterField label="Канал">
                <select value={extra.channel} onChange={(e) => setExtra((x) => ({ ...x, channel: e.target.value }))} className={selectClass}>
                  <option value="">Все</option>
                  {settings.channels.map((c) => (
                    <option key={c.key} value={c.key}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </FilterField>
            </FilterButton>
            <Button size="sm" onClick={() => exportLinkHistory(filters).catch((err) => setNote(errorMessage(err)))} disabled={!data?.total} data-testid="traffic-history-xlsx">
              <FileSpreadsheet className="h-4 w-4" aria-hidden /> Excel
            </Button>
          </div>
        </div>
        {note && (
          <p className="px-5 pb-3 text-sm text-muted" role="status" data-testid="traffic-history-note">
            {note}
          </p>
        )}
        {error && <p className="px-5 pb-4 text-sm text-bad">{error}</p>}
        {!data && !error && <Skeleton className="mx-5 mb-5 h-40" />}
        {data && data.total === 0 && (
          <EmptyState icon={Tag} title={data.counts.all || data.counts.deleted ? 'Ничего не нашлось' : 'Пока нет созданных ссылок'} testid="traffic-history-empty">
            {data.counts.all || data.counts.deleted
              ? 'Поменяйте поиск, период, вкладку или фильтры.'
              : 'Соберите ссылки во вкладке «Конструктор» — они сохранятся здесь.'}
          </EmptyState>
        )}
        {data && data.total > 0 && (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[860px] text-sm" data-testid="traffic-history-table">
              <thead className="bg-soft/60 text-xs text-muted">
                <tr>
                  <SortTh {...t.th('created')} kind="date" className="py-2 pl-5 pr-3 font-medium">
                    Создана
                  </SortTh>
                  <SortTh {...t.th('what')} kind="text" className="px-3 py-2 font-medium">
                    Что размечено
                  </SortTh>
                  <SortTh {...t.th('campaign')} kind="text" className="px-3 py-2 font-medium">
                    Кампания
                  </SortTh>
                  <SortTh {...t.th('source')} kind="text" className="px-3 py-2 font-medium">
                    Источник / тип
                  </SortTh>
                  <th className="px-3 py-2 text-left font-medium">Ссылка</th>
                  <SortTh {...t.th('deeplink')} kind="number" className="px-3 py-2 font-medium" title="Сначала больше дней до конца срока">
                    <span className="inline-flex items-center gap-1">
                      Диплинк
                    </span>
                  </SortTh>
                  <th className="py-2 pl-3 pr-5 text-right font-medium">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {t.rows.map((r) => (
                  <tr key={r.link_id} id={`link-${r.link_id}`} className={`align-top ${r.link_id === focus ? 'bg-accent/5' : ''}`} data-testid="traffic-history-row">
                    <td className="whitespace-nowrap py-3 pl-5 pr-3 tabular-nums">
                      {fmtDay(r.created_at)}
                      {r.versions > 1 && <Sub>изменена {fmtDay(r.updated_at)}</Sub>}
                    </td>
                    <td className="max-w-[15rem] px-3 py-3">
                      {r.product ? (
                        <>
                          <span className="block truncate font-medium">{r.product.name ?? 'Без названия'}</span>
                          <Sub>
                            <CopyText value={r.product.offer_id} label="Скопировать артикул" inline />
                          </Sub>
                        </>
                      ) : (
                        <>
                          <span className="block truncate" title={r.source_url}>
                            {r.source_url.replace(/^https:\/\/www\./, '')}
                          </span>
                          <Sub>{settings.sections[r.section] ?? r.section}</Sub>
                        </>
                      )}
                    </td>
                    <td className="max-w-[10rem] px-3 py-3">
                      <span className="block truncate font-mono text-xs">{r.campaign_name}</span>
                      {r.substitute_code && <Sub>подменный {r.substitute_code}</Sub>}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 font-mono text-xs">
                      {r.utm_source}
                      <Sub>{r.utm_medium}</Sub>
                    </td>
                    <td className="max-w-[18rem] px-3 py-3">
                      <CopyText value={r.url} label="Скопировать ссылку" testid="traffic-history-url">
                        <span className="font-mono text-xs">{r.url}</span>
                      </CopyText>
                      {r.deeplink && (
                        <Sub>
                          <CopyText value={r.deeplink} label="Скопировать диплинк" inline>
                            {r.deeplink.replace(/^https:\/\//, '')}
                          </CopyText>
                        </Sub>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      <DeeplinkBadge row={r} />
                      {r.deeplink && !r.deleted && (
                        <div className="mt-1">
                          <button type="button" onClick={() => open(r)} className="inline-flex items-center gap-1 text-xs text-accent hover:underline" data-testid="traffic-open">
                            <ExternalLink className="h-3 w-3" aria-hidden /> Открыть — продлить
                          </button>
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap py-3 pl-3 pr-5 text-right">
                      {r.deleted ? (
                        <Button size="sm" variant="ghost" onClick={() => act(restoreLink(r.link_id), 'Ссылка возвращена')} data-testid="traffic-restore">
                          <RotateCcw className="h-4 w-4" aria-hidden /> Вернуть
                        </Button>
                      ) : (
                        <span className="inline-flex gap-0.5">
                          <IconAction label="QR-код" onClick={() => setQr(r.url)} icon={QrCode} />
                          <IconAction label="Изменить" onClick={() => setEdit(r)} icon={Pencil} testid="traffic-edit-open" />
                          <IconAction label="Удалить" onClick={() => act(deleteLink(r.link_id), 'Ссылка удалена — её можно вернуть во вкладке «Удалённые»')} icon={Trash2} testid="traffic-delete" />
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      <p className="flex items-center gap-1 text-xs text-muted">
        Диплинк перестаёт работать через 30 дней без переходов — сервис напомнит за неделю.
        <HelpTip label="Как продлить диплинк">
          Нажмите «Открыть — продлить»: ссылка откроется, отсчёт 30 дней начнётся заново. Ссылки без диплинка работают бессрочно.
          Переходы покупателей из отчётов Ozon будут продлевать срок сами, когда загрузите отчёты.
        </HelpTip>
      </p>
      {edit && (
        <EditModal
          row={edit}
          settings={settings}
          onClose={() => setEdit(null)}
          onSaved={(_, warnings) => {
            setEdit(null)
            setNote(warnings.length ? warnings.join(' ') : 'Сохранено — прежняя версия осталась в истории ссылки')
            load()
          }}
        />
      )}
      {qr && <QrModal url={qr} onClose={() => setQr(null)} />}
    </div>
  )
}

function IconAction({ label, onClick, icon: I, testid }: { label: string; onClick: () => void; icon: typeof Pencil; testid?: string }) {
  return (
    <button type="button" onClick={onClick} className="rounded-lg p-1.5 text-muted hover:bg-soft hover:text-fg" aria-label={label} title={label} data-testid={testid}>
      <I className="h-4 w-4" aria-hidden />
    </button>
  )
}

export { fmtDay }
