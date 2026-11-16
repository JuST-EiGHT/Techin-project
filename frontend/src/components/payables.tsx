'use client'
// «Финансы → Долги поставщикам» (шаг 10.23a): поставки от поставщика, что из них продано на Ozon и сколько за это
// заплатить. Только при «Откуда товар» = «Поставщик получает деньги после продажи». Продажи относятся к поставкам сами —
// FIFO внутри артикула; возврат после получения — к поставке своего отправления; оплаты закрывают старые поставки первыми.
import { Download, FileUp, Plus } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { CopyText } from '@/components/copy'
import { FormError } from '@/components/AuthForm'
import { Modal, input } from '@/components/planning/ui'
import { fmtDay, rub } from '@/components/reports'
import { Badge, Button, EmptyState, HelpTip, PageHeader, Panel, QuickTabs, Skeleton, Sub, Tabs, buttonClass } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { todayMsk } from '@/lib/period'
import { isPostpay } from '@/lib/nav'
import { useSettings } from '@/lib/settings'
import { SortTh, num, useSort } from '@/components/table'

// ---------- типы ответа /finance/payables ----------

type Status = 'selling' | 'awaiting' | 'done' | 'closed'

type Summary = {
  to_pay: string
  accrued: string
  paid_total: string
  paid_30d: string
  prepaid: string
  stock_qty: number
  stock_value: string
  stock_unknown_qty: number
  unknown_price_qty: number
  lots_total: number
  lots_selling: number
  lots_awaiting: number
  lots_done: number
  lots_closed: number
  unallocated_qty: number
}

type LotLine = {
  article: string
  name: string | null
  barcode: string | null
  offer: string | null
  in_catalog: boolean
  qty: number
  sold_before: number
  sold: number
  returned: number
  written_off: number
  remaining: number
  price: string | null
  amount: string | null
}

type Lot = {
  lot_id: string
  version: number
  number: string
  day: string
  supplier: string | null
  receiver: string | null
  warehouse: string | null
  source: string
  doc_total_qty: number | null
  has_document: boolean
  comment: string | null
  closed_on: string | null
  close_reason: string | null
  status: Status
  qty: number
  sold: number
  remaining: number
  debt: string
  paid: string
  unpaid: string
  unknown_price_qty: number
  stock_value: string
  lines: LotLine[]
  moves: { day: string; article: string; kind: 'sale' | 'return'; qty: number }[]
}

type Payment = {
  payment_id: string
  day: string
  amount: string
  counterparty: string | null
  comment: string | null
  source: 'manual' | 'operation'
  editable: boolean
  version: number
}

export type Payables = {
  today: string
  configured: boolean
  summary: Summary
  warnings: string[]
  sales_known_from: string | null
  operations_counted: boolean
  lots: Lot[]
  payments: Payment[]
  unallocated: { article: string; qty: number }[]
}

type PreviewLine = {
  line: number
  article: string
  name: string | null
  qty: number | null
  sold_before: number
  offer: string | null
  in_catalog: boolean
  price: string | null
  amount: string | null
  error: string | null
}

type PreviewDoc = {
  number: string | null
  day: string | null
  supplier: string | null
  receiver: string | null
  warehouse: string | null
  total_qty: number | null
  lines_qty: number
  status: 'new' | 'changed' | 'unchanged' | 'error'
  errors: string[]
  warnings: string[]
  lines: PreviewLine[]
  amount: string
  no_price: number
  not_in_catalog: number
  older_than_sales: boolean
}

type UploadResult = {
  applied: boolean
  format: string
  counts: Record<string, number>
  docs: PreviewDoc[]
  sales_known_from: string | null
}

const STATUS: Record<Status, { label: string; tone: 'accent' | 'warn' | 'ok' | 'muted' }> = {
  selling: { label: 'В продаже', tone: 'accent' },
  awaiting: { label: 'Ждёт оплаты', tone: 'warn' },
  done: { label: 'Оплачена', tone: 'ok' },
  closed: { label: 'Закрыта', tone: 'muted' },
}
const CLOSE_REASONS = [
  { value: 'returned', label: 'Вернули поставщику' },
  { value: 'written_off', label: 'Списали' },
  { value: 'other', label: 'Другое' },
] as const
const DOC_STATUS: Record<PreviewDoc['status'], { label: string; tone: 'accent' | 'warn' | 'muted' | 'bad' }> = {
  new: { label: 'Новая поставка', tone: 'accent' },
  changed: { label: 'Состав изменился — новая версия', tone: 'warn' },
  unchanged: { label: 'Уже загружена, без изменений', tone: 'muted' },
  error: { label: 'Есть ошибки — не сохранится', tone: 'bad' },
}

const pcs = (n: number) => `${n.toLocaleString('ru-RU')} шт.`
const lotTitle = (l: { number: string | null; day: string | null }) => `№ ${l.number ?? '—'} от ${fmtDay(l.day)}`

// ---------- страница ----------

