'use client'
// Заявки покупателей на скидку (шаг 4.3a): таблица заявок, окно решения (предпросмотр → подтверждение → Ozon) и блок
// «Заявки на скидку» на странице товара. Пока не нажата кнопка подтверждения, в Ozon ничего не уходит; каждое решение
// записывается в журнал действий.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CopyText } from '@/components/copy'
import { Modal } from '@/components/planning/ui'
import { pctText, rub } from '@/components/reports'
import { SortTh, type SortDir } from '@/components/table'
import { Badge, EmptyState, Panel, Skeleton, Sub, buttonClass } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import {
  MARK_TONE,
  leftText,
  msk,
  type DecisionAction,
  type DecisionPreview,
  type DecisionResult,
  type DiscountList,
  type DiscountRow,
} from '@/lib/discounts'
import { fieldNumber } from '@/lib/pricing'

const STATE_TONE = { NEW: 'accent', APPROVED: 'ok', DECLINED: 'muted', EXPIRED: 'muted' } as const

export type DiscountSort = { key: string; dir: SortDir } | null

/** Таблица заявок. sort — сортирует сервер (заголовки SortTh); selection — выбор строк для решения списком. */
export function DiscountsTable({
  rows,
  sort,
  onSort,
  selected,
  onSelect,
  onDecide,
  compact = false,
  ruleOn = false,
}: {
  rows: DiscountRow[]
  sort?: DiscountSort
  onSort?: (key: string, dir: SortDir | null) => void
  selected?: Set<string>
  onSelect?: (ids: string[], on: boolean) => void
  onDecide: (rows: DiscountRow[], action: DecisionAction) => void
  compact?: boolean
  ruleOn?: boolean
}) {
  const th = (key: string) => ({
    active: sort?.key === key ? sort.dir : null,
    onSort: (dir: SortDir | null) => onSort?.(key, dir),
  })
  const sortable = Boolean(onSort)
  const head = (
    key: string,
    label: React.ReactNode,
    opts: { align?: 'right'; kind?: 'number' | 'text' | 'date'; sub?: React.ReactNode; first?: boolean } = {},
  ) => {
    const pad = `py-3 pr-3 font-medium ${opts.first ? 'pl-5' : 'pl-3'}`
    return sortable ? (
      <SortTh {...th(key)} align={opts.align} kind={opts.kind ?? 'number'} sub={opts.sub} className={pad}>
        {label}
      </SortTh>
    ) : (
      <th className={`${pad} ${opts.align === 'right' ? 'text-right' : ''}`}>{label}</th>
    )
  }
  const choosable = rows.filter((r) => r.can_decide)
  const allOn = selected !== undefined && choosable.length > 0 && choosable.every((r) => selected.has(r.id))
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[60rem] text-[13px]" data-testid="discounts-table">
        <thead className="bg-soft/60 text-left text-xs text-muted">
          <tr>
            {onSelect && (
              <th className="w-9 py-3 pl-5 pr-3">
                <input
                  type="checkbox"
                  aria-label="Выбрать все заявки, по которым можно решить"
                  checked={allOn}
                  disabled={choosable.length === 0}
                  onChange={(e) => onSelect(choosable.map((r) => r.id), e.target.checked)}
                  className="accent-accent"
                  data-testid="discounts-select-all"
                />
              </th>
            )}
            {head('offer_id', 'Товар', { kind: 'text', first: !onSelect })}
            {head('price', 'Ваша цена', { align: 'right' })}
            {head('requested_price', 'Просят', { align: 'right' })}
            {head('breakeven_price', 'Мин. для участия', { align: 'right' })}
            {head('margin', 'Маржа', { align: 'right', sub: 'при их цене' })}
            {!compact && head('stock', 'Остаток', { align: 'right' })}
            {head('end_at', 'Срок', { kind: 'date' })}
            <th className="px-3 py-3 font-medium">Пометки</th>
            <th className="py-3 pl-3 pr-5" />
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={`border-t border-line ${selected?.has(r.id) ? 'bg-accent/5' : ''}`} data-testid="discount-row" data-task={r.ozon_task_id}>
              {onSelect && (
                <td className="py-3 pl-5 pr-3 align-top">
                  <input
                    type="checkbox"
                    aria-label={`Выбрать заявку ${r.ozon_task_id}`}
                    checked={selected?.has(r.id) ?? false}
                    disabled={!r.can_decide}
                    onChange={(e) => onSelect([r.id], e.target.checked)}
                    className="accent-accent"
                    data-testid="discount-select"
                  />
                </td>
              )}
              <td className={`max-w-[16rem] py-3 pr-3 align-top ${onSelect ? 'pl-3' : 'pl-5'}`}>
                {r.product ? (
                  <>
                    <Link href={`/dashboard/products/${r.product.id}`} className="block min-w-0 hover:text-accent">
                      <CopyText value={r.product.offer_id} label="артикул" className="font-medium" />
                    </Link>
                    <Sub>
                      <CopyText value={r.product.name ?? r.name ?? `SKU ${r.sku}`} label="название" />
                    </Sub>
                  </>
                ) : (
                  <>
                    <CopyText value={r.name ?? `SKU ${r.sku ?? '—'}`} label="название" className="font-medium" />
                    <Sub tone="warn">нет в каталоге — обновите товары</Sub>
                  </>
                )}
              </td>
              <td className="whitespace-nowrap px-3 py-3 text-right align-top tabular-nums">
                {rub(r.price)}
                {r.min_auto_price && <Sub>мин. в Ozon {rub(r.min_auto_price)}</Sub>}
              </td>
              <td className="whitespace-nowrap px-3 py-3 text-right align-top font-medium tabular-nums" data-testid="discount-requested">
                {rub(r.requested_price)}
                <Sub>
                  {r.discount_pct !== null ? `−${Number(r.discount_pct).toLocaleString('ru-RU')} %` : '—'}
                  {r.requested_quantity ? ` · ${r.requested_quantity} шт.` : ''}
                </Sub>
              </td>
              <td className="whitespace-nowrap px-3 py-3 text-right align-top tabular-nums">
                {rub(r.breakeven_price)}
                {r.target_price && <Sub>целевая {rub(r.target_price)}</Sub>}
              </td>
              <td
                className={`whitespace-nowrap px-3 py-3 text-right align-top font-medium tabular-nums ${
                  r.margin === null ? 'text-muted' : Number(r.margin) < 0 ? 'text-bad' : r.marks.includes('low_margin') ? 'text-warn' : 'text-ok'
                }`}
                data-testid="discount-margin"
              >
                {r.margin === null ? <span title={r.missing.join(' ')}>нет данных</span> : pctText(r.margin)}
                {r.unit_profit !== null && (
                  <Sub tone={Number(r.unit_profit) < 0 ? 'bad' : undefined}>
                    {rub(r.unit_profit)} с 1 шт.{r.total_profit !== null && r.requested_quantity && r.requested_quantity > 1 ? ` · ${rub(r.total_profit)} за все` : ''}
                  </Sub>
                )}
              </td>
              {!compact && <td className="whitespace-nowrap px-3 py-3 text-right align-top tabular-nums">{r.stock === null ? '—' : `${r.stock} шт.`}</td>}
              <td className="whitespace-nowrap px-3 py-3 align-top">
                {r.can_decide ? (
                  <>
                    <span className={Number(r.hours_left) <= 24 ? 'font-medium text-bad' : ''}>{leftText(r.hours_left)}</span>
                    <Sub>до {msk(r.end_at)}</Sub>
                  </>
                ) : (
                  <>
                    <Badge tone={STATE_TONE[r.state]} dot>
                      {r.state_title}
                    </Badge>
                    {r.state === 'APPROVED' && r.approved_price && (
                      <Sub>
                        по {rub(r.approved_price)}
                        {r.approved_quantity ? ` · ${r.approved_quantity} шт.` : ''}
                      </Sub>
                    )}
                    {r.state !== 'APPROVED' && <Sub>{msk(r.moderated_at ?? r.end_at)}</Sub>}
                  </>
                )}
              </td>
              <td className="px-3 py-3 align-top">
                <div className="flex max-w-[12rem] flex-wrap gap-1">
                  {r.marks.map((m, i) => (
                    <Badge key={m} tone={MARK_TONE[m]}>
                      {r.mark_titles[i]}
                    </Badge>
                  ))}
                  {ruleOn && r.rule_ok === true && (
                    <Badge tone="ok" title="Маржа при запрошенной цене не ниже порога вашего правила">
                      По правилу — одобрить
                    </Badge>
                  )}
                  {r.decision && r.decision.status !== 'applied' && (
                    <Badge tone="bad" title={r.decision.error ?? undefined}>
                      Решение не прошло
                    </Badge>
                  )}
                </div>
              </td>
              <td className="whitespace-nowrap py-3 pl-3 pr-5 text-right align-top">
                {r.can_decide && (
                  <div className="flex justify-end gap-1.5">
                    <button type="button" onClick={() => onDecide([r], 'approve')} className={buttonClass('primary', 'sm')} data-testid="discount-approve">
                      Одобрить
                    </button>
                    <button type="button" onClick={() => onDecide([r], 'decline')} className={buttonClass('secondary', 'sm')} data-testid="discount-decline">
                      Отклонить
                    </button>
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Окно решения: предпросмотр (что уйдёт в Ozon, маржа, проверки) → подтверждение → итог по каждой заявке. */
export function DecisionModal({
  rows,
  action,
  onClose,
  onDone,
}: {
  rows: DiscountRow[]
  action: DecisionAction
  onClose: () => void
  onDone: (result: DecisionResult) => void
}) {
  const single = rows.length === 1
  const approve = action === 'approve'
  const [price, setPrice] = useState(single && rows[0].requested_price ? Number(rows[0].requested_price).toString() : '')
  const [qty, setQty] = useState(single && rows[0].requested_quantity ? String(rows[0].requested_quantity) : '')
  const [comment, setComment] = useState('')
  const [preview, setPreview] = useState<DecisionPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const [ack, setAck] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState<DecisionResult | null>(null)
  const seq = useRef(0)

  const p = fieldNumber(price)
  const q = fieldNumber(qty)
  const bad =
    single && approve && p !== null && (!Number.isFinite(p) || p <= 0)
      ? 'Цена — число больше 0'
      : single && approve && q !== null && (!Number.isInteger(q) || q < 1)
        ? 'Количество — целое число от 1'
        : null
  const items = rows.map((r) => ({
    task_id: r.id,
    action,
    ...(single && approve && p !== null ? { price: p.toFixed(2) } : {}),
    ...(single && approve && q !== null ? { quantity: q } : {}),
  }))
  const key = JSON.stringify({ items, comment })

  useEffect(() => {
    if (bad || result) return
    const id = ++seq.current
    setBusy(true)
    const t = setTimeout(
      () => {
        api
          .post<DecisionPreview>('/discounts/preview', { items, comment: comment.trim() || null })
          .then((r) => {
            if (id !== seq.current) return
            setPreview(r.data)
            setError(null)
          })
          .catch((err) => id === seq.current && setError(errorMessage(err)))
          .finally(() => id === seq.current && setBusy(false))
      },
      preview ? 400 : 0,
    )
    return () => clearTimeout(t)
    // items и комментарий сравниваем по содержимому (key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, bad])

  async function apply() {
    if (!preview) return
    setSending(true)
    setError(null)
    try {
      const { data } = await api.post<DecisionResult>('/discounts/apply', { batch_id: preview.batch_id, accept_loss: ack, confirmed })
      setResult(data)
      onDone(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSending(false)
    }
  }

  const s = preview?.summary
  const blocked = !preview || !s || s.ok === 0 || busy || sending || !!bad || (s.loss > 0 && !ack) || (s.needs_confirm && !confirmed)
  const input = 'mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-right text-sm outline-none focus:border-accent'
  const first = preview?.items[0]
  const title = approve ? (single ? 'Одобрить заявку на скидку' : `Одобрить заявки: ${rows.length}`) : single ? 'Отклонить заявку' : `Отклонить заявки: ${rows.length}`

  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="discount-decision">
        {single && (
          <p>
            <span className="font-medium">{rows[0].product?.offer_id ?? rows[0].name}</span>
            {rows[0].product?.name && <span className="text-muted"> · {rows[0].product.name}</span>}
            <span className="block text-muted">
              Покупатель просит {rub(rows[0].requested_price)}
              {rows[0].requested_quantity ? ` за ${rows[0].requested_quantity} шт.` : ''} · ваша цена {rub(rows[0].price)} · решить до {msk(rows[0].end_at)}
            </span>
          </p>
        )}

        {result ? (
          <div className="space-y-3" data-testid="discount-result">
            <p className={result.failed ? 'text-warn' : 'text-ok'}>{result.message}</p>
            <ul className="divide-y divide-line rounded-lg border border-line">
              {result.items.map((i) => {
                const r = rows.find((x) => x.id === i.task_id)
                return (
                  <li key={i.task_id} className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2">
                    <span className="min-w-0 truncate">{r?.product?.offer_id ?? r?.name ?? `Заявка ${i.ozon_task_id}`}</span>
                    <Badge tone={i.status === 'applied' ? 'ok' : i.status === 'unknown' ? 'warn' : 'bad'} dot>
                      {i.status_title}
                    </Badge>
                    {i.error && <span className="basis-full text-xs text-bad">{i.error}</span>}
                  </li>
                )
              })}
            </ul>
            <div className="flex justify-end">
              <button type="button" onClick={onClose} className={buttonClass('secondary')} data-testid="discount-close">
                Готово
              </button>
            </div>
          </div>
        ) : (
          <>
            {single && approve && (
              <div className="grid grid-cols-2 gap-3">
                <label className="text-xs text-muted">
                  Цена для покупателя, ₽
                  <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={input} data-testid="discount-price" />
                  <span className="mt-1 block text-faint">не ниже запрошенной; можно поднять</span>
                </label>
                <label className="text-xs text-muted">
                  Сколько штук разрешить
                  <input inputMode="numeric" value={qty} onChange={(e) => setQty(e.target.value)} className={input} data-testid="discount-qty" />
                  <span className="mt-1 block text-faint">просят до {rows[0].requested_quantity ?? '—'} · на складах {rows[0].stock ?? '—'}</span>
                </label>
              </div>
            )}
            {!single && approve && <p className="text-muted">Каждая заявка одобряется по цене и количеству, которые просит покупатель.</p>}
            <label className="block text-xs text-muted">
              Комментарий покупателю (необязательно)
              <input
                value={comment}
                maxLength={500}
                onChange={(e) => setComment(e.target.value)}
                className="mt-1 w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent"
                placeholder={approve ? 'Например: «Спасибо за заказ!»' : 'Например: «Цена уже со скидкой»'}
                data-testid="discount-comment"
              />
            </label>

            <FormError message={error ?? bad} />
            {!preview && !error && <p className="text-muted">Проверяем…</p>}
            {preview && s && (
              <div className={`space-y-3 transition-opacity ${busy ? 'opacity-60' : ''}`}>
                {single && first && approve ? (
                  <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5" data-testid="discount-figures">
                    <dt className="text-muted">Цена для покупателя</dt>
                    <dd className="text-right font-medium tabular-nums">
                      {rub(first.approved_price)}
                      {first.discount_pct && <span className="text-xs text-muted"> −{Number(first.discount_pct).toLocaleString('ru-RU')} %</span>}
                    </dd>
                    <dt className="text-muted">Останется с 1 шт.</dt>
                    <dd className={`text-right font-medium tabular-nums ${first.unit_profit === null ? 'text-muted' : Number(first.unit_profit) < 0 ? 'text-bad' : 'text-ok'}`} data-testid="discount-profit">
                      {rub(first.unit_profit)}
                      {first.margin !== null && <span className="text-xs"> · маржа {pctText(first.margin)}</span>}
                    </dd>
                    {first.total_profit !== null && (
                      <>
                        <dt className="text-muted">За {first.approved_quantity} шт.</dt>
                        <dd className={`text-right tabular-nums ${Number(first.total_profit) < 0 ? 'text-bad' : ''}`}>{rub(first.total_profit)}</dd>
                      </>
                    )}
                    <dt className="text-muted">Минимальная для участия</dt>
                    <dd className="text-right tabular-nums text-muted">{rub(first.breakeven_price)}</dd>
                  </dl>
                ) : (
                  <p data-testid="discount-summary">
                    Уйдёт в Ozon: <b>{s.ok}</b> из {s.total}
                    {s.blocked > 0 && <span className="text-bad"> · не пройдёт проверки: {s.blocked}</span>}
                    {s.loss > 0 && <span className="text-bad"> · в минус: {s.loss}</span>}
                  </p>
                )}
                <PreviewProblems preview={preview} rows={rows} single={single} />
                {s.loss > 0 && (
                  <label className="flex items-start gap-2 text-bad">
                    <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" data-testid="discount-ack" />
                    {single ? 'Понимаю: каждая продажа по этой цене — в минус' : `Понимаю: по ${s.loss} заявкам каждая продажа — в минус`}
                  </label>
                )}
                {s.needs_confirm && (
                  <label className="flex items-start gap-2">
                    <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} className="mt-0.5" data-testid="discount-confirmed" />
                    Проверил список: решение уйдёт в Ozon сразу по {s.ok} заявкам
                  </label>
                )}
              </div>
            )}

            <p className="text-xs text-faint">
              Решение уйдёт в Ozon сразу после подтверждения и записывается в журнал: что было, что стало, ответ Ozon. Цена
              товара на сайте не меняется — скидка только для этого покупателя.
            </p>
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" onClick={onClose} className={buttonClass('secondary')}>
                Отмена
              </button>
              <button type="button" onClick={apply} disabled={blocked} className={buttonClass(approve ? 'primary' : 'danger')} data-testid="discount-confirm">
                {sending ? 'Отправляем в Ozon…' : approve ? (single ? 'Одобрить в Ozon' : `Одобрить ${s?.ok ?? ''}`) : single ? 'Отклонить в Ozon' : `Отклонить ${s?.ok ?? ''}`}
              </button>
            </div>
          </>
        )}
      </div>
    </Modal>
  )
}

function PreviewProblems({ preview, rows, single }: { preview: DecisionPreview; rows: DiscountRow[]; single: boolean }) {
  const name = (taskId: string) => {
    const r = rows.find((x) => x.id === taskId)
    return r?.product?.offer_id ?? r?.name ?? ''
  }
  const errors = preview.items.flatMap((i) => i.errors.map((e) => (single ? e : `${name(i.task_id)}: ${e}`)))
  const warnings = preview.items.flatMap((i) => i.warnings.map((w) => (single ? w : `${name(i.task_id)}: ${w}`)))
  return (
    <>
      {errors.length > 0 && (
        <ul className="max-h-40 space-y-1 overflow-y-auto rounded-lg bg-bad/10 px-3 py-2 text-bad" data-testid="discount-errors">
          {errors.map((e, n) => (
            <li key={n}>{e}</li>
          ))}
        </ul>
      )}
      {warnings.length > 0 && (
        <ul className="max-h-40 space-y-1 overflow-y-auto rounded-lg bg-warn/10 px-3 py-2" data-testid="discount-warnings">
          {warnings.map((w, n) => (
            <li key={n}>{w}</li>
          ))}
        </ul>
      )}
    </>
  )
}

/** «Заявки на скидку» на странице товара: последние заявки товара и решение по новым. */
export function ProductDiscounts({ productId }: { productId: string }) {
  const [data, setData] = useState<DiscountList | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [deciding, setDeciding] = useState<{ rows: DiscountRow[]; action: DecisionAction } | null>(null)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    api
      .get<DiscountList>('/discounts', { params: { tab: 'all', product_id: productId } })
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [productId, reload])

  if (error)
    return (
      <Panel title="Заявки на скидку" testid="product-discounts">
        <p className="text-sm text-bad" data-testid="product-discounts-error">
          Заявки не загрузились: {error}{' '}
          <button
            type="button"
            onClick={() => {
              setError(null)
              setReload((n) => n + 1)
            }}
            className="text-accent underline"
          >
            Повторить
          </button>
        </p>
      </Panel>
    )
  if (!data) return <Skeleton className="h-24" />
  if (data.items.length === 0) return null
  const rows = data.items.slice(0, 5)
  return (
    <Panel
      title="Заявки на скидку"
      description={data.counts.new ? `Ждут решения: ${data.counts.new}` : 'Новых заявок нет'}
      action={
        <Link href={`/dashboard/products/discounts?tab=all&search=${encodeURIComponent(rows[0].product?.offer_id ?? '')}`} className="text-accent hover:underline">
          Все заявки →
        </Link>
      }
      flush
      testid="product-discounts"
    >
      <div className="mt-3">
        <DiscountsTable rows={rows} compact onDecide={(r, action) => setDeciding({ rows: r, action })} />
      </div>
      {deciding && (
        <DecisionModal
          rows={deciding.rows}
          action={deciding.action}
          onClose={() => setDeciding(null)}
          onDone={() => setReload((n) => n + 1)}
        />
      )}
    </Panel>
  )
}

export function DiscountsEmpty({ hasKey, filtered }: { hasKey: boolean; filtered: boolean }) {
  if (filtered)
    return (
      <EmptyState title="Ничего не нашлось" testid="discounts-none">
        Проверьте поиск, период и фильтры или выберите другую вкладку.
      </EmptyState>
    )
  return (
    <EmptyState title="Заявок пока нет" testid="discounts-empty">
      {hasKey
        ? 'Когда покупатель попросит скидку, заявка появится здесь — сервис проверяет Ozon каждые 30 минут.'
        : 'Заявки приходят из Ozon API — подключите ключ в «Интеграциях».'}
    </EmptyState>
  )
}
