'use client'

// «Продвижение → Реферальная программа» (шаг 11.7, решение владельца 27.11.2026): тем, кто оставил отзыв, — личная
// ссылка с UTM-метками и кодом участника. Отзывы через API без подписки не видны, а первым при FBO в чате Ozon не
// написать — участника добавляет продавец, ссылку отправляет сам и отмечает «Отправлено». Покупки друзей — 11.8,
// награды промокодами — 11.9.
import { Check, FileSpreadsheet, History, Pencil, Plus, QrCode, RotateCcw, Send, Trash2, Users, X } from 'lucide-react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { CopyText } from '@/components/copy'
import { DateRangePicker, FilterButton, FilterField, SearchBox, lastDays, selectClass } from '@/components/filters'
import { Modal, input } from '@/components/planning/ui'
import { SortTh, useSort } from '@/components/table'
import { Field, PrefixForm, QrModal } from '@/components/traffic'
import { Badge, Button, EmptyState, HelpTip, PageHeader, Panel, QuickTabs, Segmented, Skeleton, Sub, buttonClass } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { type Range, todayMsk } from '@/lib/period'
import {
  PROGRAM_WHY,
  type Participant,
  type ParticipantEvent,
  type ParticipantFilters,
  type ParticipantList,
  type ParticipantTab,
  type ReferralSettings,
  type SentChannel,
  type Target,
  createParticipant,
  exportParticipants,
  getReferralSettings,
  listParticipants,
  markSent,
  participantEvents,
  removeParticipant,
  restoreParticipant,
  saveReferralSettings,
  unmarkSent,
  updateParticipant,
} from '@/lib/referral'
import { type ProductOption, type TrafficSettings, getTrafficSettings, searchTrafficProducts } from '@/lib/traffic'

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

// ---------- настройки программы ----------