export function PayablesPage() {
  const settings = useSettings()
  const allowed = settings === null ? null : isPostpay(settings.supplier_payment)
  const [data, setData] = useState<Payables | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [tab, setTab] = useState<'lots' | 'payments'>('lots')
  const [uploading, setUploading] = useState(false)
  const [paying, setPaying] = useState<Payment | 'new' | null>(null)
  const [openLot, setOpenLot] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Payables>('/finance/payables')
      setData(data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])
  useEffect(() => {
    if (allowed) load()
  }, [allowed, load])

  if (allowed === false) {
    return (
      <div className="mx-auto max-w-5xl">
        <PageHeader title="Долги поставщикам" />
        <Panel testid="payables-closed">
          <EmptyState title="Раздел — для тех, кто платит поставщику после продажи">
            Сервис ведёт поставки от поставщика, сам отмечает, что из них продано на Ozon, и считает, сколько за это нужно
            заплатить. Если вы платите поставщику после продажи, выберите этот вариант в настройках «Откуда товар».
          </EmptyState>
          <div className="flex justify-center pb-4">
            <Link href="/dashboard/settings" className={buttonClass('primary')}>
              Открыть настройки
            </Link>
          </div>
        </Panel>
      </div>
    )
  }

  const lot = data?.lots.find((l) => l.lot_id === openLot) ?? null
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <PageHeader
        title="Долги поставщикам"
        description="Поставки от поставщика: что из них продано на Ozon и сколько за это заплатить"
        actions={
          <>
            <Button variant="secondary" onClick={() => setPaying('new')} data-testid="payment-add">
              <Plus className="h-4 w-4" /> Внести оплату
            </Button>
            <Button variant="primary" onClick={() => setUploading(true)} data-testid="lot-upload">
              <FileUp className="h-4 w-4" /> Загрузить поставку
            </Button>
          </>
        }
      />
      <FormError message={error} />
      {notice && (
        <p className="rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="payables-notice">
          {notice}
        </p>
      )}
      {!data && !error && <Skeleton className="h-28" />}
      {data && (
        <>
          <SummaryPanel data={data} />
          <WarningLines items={data.warnings} />
          {!data.configured ? (
            <Panel testid="payables-empty">
              <EmptyState title="Загрузите первую поставку">
                Документ поставщика в PDF — сборочный лист или накладная: из него возьмём № и дату, артикулы и количество.
                Цена — из справочника себестоимости на дату поставки. Дальше сервис сам отмечает, что продано на Ozon.
              </EmptyState>
              <div className="flex justify-center pb-4">
                <Button variant="primary" onClick={() => setUploading(true)}>
                  <FileUp className="h-4 w-4" /> Загрузить поставку
                </Button>
              </div>
            </Panel>
          ) : (
            <>
              <Tabs
                value={tab}
                onChange={setTab}
                label="Поставки и оплаты"
                testid="payables-tabs"
                items={[
                  { value: 'lots', label: 'Поставки', count: data.lots.length },
                  { value: 'payments', label: 'Оплаты', count: data.payments.length },
                ]}
              />
              {tab === 'lots' ? (
                <LotsTable lots={data.lots} onOpen={setOpenLot} />
              ) : (
                <PaymentsTable data={data} onEdit={setPaying} onChanged={load} />
              )}
            </>
          )}
          <TemplatePanel onUpload={() => setUploading(true)} />
        </>
      )}

      {uploading && (
        <UploadModal
          onClose={() => setUploading(false)}
          onSaved={(text) => {
            setUploading(false)
            setNotice(text)
            setTab('lots')
            load()
          }}
        />
      )}
      {paying && (
        <PaymentModal
          payment={paying === 'new' ? null : paying}
          onClose={() => setPaying(null)}
          onSaved={() => {
            setPaying(null)
            setTab('payments')
            load()
          }}
        />
      )}
      {lot && data && (
        <LotModal
          lot={lot}
          salesFrom={data.sales_known_from}
          onClose={() => setOpenLot(null)}
          onChanged={(next) => (next ? setData(next) : load())}
          onDeleted={() => {
            setOpenLot(null)
            load()
          }}
        />
      )}
    </div>
  )
}

function SummaryPanel({ data }: { data: Payables }) {
  const s = data.summary
  const toPay = Number(s.to_pay)
  return (
    <section className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line shadow-card lg:grid-cols-4" data-testid="payables-summary">
      <Figure label="К оплате за проданное" value={rub(s.to_pay)} tone={toPay > 0 ? 'warn' : undefined} testid="sum-to-pay">
        {s.unknown_price_qty > 0 ? <Sub tone="warn">ещё {pcs(s.unknown_price_qty)} без цены</Sub> : <Sub>продано, не оплачено</Sub>}
      </Figure>
      <Figure label="Оплачено за 30 дней" value={rub(s.paid_30d)} testid="sum-paid">
        <Sub>{data.operations_counted ? 'вместе с «Операциями»' : 'внесено здесь'}</Sub>
      </Figure>
      <Figure label="Ещё не продано" value={rub(s.stock_value)} testid="sum-stock">
        <Sub>
          {pcs(s.stock_qty)} в поставках{s.stock_unknown_qty > 0 ? `, ${s.stock_unknown_qty} без цены` : ''}
        </Sub>
      </Figure>
      {Number(s.prepaid) > 0 ? (
        <Figure label="Аванс поставщику" value={rub(s.prepaid)} tone="ok" testid="sum-prepaid">
          <Sub>оплачено больше, чем продано</Sub>
        </Figure>
      ) : (
        <Figure label="Поставок в работе" value={String(s.lots_selling + s.lots_awaiting)} testid="sum-lots">
          <Sub>
            ждут оплаты: {s.lots_awaiting}, закрыто: {s.lots_done + s.lots_closed}
          </Sub>
        </Figure>
      )}
    </section>
  )
}

function Figure({ label, value, tone, testid, children }: { label: string; value: string; tone?: 'ok' | 'warn' | 'bad'; testid: string; children?: React.ReactNode }) {
  const color = tone === 'ok' ? 'text-ok' : tone === 'warn' ? 'text-warn' : tone === 'bad' ? 'text-bad' : ''
  return (
    <div className="bg-card p-4" data-testid={testid}>
      <div className="flex items-center gap-1.5 text-sm text-muted">
        {label}
        {testid === 'sum-to-pay' && (
          <HelpTip>
            Проданное на Ozon — когда покупатель получил товар — по цене из справочника себестоимости на дату поставки, с НДС
            поставщика, минус оплаты. Невыкуп и возврат до получения продажей не считаются; возврат после получения снимает
            продажу с той поставки, откуда ушло отправление.
          </HelpTip>
        )}
      </div>
      <div className={`mt-1.5 text-xl font-semibold tabular-nums sm:text-2xl ${color}`}>{value}</div>
      {children}
    </div>
  )
}

function WarningLines({ items }: { items: string[] }) {
  if (!items.length) return null
  return (
    <ul className="space-y-1.5" data-testid="payables-warnings">
      {items.map((w) => (
        <li key={w} className="rounded-lg border border-warn/30 bg-warn/5 px-3 py-2 text-sm">
          {w}
        </li>
      ))}
    </ul>
  )
}

// ---------- поставки ----------

type LotFilter = 'all' | 'selling' | 'awaiting' | 'closed'