function ProgramPanel({ settings, onSaved }: { settings: ReferralSettings; onSaved: (s: ReferralSettings) => void }) {
  const p = settings.program
  const [open, setOpen] = useState(false)
  const [f, setF] = useState({ campaign_name: p.campaign_name, utm_source: p.utm_source, utm_medium: p.utm_medium, store_url: p.store_url ?? '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const s = await saveReferralSettings({ ...f, store_url: f.store_url.trim() || null })
      onSaved(s)
      setOpen(false)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Panel
      testid="referral-program"
      title="Метки ссылок программы"
      description={
        <>
          utm_campaign <span className="break-all font-mono text-fg">{settings.campaign}</span> · {p.utm_source} / {p.utm_medium} ·{' '}
          {p.store_url ? (
            <>
              магазин <span className="break-all">{p.store_url.replace(/^https:\/\/www\./, '')}</span>
            </>
          ) : (
            'ссылка на магазин не указана'
          )}
        </>
      }
      action={
        <Button size="sm" variant="ghost" onClick={() => setOpen((v) => !v)} data-testid="referral-program-edit">
          {open ? 'Скрыть' : 'Изменить'}
        </Button>
      }
    >
      {open ? (
        <form onSubmit={save} className="space-y-4" data-testid="referral-program-form">
          <p className="text-xs text-muted">Новые метки — только для новых участников: отправленные ссылки не меняются.</p>
          <Field label="Название кампании" htmlFor="ref-campaign" required help={PROGRAM_WHY.campaign} hint={`utm_campaign: ${settings.utm_prefix}_${f.campaign_name.trim().toLowerCase() || '…'}`}>
            <input id="ref-campaign" value={f.campaign_name} onChange={(e) => setF({ ...f, campaign_name: e.target.value })} className={input} autoComplete="off" spellCheck={false} data-testid="ref-campaign" />
          </Field>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Источник" htmlFor="ref-source" required hint="utm_source" help={PROGRAM_WHY.labels}>
              <input id="ref-source" value={f.utm_source} onChange={(e) => setF({ ...f, utm_source: e.target.value })} className={input} autoComplete="off" spellCheck={false} />
            </Field>
            <Field label="Тип трафика" htmlFor="ref-medium" required hint="utm_medium" help={PROGRAM_WHY.labels}>
              <input id="ref-medium" value={f.utm_medium} onChange={(e) => setF({ ...f, utm_medium: e.target.value })} className={input} autoComplete="off" spellCheck={false} />
            </Field>
          </div>
          <Field label="Ссылка на магазин в Ozon" htmlFor="ref-store" help={PROGRAM_WHY.store} hint="Например https://www.ozon.ru/seller/имя/ — необязательно">
            <input id="ref-store" value={f.store_url} onChange={(e) => setF({ ...f, store_url: e.target.value })} placeholder="https://www.ozon.ru/seller/…" className={`${input} font-mono text-xs`} autoComplete="off" spellCheck={false} data-testid="ref-store" />
          </Field>
          {error && (
            <p className="text-sm text-bad" role="alert">
              {error}
            </p>
          )}
          <Button type="submit" variant="primary" disabled={busy} data-testid="referral-program-save">
            Сохранить
          </Button>
        </form>
      ) : (
        <p className="text-sm text-muted">
          У всех участников — одни метки, а код участника — свой, в метке utm_content. По нему в отчёте Ozon «по заказам» будет видно, чьи друзья купили.
        </p>
      )}
    </Panel>
  )
}

// ---------- добавить участника ----------

function ProductSelect({ value, onChange }: { value: ProductOption | null; onChange: (p: ProductOption | null) => void }) {
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 250)
  const [found, setFound] = useState<ProductOption[] | null>(null)
  useEffect(() => {
    if (value) return
    let alive = true
    searchTrafficProducts(q).then((l) => alive && setFound(l), () => alive && setFound([]))
    return () => {
      alive = false
    }
  }, [q, value])
  if (value)
    return (
      <div className="flex items-center gap-2 rounded-lg border border-line px-3 py-2" data-testid="ref-product-chosen">
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm">{value.name ?? 'Без названия'}</span>
          <span className="block truncate text-xs text-faint">
            {value.offer_id}
            {value.sku ? ` · SKU ${value.sku}` : ' · нет SKU Ozon'}
          </span>
        </span>
        <button type="button" onClick={() => onChange(null)} className="rounded p-1 text-muted hover:text-fg" aria-label="Выбрать другой товар">
          <X className="h-4 w-4" aria-hidden />
        </button>
      </div>
    )
  return (
    <div className="space-y-2">
      <SearchBox value={search} onChange={setSearch} placeholder="Артикул, название или SKU" testid="ref-product-search" />
      {!found && <Skeleton className="h-24" />}
      {found && found.length === 0 && <p className="text-sm text-muted">Ничего не нашлось</p>}
      {found && found.length > 0 && (
        <ul className="max-h-48 divide-y divide-line overflow-y-auto rounded-lg border border-line" data-testid="ref-product-list">
          {found.map((p) => (
            <li key={p.id}>
              <button type="button" onClick={() => onChange(p)} className="block w-full px-3 py-2 text-left hover:bg-soft" data-testid={`ref-product-${p.offer_id}`}>
                <span className="block truncate text-sm">{p.name ?? 'Без названия'}</span>
                <span className="block truncate text-xs text-faint">
                  {p.offer_id}
                  {p.sku ? ` · SKU ${p.sku}` : ' · нет SKU Ozon'}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function AddModal({ settings, onClose, onDone }: { settings: ReferralSettings; onClose: () => void; onDone: (p: Participant, again: boolean) => void }) {
  const [product, setProduct] = useState<ProductOption | null>(null)
  const [name, setName] = useState('')
  const [reviewDate, setReviewDate] = useState('')
  const [note, setNote] = useState('')
  const [target, setTarget] = useState<Target>('product')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [made, setMade] = useState<Participant | null>(null)
  const noStore = !settings.program.store_url

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      setMade(await createParticipant({ name, product_id: product?.id ?? null, review_date: reviewDate || null, note: note || null, target }))
    } catch (err) {
      setError(errorMessage(err, 'Не получилось добавить — попробуйте ещё раз'))
    } finally {
      setBusy(false)
    }
  }

  if (made)
    return (
      <Modal title="Ссылка готова" onClose={() => onDone(made, false)}>
        <div className="space-y-4" data-testid="ref-made">
          <p className="text-sm">
            Личная ссылка для <span className="font-medium">{made.name}</span> · код <span className="font-mono">{made.code}</span>. Скопируйте её и отправьте человеку, потом
            отметьте «Отправлено».
          </p>
          <div className="rounded-lg border border-line bg-soft/60 p-3">
            <CopyText value={made.url} label="Скопировать ссылку" testid="ref-made-url">
              <span className="break-all font-mono text-xs">{made.url}</span>
            </CopyText>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => onDone(made, false)} data-testid="ref-made-done">
              Готово
            </Button>
            <Button onClick={() => onDone(made, true)} data-testid="ref-made-again">
              <Plus className="h-4 w-4" aria-hidden /> Добавить ещё
            </Button>
          </div>
        </div>
      </Modal>
    )

  return (
    <Modal title="Добавить участника" onClose={onClose}>
      <form onSubmit={save} className="space-y-4" data-testid="ref-add">
        <p className="text-xs text-muted">Тот, кто оставил отзыв. Сервис сразу создаст его личную ссылку.</p>
        <Field label="Товар из отзыва" htmlFor="ref-product-search" help="По товару ссылка поведёт на его страницу; в учёте будет видно, за какой отзыв участник в программе.">
          <ProductSelect value={product} onChange={setProduct} />
        </Field>
        <Field label="Имя из отзыва" htmlFor="ref-name" required help="Как подписан отзыв — чтобы потом узнать человека. Больше ничего о покупателе сервис не хранит.">
          <input id="ref-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Например Анна К." className={input} autoComplete="off" data-testid="ref-name" />
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Дата отзыва" htmlFor="ref-date" hint="Необязательно">
            <input id="ref-date" type="date" value={reviewDate} max={todayMsk()} onChange={(e) => setReviewDate(e.target.value)} className={input} data-testid="ref-date" />
          </Field>
          <Field label="Куда ведёт ссылка" htmlFor="ref-target" hint={noStore ? 'Ссылку на магазин укажите в «Метках ссылок программы»' : undefined}>
            <Segmented
              value={target}
              onChange={setTarget}
              label="Куда ведёт ссылка"
              testid="ref-target"
              optionTestid={(v) => `ref-target-${v}`}
              options={[
                { value: 'product', label: 'Товар' },
                { value: 'store', label: 'Магазин' },
              ]}
            />
          </Field>
        </div>
        <Field label="Заметка" htmlFor="ref-note" hint="Необязательно: о чём отзыв, где нашли человека">
          <textarea id="ref-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={input} data-testid="ref-note" />
        </Field>
        {error && (
          <p className="text-sm text-bad" role="alert" data-testid="ref-add-error">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy || !name.trim()} data-testid="ref-add-save">
            Создать ссылку
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  )
}

// ---------- отметка «отправлено», правка ----------

function SentModal({ row, settings, onClose, onDone }: { row: Participant; settings: ReferralSettings; onClose: () => void; onDone: () => void }) {
  const [channel, setChannel] = useState<SentChannel>('ozon_chat')
  const [day, setDay] = useState(todayMsk())
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await markSent(row.id, { channel, sent_on: day || null, note: note || null })
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Modal title={`Ссылка отправлена: ${row.name}`} onClose={onClose}>
      <form onSubmit={save} className="space-y-4" data-testid="ref-sent">
        <Field label="Где отправили" htmlFor="ref-channel" help="Чтобы потом помнить, где искать переписку с человеком.">
          <select id="ref-channel" value={channel} onChange={(e) => setChannel(e.target.value as SentChannel)} className={selectClass} data-testid="ref-channel">
            {Object.entries(settings.channels).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Когда" htmlFor="ref-sent-on">
          <input id="ref-sent-on" type="date" value={day} max={todayMsk()} onChange={(e) => setDay(e.target.value)} className={`${input} sm:max-w-[12rem]`} />
        </Field>
        <Field label="Заметка" htmlFor="ref-sent-note" hint="Необязательно">
          <input id="ref-sent-note" value={note} onChange={(e) => setNote(e.target.value)} className={input} autoComplete="off" />
        </Field>
        {error && (
          <p className="text-sm text-bad" role="alert">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" disabled={busy} data-testid="ref-sent-save">
            <Check className="h-4 w-4" aria-hidden /> Отметить отправленной
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  )
}

function Events({ id }: { id: string }) {
  const [list, setList] = useState<ParticipantEvent[] | null>(null)
  useEffect(() => {
    participantEvents(id).then(setList, () => setList([]))
  }, [id])
  if (!list) return <Skeleton className="h-16" />
  return (
    <ol className="space-y-1.5 text-sm" data-testid="ref-events">
      {list.map((e, i) => (
        <li key={i} className="flex flex-wrap gap-x-2">
          <span className="text-xs tabular-nums text-faint">{fmtTime(e.created_at)}</span>
          <span>{e.title}</span>
          {e.channel && <span className="text-muted">· {e.channel}</span>}
          {e.sent_on && <span className="text-muted">· {fmtDay(e.sent_on)}</span>}
          {e.note && <span className="text-muted">· {e.note}</span>}
        </li>
      ))}
    </ol>
  )
}

function EditModal({ row, onClose, onDone }: { row: Participant; onClose: () => void; onDone: () => void }) {
  const [name, setName] = useState(row.name)
  const [day, setDay] = useState(row.review_date ?? '')
  const [note, setNote] = useState(row.note ?? '')
  const [error, setError] = useState<string | null>(null)
  async function save(e: React.FormEvent) {
    e.preventDefault()
    try {
      await updateParticipant(row.id, { name, review_date: day || null, note })
      onDone()
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  return (
    <Modal title="Участник" onClose={onClose}>
      <form onSubmit={save} className="space-y-4" data-testid="ref-edit">
        <div className="rounded-lg border border-line bg-soft/60 p-3 text-xs">
          <CopyText value={row.url} label="Скопировать ссылку">
            <span className="break-all font-mono">{row.url}</span>
          </CopyText>
          <p className="mt-1 text-muted">Код {row.code} · ссылка не меняется — её уже могли отправить</p>
        </div>
        <Field label="Имя из отзыва" htmlFor="ref-edit-name" required>
          <input id="ref-edit-name" value={name} onChange={(e) => setName(e.target.value)} className={input} autoComplete="off" data-testid="ref-edit-name" />
        </Field>
        <Field label="Дата отзыва" htmlFor="ref-edit-date">
          <input id="ref-edit-date" type="date" value={day} max={todayMsk()} onChange={(e) => setDay(e.target.value)} className={`${input} sm:max-w-[12rem]`} />
        </Field>
        <Field label="Заметка" htmlFor="ref-edit-note">
          <textarea id="ref-edit-note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} className={input} />
        </Field>
        {error && (
          <p className="text-sm text-bad" role="alert">
            {error}
          </p>
        )}
        <div className="flex flex-wrap gap-2">
          <Button type="submit" variant="primary" data-testid="ref-edit-save">
            Сохранить
          </Button>
          <Button type="button" onClick={onClose}>
            Отмена
          </Button>
        </div>
        <div className="border-t border-line pt-3">
          <p className="mb-2 flex items-center gap-1.5 text-sm font-medium">
            <History className="h-4 w-4" aria-hidden /> Что происходило
          </p>
          <Events id={row.id} />
        </div>
      </form>
    </Modal>
  )
}

// ---------- страница ----------

const TABS = (c: ParticipantList['counts'] | undefined): { value: ParticipantTab; label: string; count?: number; tone?: 'warn' }[] => [
  { value: 'all', label: 'Все', count: c?.all },
  { value: 'new', label: 'Не отправлены', count: c?.new, tone: 'warn' },
  { value: 'sent', label: 'Отправлены', count: c?.sent },
  { value: 'deleted', label: 'Убранные', count: c?.deleted },
]

function IconAction({ label, onClick, icon: I, testid }: { label: string; onClick: () => void; icon: typeof Pencil; testid?: string }) {
  return (
    <button type="button" onClick={onClick} className="rounded-lg p-1.5 text-muted hover:bg-soft hover:text-fg" aria-label={label} title={label} data-testid={testid}>
      <I className="h-4 w-4" aria-hidden />
    </button>
  )
}

export function ReferralPage() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const focus = params.get('participant')
  const [settings, setSettings] = useState<ReferralSettings | null>(null)
  const [traffic, setTraffic] = useState<TrafficSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tab, setTab] = useState<ParticipantTab>(params.get('tab') === 'deleted' ? 'deleted' : 'all')
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 250)
  const [range, setRange] = useState<Range | null>(null)
  const [preset, setPreset] = useState<string | null>('all')
  const [channel, setChannel] = useState('')
  const [data, setData] = useState<ParticipantList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [sending, setSending] = useState<Participant | null>(null)
  const [editing, setEditing] = useState<Participant | null>(null)
  const [qr, setQr] = useState<string | null>(null)

  const loadSettings = useCallback(() => {
    Promise.all([getReferralSettings(), getTrafficSettings()]).then(
      ([s, t]) => (setSettings(s), setTraffic(t)),
      (err) => setLoadError(errorMessage(err)),
    )
  }, [])
  useEffect(loadSettings, [loadSettings])

  const filters: ParticipantFilters = useMemo(
    () => ({ tab, search: q.trim() || undefined, date_from: range?.from, date_to: range?.to, channel: channel || undefined }),
    [tab, q, range, channel],
  )
  const load = useCallback(() => {
    listParticipants(filters).then(
      (d) => (setData(d), setError(null)),
      (err) => setError(errorMessage(err)),
    )
  }, [filters])
  useEffect(load, [load])

  // пришли из «Истории событий» (?participant=) — открыть участника
  useEffect(() => {
    if (!focus || !data) return
    const row = data.items.find((r) => r.id === focus)
    if (row) setEditing(row)
    // только при первом появлении данных
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, data === null])

  const by = useMemo(
    () => ({
      created: (r: Participant) => r.created_at,
      name: (r: Participant) => r.name,
      review: (r: Participant) => r.review_date,
      status: (r: Participant) => (r.sent ? r.sent.sent_on : null),
    }),
    [],
  )
  const t = useSort(data?.items, by)
  const act = (p: Promise<unknown>, done: string) =>
    p.then(
      () => (setNote(done), load()),
      (err) => setNote(errorMessage(err)),
    )
  const closeFocus = () => focus && router.replace(pathname, { scroll: false })

  const header = (
    <PageHeader
      title="Реферальная программа"
      titleTestid="referral-title"
      description="Личные ссылки тем, кто оставил отзыв: друзья купили по ссылке — человеку промокод"
      actions={
        settings?.utm_prefix && (
          <>
            <HelpTip label="Как это работает">
              1. Добавьте того, кто оставил отзыв, — сервис создаст его личную ссылку (код участника — в метке utm_content). 2. Скопируйте
              ссылку и отправьте человеку, отметьте «Отправлено». 3. Загрузите отчёт Ozon «по заказам» — сервис найдёт покупки друзей по коду
              и засчитает только выкупленные (скоро). 4. За покупки друзей — промокод со скидкой, которая не уводит прибыль в минус (скоро).
            </HelpTip>
            <Button variant="primary" onClick={() => setAdding(true)} data-testid="referral-add">
              <Plus className="h-4 w-4" aria-hidden /> Добавить участника
            </Button>
          </>
        )
      }
    />
  )

  if (loadError)
    return (
      <div>
        {header}
        <EmptyState title="Не загрузилось">{loadError}</EmptyState>
      </div>
    )
  if (!settings || !traffic)
    return (
      <div>
        {header}
        <Skeleton className="h-64" />
      </div>
    )
  if (!settings.utm_prefix)
    return (
      <div>
        {header}
        <Panel title="Сначала — префикс магазина" description="Ozon узнаёт ссылки программы по префиксу в метке utm_campaign — без него покупки друзей не попадут в отчёты." testid="referral-need-prefix" className="max-w-2xl">
          <PrefixForm settings={traffic} onSaved={() => loadSettings()} compact />
        </Panel>
      </div>
    )

  const today = todayMsk()
  const all = { from: today, to: today }
  return (
    <div className="space-y-5">
      {header}
      <ProgramPanel settings={settings} onSaved={setSettings} />
      <QuickTabs value={tab} onChange={setTab} items={TABS(data?.counts)} testid="referral-tabs" itemTestid={(v) => `referral-tab-${v}`} label="Участники" />
      <Panel flush testid="referral-panel">
        <div className="flex flex-wrap items-center gap-2 px-5 pb-3 pt-4">
          <SearchBox value={search} onChange={setSearch} placeholder="Имя, код, артикул или заметка" testid="referral-search" className="min-w-0 flex-1 sm:max-w-xs" />
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
            testid="referral-period"
          />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            <FilterButton count={channel ? 1 : 0} onReset={() => setChannel('')} testid="referral-filters">
              <FilterField label="Где отправлена ссылка">
                <select value={channel} onChange={(e) => setChannel(e.target.value)} className={selectClass} data-testid="referral-filter-channel">
                  <option value="">Любой канал</option>
                  {Object.entries(settings.channels).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </FilterField>
            </FilterButton>
            <Button size="sm" onClick={() => exportParticipants(filters).catch((err) => setNote(errorMessage(err)))} disabled={!data?.total} data-testid="referral-xlsx">
              <FileSpreadsheet className="h-4 w-4" aria-hidden /> Excel
            </Button>
          </div>
        </div>
        {note && (
          <p className="px-5 pb-3 text-sm text-muted" role="status" data-testid="referral-note">
            {note}
          </p>
        )}
        {error && <p className="px-5 pb-4 text-sm text-bad">{error}</p>}
        {!data && !error && <Skeleton className="mx-5 mb-5 h-40" />}
        {data && data.total === 0 && (
          <EmptyState
            icon={Users}
            title={data.counts.all || data.counts.deleted ? 'Ничего не нашлось' : 'Пока нет участников'}
            testid="referral-empty"
            action={
              !(data.counts.all || data.counts.deleted) && (
                <Button variant="primary" onClick={() => setAdding(true)}>
                  <Plus className="h-4 w-4" aria-hidden /> Добавить участника
                </Button>
              )
            }
          >
            {data.counts.all || data.counts.deleted
              ? 'Поменяйте поиск, период, вкладку или фильтры.'
              : 'Добавьте того, кто оставил отзыв: сервис создаст его личную ссылку, вы отправите её человеку.'}
          </EmptyState>
        )}
        {data && data.total > 0 && (
          <div className="relative overflow-x-auto">
            <table className="w-full min-w-[820px] text-sm" data-testid="referral-table">
              <thead className="bg-soft/60 text-xs text-muted">
                <tr>
                  <SortTh {...t.th('name')} kind="text" className="py-2 pl-5 pr-3 font-medium">
                    Участник
                  </SortTh>
                  <SortTh {...t.th('review')} kind="date" className="px-3 py-2 font-medium">
                    Отзыв
                  </SortTh>
                  <th className="px-3 py-2 text-left font-medium">Личная ссылка</th>
                  <SortTh {...t.th('status')} kind="date" className="px-3 py-2 font-medium" title="По дате отправки">
                    Статус
                  </SortTh>
                  <SortTh {...t.th('created')} kind="date" className="px-3 py-2 font-medium">
                    Добавлен
                  </SortTh>
                  <th className="py-2 pl-3 pr-5 text-right font-medium">
                    <span className="sr-only">Действия</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {t.rows.map((r) => (
                  <tr key={r.id} className={`align-top ${r.id === focus ? 'bg-accent/5' : ''}`} data-testid="referral-row">
                    <td className="max-w-[15rem] py-3 pl-5 pr-3">
                      <span className="block truncate font-medium">{r.name}</span>
                      <Sub>
                        {r.product ? (
                          <Link href={`/dashboard/products/${r.product.id}`} className="hover:text-accent">
                            {r.product.offer_id}
                          </Link>
                        ) : (
                          'без товара'
                        )}
                        {r.note && ` · ${r.note}`}
                      </Sub>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 tabular-nums">{r.review_date ? fmtDay(r.review_date) : <span className="text-faint">—</span>}</td>
                    <td className="max-w-[18rem] px-3 py-3">
                      <CopyText value={r.url} label="Скопировать ссылку" testid="referral-url">
                        <span className="font-mono text-xs">{r.url}</span>
                      </CopyText>
                      <Sub>
                        код {r.code} · {r.target === 'store' ? 'на магазин' : 'на товар'}
                      </Sub>
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      {r.deleted ? (
                        <Badge tone="muted">Убран</Badge>
                      ) : r.sent ? (
                        <>
                          <Badge tone="ok" dot>
                            Отправлена
                          </Badge>
                          <Sub>
                            {fmtDay(r.sent.sent_on)} · {r.sent.channel_title}
                          </Sub>
                        </>
                      ) : (
                        <Badge tone="warn" dot>
                          Не отправлена
                        </Badge>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 tabular-nums">{fmtDay(r.created_at)}</td>
                    <td className="whitespace-nowrap py-3 pl-3 pr-5 text-right">
                      {r.deleted ? (
                        <Button size="sm" variant="ghost" onClick={() => act(restoreParticipant(r.id), `${r.name} — снова в программе`)} data-testid="referral-restore">
                          <RotateCcw className="h-4 w-4" aria-hidden /> Вернуть
                        </Button>
                      ) : (
                        <span className="inline-flex items-center gap-0.5">
                          {r.sent ? (
                            <button type="button" onClick={() => act(unmarkSent(r.id), 'Отметка «отправлено» снята')} className="mr-1 text-xs text-muted hover:text-fg hover:underline" data-testid="referral-unsent">
                              Снять отметку
                            </button>
                          ) : (
                            <button type="button" onClick={() => setSending(r)} className={`${buttonClass('secondary', 'sm')} mr-1`} data-testid="referral-sent">
                              <Send className="h-3.5 w-3.5" aria-hidden /> Отправлено
                            </button>
                          )}
                          <IconAction label="QR-код" onClick={() => setQr(r.url)} icon={QrCode} />
                          <IconAction label="Изменить" onClick={() => setEditing(r)} icon={Pencil} testid="referral-edit" />
                          <IconAction label="Убрать из программы" onClick={() => act(removeParticipant(r.id), `${r.name} убран — вернуть можно во вкладке «Убранные»`)} icon={Trash2} testid="referral-delete" />
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
      <p className="text-xs text-muted">
        Покупки друзей появятся после загрузки отчёта Ozon «по заказам», награды промокодами — следующим шагом. Ссылки участников видны и в{' '}
        <Link href="/dashboard/advertising/links?tab=history" className="text-accent hover:underline">
          «Созданных ссылках»
        </Link>
        .
      </p>
      {adding && (
        <AddModal
          settings={settings}
          onClose={() => setAdding(false)}
          onDone={(p, again) => {
            setAdding(false)
            setNote(`${p.name} добавлен — ссылка создана`)
            load()
            if (again) setTimeout(() => setAdding(true), 0)
          }}
        />
      )}
      {sending && (
        <SentModal
          row={sending}
          settings={settings}
          onClose={() => setSending(null)}
          onDone={() => {
            setNote(`Ссылка для ${sending.name} отмечена отправленной`)
            setSending(null)
            load()
          }}
        />
      )}
      {editing && (
        <EditModal
          row={editing}
          onClose={() => (setEditing(null), closeFocus())}
          onDone={() => {
            setEditing(null)
            closeFocus()
            setNote('Сохранено')
            load()
          }}
        />
      )}
      {qr && <QrModal url={qr} onClose={() => setQr(null)} />}
    </div>
  )
}