function LotsTable({ lots, onOpen }: { lots: Lot[]; onOpen: (id: string) => void }) {
  const [filter, setFilter] = useState<LotFilter>('all')
  const count = (f: LotFilter) => lots.filter((l) => match(l, f)).length
  const shown = lots.filter((l) => match(l, filter))
  const t = useSort(shown, {
    lot: (l) => l.day,
    status: (l) => STATUS[l.status].label,
    sold: (l) => l.sold,
    debt: (l) => num(l.debt),
    unpaid: (l) => num(l.unpaid),
  })
  return (
    <div className="space-y-3">
      <QuickTabs
        value={filter}
        onChange={setFilter}
        label="Поставки по статусу"
        testid="lot-filter"
        items={[
          { value: 'all', label: 'Все', count: lots.length },
          { value: 'selling', label: 'В продаже', count: count('selling') },
          { value: 'awaiting', label: 'Ждут оплаты', count: count('awaiting'), tone: 'warn' },
          { value: 'closed', label: 'Закрытые', count: count('closed') },
        ]}
      />
      <Panel flush testid="lots-table">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
            <thead className="bg-soft/60 text-xs font-medium text-muted">
              <tr>
                <SortTh {...t.th('lot')} kind="date" className="px-5 py-2 font-medium" title="По дате поставки">
                  Поставка
                </SortTh>
                <SortTh {...t.th('status')} kind="text" className="px-3 py-2 font-medium">
                  Статус
                </SortTh>
                <SortTh {...t.th('sold')} align="right" className="px-3 py-2 font-medium">
                  Продано
                </SortTh>
                <SortTh {...t.th('debt')} align="right" className="px-3 py-2 font-medium">
                  За проданное
                </SortTh>
                <SortTh {...t.th('unpaid')} align="right" className="px-5 py-2 font-medium">
                  К оплате
                </SortTh>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {t.rows.map((l) => (
                <tr key={l.lot_id} className="cursor-pointer hover:bg-soft/40" onClick={() => onOpen(l.lot_id)} data-testid="lot-row">
                  <td className="px-5 py-3">
                    <button type="button" className="text-left font-medium hover:text-accent" onClick={() => onOpen(l.lot_id)}>
                      {lotTitle(l)}
                    </button>
                    <Sub>{l.supplier ?? 'поставщик не указан'}</Sub>
                  </td>
                  <td className="px-3 py-3">
                    <Badge tone={STATUS[l.status].tone} dot>
                      {STATUS[l.status].label}
                    </Badge>
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums whitespace-nowrap">
                    {l.sold} из {l.qty}
                    <Sub>{l.remaining > 0 ? `осталось ${pcs(l.remaining)}` : 'всё продано'}</Sub>
                  </td>
                  <td className="px-3 py-3 text-right tabular-nums whitespace-nowrap">
                    {rub(l.debt)}
                    <Sub tone={l.unknown_price_qty > 0 ? 'warn' : undefined}>
                      {l.unknown_price_qty > 0 ? `${l.unknown_price_qty} шт. без цены` : `оплачено ${rub(l.paid)}`}
                    </Sub>
                  </td>
                  <td className={`px-5 py-3 text-right font-medium tabular-nums ${Number(l.unpaid) > 0 ? 'text-warn' : ''}`}>{rub(l.unpaid)}</td>
                </tr>
              ))}
              {shown.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-5 py-6 text-center text-sm text-muted">
                    Таких поставок нет
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  )
}

function match(l: Lot, f: LotFilter): boolean {
  if (f === 'all') return true
  if (f === 'closed') return l.status === 'closed' || l.status === 'done'
  return l.status === f
}

function LotModal({ lot, salesFrom, onClose, onChanged, onDeleted }: {
  lot: Lot
  salesFrom: string | null
  onClose: () => void
  onChanged: (next: Payables | null) => void
  onDeleted: () => void
}) {
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const [sold, setSold] = useState<Record<string, string>>({})
  const [closing, setClosing] = useState(false)
  const lines = useSort(lot.lines, {
    article: (ln) => ln.article,
    qty: (ln) => ln.qty,
    sold: (ln) => ln.sold + ln.sold_before,
    remaining: (ln) => ln.remaining,
    price: (ln) => num(ln.price),
    amount: (ln) => num(ln.amount),
  })
  const [reason, setReason] = useState<(typeof CLOSE_REASONS)[number]['value']>('returned')
  const [comment, setComment] = useState('')

  async function run(fn: () => Promise<Payables | null>) {
    setBusy(true)
    setError(null)
    try {
      onChanged(await fn())
      setEditing(false)
      setClosing(false)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const saveSold = () =>
    run(async () => {
      const body = Object.fromEntries(Object.entries(sold).map(([a, v]) => [a, Number(v || 0)]))
      return (await api.patch<Payables>(`/finance/payables/lots/${lot.lot_id}`, { sold_before: body })).data
    })

  async function remove() {
    if (!window.confirm(`Убрать поставку ${lotTitle(lot)} из учёта? Её продажи перейдут в другие поставки; в истории она останется.`)) return
    setBusy(true)
    try {
      await api.delete(`/finance/payables/lots/${lot.lot_id}`)
      onDeleted()
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  const older = !!salesFrom && lot.day < salesFrom
  return (
    <Modal title={`Поставка ${lotTitle(lot)}`} onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="lot-card">
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={STATUS[lot.status].tone} dot>
            {STATUS[lot.status].label}
          </Badge>
          {lot.supplier && <span className="text-muted">{lot.supplier}</span>}
          {lot.closed_on && (
            <span className="text-muted">
              · закрыта {fmtDay(lot.closed_on)}: {CLOSE_REASONS.find((r) => r.value === lot.close_reason)?.label.toLowerCase()}
            </span>
          )}
        </div>
        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Fact label="Продано" value={`${lot.sold} из ${lot.qty}`} />
          <Fact label="За проданное" value={rub(lot.debt)} />
          <Fact label="Оплачено" value={rub(lot.paid)} />
          <Fact label="К оплате" value={rub(lot.unpaid)} tone={Number(lot.unpaid) > 0 ? 'text-warn' : undefined} />
        </dl>
        <FormError message={error} />

        <div className="overflow-x-auto rounded-lg border border-line">
          <table className="w-full min-w-[560px] text-sm" data-testid="lot-lines">
            <thead className="bg-soft/60 text-xs text-muted">
              <tr>
                <SortTh {...lines.th('article')} kind="text" className="px-3 py-2 font-medium">
                  Артикул
                </SortTh>
                <SortTh {...lines.th('qty')} align="right" className="px-3 py-2 font-medium">
                  В поставке
                </SortTh>
                <SortTh {...lines.th('sold')} align="right" className="px-3 py-2 font-medium">
                  Продано
                </SortTh>
                <SortTh {...lines.th('remaining')} align="right" className="px-3 py-2 font-medium">
                  Осталось
                </SortTh>
                <SortTh {...lines.th('price')} align="right" className="px-3 py-2 font-medium">
                  Цена
                </SortTh>
                <SortTh {...lines.th('amount')} align="right" className="px-3 py-2 font-medium">
                  За проданное
                </SortTh>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {lines.rows.map((ln) => (
                <tr key={ln.article}>
                  <td className="px-3 py-2">
                    <CopyText value={ln.article} label="артикул" className="font-medium" />
                    <Sub tone={ln.in_catalog ? undefined : 'warn'}>{ln.in_catalog ? ln.name ?? '' : 'нет в каталоге Ozon'}</Sub>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{ln.qty}</td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                    {editing ? (
                      <label className="flex items-center justify-end gap-1 text-xs text-muted">
                        ранее
                        <input
                          type="number"
                          min={0}
                          max={ln.qty}
                          value={sold[ln.article] ?? String(ln.sold_before)}
                          onChange={(e) => setSold((s) => ({ ...s, [ln.article]: e.target.value }))}
                          className={`${input} w-16 py-1 text-right`}
                          aria-label={`Уже продано ранее — ${ln.article}`}
                        />
                      </label>
                    ) : (
                      <>
                        {ln.sold + ln.sold_before}
                        <Sub>
                          {[ln.sold_before ? `ранее ${ln.sold_before}` : '', ln.returned ? `вернули ${ln.returned}` : '']
                            .filter(Boolean)
                            .join(', ')}
                        </Sub>
                      </>
                    )}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                    {ln.remaining}
                    {ln.written_off > 0 && <Sub>списано {ln.written_off}</Sub>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{ln.price === null ? <Sub tone="warn">нет себестоимости</Sub> : rub(ln.price)}</td>
                  <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{rub(ln.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {editing ? (
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" disabled={busy} onClick={saveSold} data-testid="sold-before-save">
              Сохранить
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Отмена
            </Button>
            <span className="text-xs text-muted">Сколько штук продано до того, как сервис стал видеть продажи — они войдут в долг.</span>
          </div>
        ) : null}

        {lot.moves.length > 0 && (
          <details className="rounded-lg border border-line p-3">
            <summary className="cursor-pointer text-sm font-medium">Продажи и возвраты по поставке</summary>
            <ul className="mt-2 max-h-56 space-y-1 overflow-y-auto text-xs tabular-nums" data-testid="lot-moves">
              {lot.moves.map((m, i) => (
                <li key={i} className="flex gap-3">
                  <span className="w-20 text-muted">{fmtDay(m.day)}</span>
                  <CopyText value={m.article} label="артикул" className="min-w-0 flex-1" />
                  <span className={m.kind === 'return' ? 'text-bad' : ''}>
                    {m.kind === 'return' ? `возврат −${m.qty}` : `продано ${m.qty}`}
                  </span>
                </li>
              ))}
            </ul>
          </details>
        )}

        {closing && (
          <div className="space-y-2 rounded-lg border border-line p-3" data-testid="lot-close-form">
            <div className="text-sm font-medium">Закрыть с остатком</div>
            <p className="text-xs text-muted">
              Остаток ({pcs(lot.remaining)}) больше не продаётся из этой поставки и не входит в долг. Проданное до сегодня
              остаётся в ней.
            </p>
            <div className="flex flex-wrap gap-2">
              <select value={reason} onChange={(e) => setReason(e.target.value as typeof reason)} className={`${input} w-auto`}>
                {CLOSE_REASONS.map((r) => (
                  <option key={r.value} value={r.value}>
                    {r.label}
                  </option>
                ))}
              </select>
              <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Комментарий" className={`${input} min-w-0 flex-1`} />
            </div>
            <div className="flex gap-2">
              <Button
                variant="primary"
                disabled={busy}
                onClick={() => run(async () => (await api.post<Payables>(`/finance/payables/lots/${lot.lot_id}/close`, { reason, comment: comment || null })).data)}
                data-testid="lot-close-confirm"
              >
                Закрыть поставку
              </Button>
              <Button variant="ghost" onClick={() => setClosing(false)}>
                Отмена
              </Button>
            </div>
          </div>
        )}

        <div className="flex flex-wrap gap-2 border-t border-line pt-3">
          {lot.has_document && (
            <Button
              size="sm"
              onClick={() =>
                saveFile(api.get(`/finance/payables/lots/${lot.lot_id}/document`, { responseType: 'blob' }), `поставка-${lot.number}.pdf`).catch((err) =>
                  setError(errorMessage(err)),
                )
              }
            >
              <Download className="h-4 w-4" /> Документ
            </Button>
          )}
          {!editing && (
            <Button size="sm" onClick={() => setEditing(true)} data-testid="sold-before-edit" title={older ? 'Поставка старше данных о продажах в сервисе' : undefined}>
              «Уже продано ранее»{older ? ' — нужно указать' : ''}
            </Button>
          )}
          {lot.status === 'closed' ? (
            <Button size="sm" disabled={busy} onClick={() => run(async () => (await api.post<Payables>(`/finance/payables/lots/${lot.lot_id}/reopen`)).data)}>
              Открыть снова
            </Button>
          ) : (
            lot.remaining > 0 &&
            !closing && (
              <Button size="sm" onClick={() => setClosing(true)} data-testid="lot-close">
                Закрыть с остатком
              </Button>
            )
          )}
          <Button size="sm" variant="ghost" disabled={busy} onClick={remove} className="ml-auto text-bad">
            Убрать из учёта
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function Fact({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="rounded-lg bg-soft/60 px-3 py-2">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`mt-0.5 font-semibold tabular-nums ${tone ?? ''}`}>{value}</dd>
    </div>
  )
}

// ---------- загрузка документа ----------

function UploadModal({ onClose, onSaved }: { onClose: () => void; onSaved: (text: string) => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [sold, setSold] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pick = useRef<HTMLInputElement>(null)

  const soldMap = useMemo(
    () => Object.fromEntries(Object.entries(sold).filter(([, v]) => v !== '').map(([k, v]) => [k, Number(v)])),
    [sold],
  )

  async function send(f: File, apply: boolean) {
    setBusy(true)
    setError(null)
    try {
      const form = new FormData()
      form.append('file', f)
      if (Object.keys(soldMap).length) form.append('sold_before', JSON.stringify(soldMap))
      const { data } = await api.post<UploadResult>('/finance/payables/lots/upload', form, { params: { dry_run: !apply } })
      if (apply) {
        const n = (data.counts.new ?? 0) + (data.counts.changed ?? 0)
        onSaved(n ? `Сохранено поставок: ${n}. Продажи по ним сервис отметит сам.` : 'Ничего нового — эти поставки уже загружены.')
      } else setResult(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const toSave = result ? (result.counts.new ?? 0) + (result.counts.changed ?? 0) : 0
  return (
    <Modal title="Загрузить поставку" onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="upload-modal">
        <p className="text-muted">
          Документ поставщика в PDF (сборочный лист, накладная) или шаблон «Поставки списком» в Excel / CSV. Сопоставляем
          только по артикулу; цену берём из справочника себестоимости на дату поставки. Сначала покажем, что получится.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary" onClick={() => pick.current?.click()} disabled={busy} data-testid="upload-pick">
            <FileUp className="h-4 w-4" /> {file ? 'Другой файл' : 'Выбрать файл'}
          </Button>
          {file && <CopyText value={file.name} label="название" className="text-muted" />}
          <input
            ref={pick}
            type="file"
            accept=".pdf,.xlsx,.csv"
            className="hidden"
            data-testid="upload-input"
            onChange={(e) => {
              const f = e.target.files?.[0]
              e.target.value = ''
              if (!f) return
              setFile(f)
              setSold({})
              setResult(null)
              send(f, false)
            }}
          />
        </div>
        <FormError message={error} />
        {busy && !result && <Skeleton className="h-24" />}
        {result && (
          <div className="space-y-4" data-testid="upload-preview">
            {result.docs.map((d, i) => (
              <PreviewBlock key={i} doc={d} salesFrom={result.sales_known_from} sold={sold} setSold={setSold} />
            ))}
            <div className="flex flex-wrap items-center gap-2 border-t border-line pt-3">
              <Button variant="primary" disabled={busy || toSave === 0 || !file} onClick={() => file && send(file, true)} data-testid="upload-apply">
                Сохранить поставки ({toSave})
              </Button>
              <Button variant="ghost" onClick={onClose}>
                Отмена
              </Button>
              {toSave === 0 && <span className="text-xs text-muted">Сохранять нечего: поставки уже загружены или в них ошибки.</span>}
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}

function PreviewBlock({ doc, salesFrom, sold, setSold }: {
  doc: PreviewDoc
  salesFrom: string | null
  sold: Record<string, string>
  setSold: React.Dispatch<React.SetStateAction<Record<string, string>>>
}) {
  const key = (article: string) => `${doc.number}|${doc.day}|${article}`
  const lines = useSort(doc.lines, { article: (ln) => ln.article, qty: (ln) => ln.qty, price: (ln) => num(ln.price), amount: (ln) => num(ln.amount) })
  return (
    <section className="space-y-2" data-testid="preview-doc">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-semibold">{lotTitle(doc)}</span>
        <Badge tone={DOC_STATUS[doc.status].tone}>{DOC_STATUS[doc.status].label}</Badge>
      </div>
      <div className="text-xs text-muted">
        {[doc.supplier, doc.warehouse ? `склад: ${doc.warehouse}` : null].filter(Boolean).join(' · ')}
      </div>
      {[...doc.errors].map((e) => (
        <p key={e} className="text-bad">
          {e}
        </p>
      ))}
      {doc.warnings.map((w) => (
        <p key={w} className="text-warn">
          {w}
        </p>
      ))}
      {doc.older_than_sales && (
        <p className="rounded-lg bg-soft/60 px-3 py-2 text-xs">
          Поставка раньше, чем сервис видит продажи (с {fmtDay(salesFrom)}). Если часть уже продана до этого дня — укажите
          «Уже продано ранее»: эти штуки войдут в долг и не будут ждать продажи.
        </p>
      )}
      <div className="overflow-x-auto rounded-lg border border-line">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-soft/60 text-xs text-muted">
            <tr>
              <SortTh {...lines.th('article')} kind="text" className="px-3 py-2 font-medium">
                Артикул
              </SortTh>
              <SortTh {...lines.th('qty')} align="right" className="px-3 py-2 font-medium">
                Кол-во
              </SortTh>
              <SortTh {...lines.th('price')} align="right" className="px-3 py-2 font-medium">
                Цена
              </SortTh>
              <SortTh {...lines.th('amount')} align="right" className="px-3 py-2 font-medium">
                Сумма
              </SortTh>
              {doc.older_than_sales && <th className="px-3 py-2 text-right font-medium">Уже продано ранее</th>}
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {lines.rows.map((ln) => (
              <tr key={ln.line}>
                <td className="px-3 py-2">
                  {ln.article ? <CopyText value={ln.article} label="артикул" className="font-medium" /> : <div className="font-medium">—</div>}
                  {ln.error ? (
                    <Sub tone="bad">{ln.error}</Sub>
                  ) : (
                    <Sub tone={ln.in_catalog ? undefined : 'warn'}>{ln.in_catalog ? ln.name ?? '' : 'нет в каталоге Ozon — продажи появятся, когда появится товар'}</Sub>
                  )}
                </td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{ln.qty ?? '—'}</td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{ln.price === null ? <Sub tone="warn">нет себестоимости</Sub> : rub(ln.price)}</td>
                <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">{rub(ln.amount)}</td>
                {doc.older_than_sales && (
                  <td className="px-3 py-2 text-right">
                    <input
                      type="number"
                      min={0}
                      max={ln.qty ?? undefined}
                      value={sold[key(ln.article)] ?? (ln.sold_before ? String(ln.sold_before) : '')}
                      placeholder="0"
                      onChange={(e) => setSold((s) => ({ ...s, [key(ln.article)]: e.target.value }))}
                      className={`${input} w-20 py-1 text-right`}
                      aria-label={`Уже продано ранее — ${ln.article}`}
                    />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
          <tfoot className="bg-soft/60 font-semibold">
            <tr>
              <td className="px-3 py-2">Итого</td>
              <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                {doc.lines_qty}
                {doc.total_qty !== null && doc.total_qty !== doc.lines_qty && <Sub tone="warn">в документе {doc.total_qty}</Sub>}
              </td>
              <td />
              <td className="px-3 py-2 text-right tabular-nums whitespace-nowrap">
                {rub(doc.amount)}
                {doc.no_price > 0 && <Sub tone="warn">без цены: {doc.no_price} арт.</Sub>}
              </td>
              {doc.older_than_sales && <td />}
            </tr>
          </tfoot>
        </table>
      </div>
    </section>
  )
}

function TemplatePanel({ onUpload }: { onUpload: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const download = (rows: 'all' | 'empty', format: 'xlsx' | 'csv') =>
    saveFile(api.get('/finance/payables/lots/template', { params: { rows, format }, responseType: 'blob' }), `поставки.${format}`).catch((err) =>
      setError(errorMessage(err, 'Не удалось скачать файл')),
    )
  const link = 'inline-flex items-center gap-1.5 text-sm text-accent hover:underline'
  return (
    <details className="rounded-xl border border-line bg-card p-4" data-testid="lots-template">
      <summary className="cursor-pointer text-sm font-semibold">Поставки списком через Excel</summary>
      <p className="mt-2 text-sm text-muted">
        Если документа в PDF нет или поставщик другой: «№ поставки», «Дата», «Артикул», «Количество» — по строке на артикул.
        Шаблон с вашими поставками загружается обратно без ошибок.
      </p>
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
        <button type="button" onClick={() => download('all', 'xlsx')} className={link}>
          <Download className="h-4 w-4" /> Шаблон с поставками (Excel)
        </button>
        <button type="button" onClick={() => download('empty', 'xlsx')} className={link}>
          <Download className="h-4 w-4" /> Пустой шаблон
        </button>
        <button type="button" onClick={() => download('all', 'csv')} className={link}>
          <Download className="h-4 w-4" /> CSV
        </button>
        <button type="button" onClick={onUpload} className={link}>
          <FileUp className="h-4 w-4" /> Загрузить файл
        </button>
      </div>
      <FormError message={error} />
    </details>
  )
}

// ---------- оплаты ----------

function PaymentsTable({ data, onEdit, onChanged }: { data: Payables; onEdit: (p: Payment) => void; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null)
  const pays = useSort(data.payments, { day: (p) => p.day, amount: (p) => num(p.amount), who: (p) => p.counterparty })
  async function remove(p: Payment) {
    if (!window.confirm(`Удалить оплату ${rub(p.amount)} от ${fmtDay(p.day)}?`)) return
    try {
      await api.delete(`/finance/payables/payments/${p.payment_id}`)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  return (
    <Panel
      flush
      title="Оплаты поставщику"
      description={
        data.operations_counted
          ? 'Оплаты из «Операций» (статья «Оплата поставщику») засчитываются сами — здесь вносите только то, чего там нет'
          : 'Ведёте «Операции»? Оплаты со статьёй «Оплата поставщику» засчитаются сами'
      }
      testid="payments-table"
    >
      <FormError message={error} />
      <div className="overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="bg-soft/60 text-xs text-muted">
            <tr>
              <SortTh {...pays.th('day')} kind="date" className="px-5 py-2 font-medium">
                Дата
              </SortTh>
              <SortTh {...pays.th('amount')} align="right" className="px-3 py-2 font-medium">
                Сумма
              </SortTh>
              <SortTh {...pays.th('who')} kind="text" className="px-3 py-2 font-medium">
                Кому и за что
              </SortTh>
              <th className="px-5 py-2 text-right font-medium" />
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {pays.rows.map((p) => (
              <tr key={`${p.source}-${p.payment_id}`}>
                <td className="px-5 py-3 tabular-nums">{fmtDay(p.day)}</td>
                <td className={`px-3 py-3 text-right tabular-nums whitespace-nowrap ${Number(p.amount) < 0 ? 'text-bad' : ''}`}>
                  {Number(p.amount) < 0 ? `−${rub(String(-Number(p.amount)))}` : rub(p.amount)}
                  {Number(p.amount) < 0 && <Sub>возврат от поставщика</Sub>}
                </td>
                <td className="px-3 py-3">
                  {p.counterparty ?? '—'}
                  <Sub>{p.comment ?? (p.source === 'operation' ? 'из «Операций»' : 'внесено здесь')}</Sub>
                </td>
                <td className="px-5 py-3 text-right">
                  {p.editable ? (
                    <span className="inline-flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => onEdit(p)}>
                        Изменить
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => remove(p)} className="text-bad">
                        Удалить
                      </Button>
                    </span>
                  ) : (
                    <Badge tone="muted">Операции</Badge>
                  )}
                </td>
              </tr>
            ))}
            {data.payments.length === 0 && (
              <tr>
                <td colSpan={4} className="px-5 py-6 text-center text-sm text-muted">
                  Оплат пока нет
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </Panel>
  )
}

function PaymentModal({ payment, onClose, onSaved }: { payment: Payment | null; onClose: () => void; onSaved: () => void }) {
  const today = todayMsk()
  const [day, setDay] = useState(payment?.day ?? today)
  const [amount, setAmount] = useState(payment?.amount ?? '')
  const [counterparty, setCounterparty] = useState(payment?.counterparty ?? '')
  const [comment, setComment] = useState(payment?.comment ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body = { day, amount: amount.replace(/\s/g, '').replace(',', '.'), counterparty: counterparty || null, comment: comment || null }
    try {
      if (payment) await api.patch(`/finance/payables/payments/${payment.payment_id}`, body)
      else await api.post('/finance/payables/payments', body)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal title={payment ? 'Изменить оплату' : 'Оплата поставщику'} onClose={onClose}>
      <form onSubmit={save} className="space-y-3 text-sm" data-testid="payment-form">
        <p className="text-muted">Оплата закрывает самые старые поставки первыми. Исправление сохраняется новой версией.</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="space-y-1">
            <span className="text-xs text-muted">Дата</span>
            <input type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} className={input} required />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">Сумма, ₽</span>
            <input inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} className={input} required data-testid="payment-amount" />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">Кому</span>
            <input value={counterparty} onChange={(e) => setCounterparty(e.target.value)} className={input} placeholder="Поставщик" />
          </label>
          <label className="space-y-1">
            <span className="text-xs text-muted">Описание</span>
            <input value={comment} onChange={(e) => setComment(e.target.value)} className={input} />
          </label>
        </div>
        <FormError message={error} />
        <div className="flex gap-2">
          <button type="submit" disabled={busy} className={buttonClass('primary')} data-testid="payment-save">
            Сохранить
          </button>
          <Button variant="ghost" onClick={onClose}>
            Отмена
          </Button>
        </div>
      </form>
    </Modal>
  )
}

// ---------- карточка в «Обзоре финансов» ----------

/** Долг поставщику коротко — в «Обзоре финансов», только при оплате после продажи. */
export function PayablesCard() {
  const settings = useSettings()
  const allowed = !!settings && isPostpay(settings.supplier_payment)
  const [data, setData] = useState<Payables | null>(null)
  useEffect(() => {
    if (!allowed) return
    let alive = true
    api
      .get<Payables>('/finance/payables', { params: { brief: true } })
      .then(({ data }) => alive && setData(data))
      .catch(() => alive && setData(null))
    return () => {
      alive = false
    }
  }, [allowed])
  if (!allowed || !data) return null
  const s = data.summary
  return (
    <Panel
      title="Долги поставщикам"
      description="За проданное на Ozon — по поставкам от поставщика"
      action={
        <Link href="/dashboard/finance/payables" className="text-accent hover:underline">
          Подробнее →
        </Link>
      }
      testid="payables-card"
    >
      {!data.configured ? (
        <p className="text-sm text-muted">
          Поставок пока нет — <Link href="/dashboard/finance/payables" className="text-accent hover:underline">загрузите документ поставщика</Link>, и
          сервис сам будет считать, сколько вы должны за проданное.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <div>
            <div className="text-xs text-muted">К оплате за проданное</div>
            <div className={`text-lg font-semibold tabular-nums ${Number(s.to_pay) > 0 ? 'text-warn' : ''}`}>{rub(s.to_pay)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Оплачено за 30 дней</div>
            <div className="text-lg font-semibold tabular-nums">{rub(s.paid_30d)}</div>
          </div>
          <div>
            <div className="text-xs text-muted">Ещё не продано</div>
            <div className="text-lg font-semibold tabular-nums">{rub(s.stock_value)}</div>
            <Sub>{pcs(s.stock_qty)}</Sub>
          </div>
        </div>
      )}
    </Panel>
  )
}
