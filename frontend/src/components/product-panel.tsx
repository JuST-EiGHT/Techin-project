'use client'
// Боковая панель товара (шаг 4.1): детали, цена за 30 дней, расчёт цены (4.2), остатки, акции, правка себестоимости.
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'

import { FormError } from '@/components/AuthForm'
import { TipBox, useDrill, type Drill } from '@/components/charts'
import { useTokenColors } from '@/components/finance'
import { PriceCalcBlock } from '@/components/price-calc'
import { Badge, fmtDateTime, fmtDay, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { VAT_OPTIONS, costPath, since, toNumber, vatText } from '@/lib/costs'
import { INDEX_NOTE, PRICE_INDEX, STOCK_STATUS, type ProductDetail } from '@/lib/products'

const qty = (n: number | null) => (n === null ? '—' : `${n.toLocaleString('ru-RU')} шт.`)
const num = (v: string | null) => (v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 }))

/** Панель справа (на телефоне — на весь экран). Esc и клик мимо — закрыть. */
export function ProductPanel({ productId, onClose, onChanged }: { productId: string; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<ProductDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const close = useRef<HTMLButtonElement>(null)

  const load = useCallback(async () => {
    try {
      setData((await api.get<ProductDetail>(`/products/${productId}`)).data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [productId])

  useEffect(() => {
    setData(null)
    load()
  }, [load])

  useEffect(() => {
    close.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const item = data?.item
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-bg/70" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={item?.offer_id ?? 'Товар'}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full overflow-y-auto border-l border-line bg-card shadow-xl sm:max-w-lg"
        data-testid="product-panel"
      >
        <div className="sticky top-0 z-10 flex items-start gap-3 border-b border-line bg-card px-5 py-4">
          <div className="min-w-0">
            <h2 className="break-words text-lg font-semibold">{item?.offer_id ?? 'Товар'}</h2>
            {item && (
              <p className="text-sm text-muted">
                {item.name ?? 'Без названия'}
                {item.is_archived && (
                  <>
                    {' '}
                    <Badge tone="muted">в архиве</Badge>
                  </>
                )}
              </p>
            )}
          </div>
          <button ref={close} type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-muted hover:text-fg" aria-label="Закрыть">
            ✕
          </button>
        </div>
        <div className="space-y-6 px-5 py-4">
          <FormError message={error} />
          {!data && !error && <p className="text-sm text-muted">Загрузка…</p>}
          {data && item && (
            <>
              <IdLine data={data} />
              <PriceCalcBlock productId={productId} refreshKey={data} />
              <PriceBlock data={data} />
              <StockBlock data={data} />
              <PromoBlock data={data} />
              <CostBlock
                data={data}
                onSaved={() => {
                  load()
                  onChanged()
                }}
              />
            </>
          )}
        </div>
      </aside>
    </div>
  )
}

function Section({ title, children, more, testid }: { title: string; children: React.ReactNode; more?: React.ReactNode; testid?: string }) {
  return (
    <section data-testid={testid}>
      <div className="mb-2 flex items-baseline gap-3">
        <h3 className="text-xs uppercase tracking-wider text-muted">{title}</h3>
        {more && <div className="ml-auto text-xs">{more}</div>}
      </div>
      {children}
    </section>
  )
}

function Row({ label, children, strong, tone = '', testid }: { label: React.ReactNode; children: React.ReactNode; strong?: boolean; tone?: string; testid?: string }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 border-b border-line py-1.5 text-sm last:border-b-0 ${strong ? 'font-semibold' : ''}`} data-testid={testid}>
      <span className={strong ? '' : 'text-muted'}>{label}</span>
      <span className={`text-right tabular-nums ${tone}`}>{children}</span>
    </div>
  )
}

function IdLine({ data }: { data: ProductDetail }) {
  const { item } = data
  return (
    <p className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted">
      {item.ozon_sku && <span>SKU {item.ozon_sku}</span>}
      <span>ID товара {item.ozon_product_id}</span>
      {data.barcode && <span>штрихкод {data.barcode}</span>}
      {item.ozon_sku && (
        <a href={`https://www.ozon.ru/product/${item.ozon_sku}/`} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          Открыть на Ozon ↗
        </a>
      )}
    </p>
  )
}

function PriceBlock({ data }: { data: ProductDetail }) {
  const { item } = data
  const idx = item.price_index ? PRICE_INDEX[item.price_index] : null
  return (
    <Section title="Цена" testid="panel-price">
      <Row label="Цена">{rub(item.price)}</Row>
      {item.old_price && <Row label="До скидки (зачёркнутая)">{rub(item.old_price)}</Row>}
      {item.marketing_price && item.marketing_price !== item.price && <Row label="С вашими акциями">{rub(item.marketing_price)}</Row>}
      <Row label={<span title="Ниже неё цена в акциях не опустится">Минимальная цена в Ozon</span>}>
        {item.min_price ? rub(item.min_price) : 'не задана'}
      </Row>
      <Row label="Индекс цены">
        {idx ? (
          <span className="inline-flex items-center gap-1.5" title={idx.hint}>
            <span className={`inline-block h-2 w-2 rounded-full ${idx.dot}`} /> {idx.label}
          </span>
        ) : (
          'нет индекса'
        )}
      </Row>
      <p className="mt-1 text-xs text-faint">
        {idx ? `${idx.label[0].toUpperCase()}${idx.label.slice(1)}: ${idx.hint}. ` : ''}
        {INDEX_NOTE}
      </p>
      <PriceChart data={data} />
    </Section>
  )
}

type PricePoint = { date: string; label: string; price: number | null; min: number | null; marketing: number | null }

function PriceTip({ active, payload, drill }: { active?: boolean; payload?: { payload: PricePoint }[]; drill: Drill<PricePoint> }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <TipBox href={null} touch={drill.touch}>
      <div className="mb-1 font-semibold">{fmtDay(p.date)}</div>
      {p.price === null ? (
        <div className="text-muted">истории цены ещё нет</div>
      ) : (
        <div className="space-y-0.5 tabular-nums">
          <div>Цена: {rub(String(p.price))}</div>
          {p.marketing !== null && p.marketing !== p.price && <div>С вашими акциями: {rub(String(p.marketing))}</div>}
          <div className="text-faint">Мин. цена: {p.min === null ? 'не задана' : rub(String(p.min))}</div>
        </div>
      )}
    </TipBox>
  )
}

/** Цена на конец каждого дня за 30 дней. Своего раздела у графика нет — только подсказка (правило 16). */
function PriceChart({ data }: { data: ProductDetail }) {
  const colors = useTokenColors()
  const drill = useDrill<PricePoint>()
  const rows: PricePoint[] = data.price_days.map((d) => ({
    date: d.date,
    label: fmtDay(d.date).slice(0, 5),
    price: d.price === null ? null : Number(d.price),
    min: d.min_price === null ? null : Number(d.min_price),
    marketing: d.marketing_price === null ? null : Number(d.marketing_price),
  }))
  const known = rows.filter((r) => r.price !== null).length
  const changes = data.price_changes.filter((c, i, all) => c.price !== (all[i + 1]?.price ?? null))
  return (
    <div className="mt-3" data-testid="price-chart">
      <div className="mb-1 text-xs text-muted">Цена за {rows.length} дней</div>
      {known < 2 || !colors ? (
        <p className="rounded-lg bg-bg/60 px-3 py-2 text-xs text-muted">
          {data.first_price_at
            ? `История цены копится с ${fmtDay(data.first_price_at.slice(0, 10))} — точек для графика пока мало.`
            : 'Цену из Ozon ещё не загружали — история появится после синхронизации товаров.'}
        </p>
      ) : (
        <div className="h-40" onPointerDown={drill.boxProps.onPointerDown}>
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: 0 }}>
              <CartesianGrid vertical={false} stroke={colors.line} />
              <XAxis dataKey="label" tick={{ fill: colors.muted, fontSize: 11 }} tickLine={false} axisLine={{ stroke: colors.line }} minTickGap={16} />
              <YAxis
                tick={{ fill: colors.muted, fontSize: 11 }}
                tickFormatter={(v: number) => v.toLocaleString('ru-RU', { maximumFractionDigits: 0 })}
                tickLine={false}
                axisLine={false}
                width={52}
                domain={['auto', 'auto']}
              />
              <Tooltip content={<PriceTip drill={drill} />} cursor={{ stroke: colors.line }} {...drill.tooltipProps} />
              <Line type="monotone" dataKey="min" stroke={colors.faint} strokeDasharray="4 4" dot={false} isAnimationActive={false} connectNulls={false} />
              <Line type="monotone" dataKey="price" stroke={colors.accent} strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
      {known >= 2 && (
        <p className="mt-1 flex gap-4 text-xs text-faint">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 bg-accent" /> цена
          </span>
          {rows.some((r) => r.min !== null) && (
            <span className="flex items-center gap-1.5">
              <span className="inline-block w-4 border-t border-dashed border-faint" /> мин. цена
            </span>
          )}
        </p>
      )}
      {changes.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-muted" data-testid="price-changes">
          {changes.slice(0, 5).map((c, i) => {
            // самое раннее изменение периода: прежняя цена — та, что действовала в первый день
            const start = data.price_days[0]?.price ?? null
            const prev = changes[i + 1]?.price ?? (start !== c.price ? start : null)
            return (
              <li key={c.at} className="flex justify-between gap-3">
                <span>{fmtDateTime(c.at)}</span>
                <span className="tabular-nums">
                  {prev !== null && <span className="text-faint">{rub(prev)} → </span>}
                  {rub(c.price)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function StockBlock({ data }: { data: ProductDetail }) {
  const { item } = data
  const status = item.stock_status ? STOCK_STATUS[item.stock_status] : null
  return (
    <Section
      title="Остатки и продажи"
      testid="panel-stock"
      more={
        <Link href="/dashboard/warehouses" className="text-accent hover:underline">
          Все остатки →
        </Link>
      }
    >
      <Row label="Доступно на всех складах">{qty(item.stock)}</Row>
      {item.in_transit ? <Row label="В пути на склады">{qty(item.in_transit)}</Row> : null}
      <Row label="Продажи в день, за 30 дней">{item.avg_daily_sales === null ? 'мало данных' : num(item.avg_daily_sales)}</Row>
      <Row label="Хватит на" tone={status?.tone}>
        {item.days_of_supply !== null ? `${num(item.days_of_supply)} дн.` : '—'}
        {status && ` · ${status.label}`}
      </Row>
      <Row label="Последняя продажа">{item.last_sale ? fmtDay(item.last_sale) : 'нет данных'}</Row>
      {data.stocks.length > 0 && (
        <table className="mt-2 w-full text-xs" data-testid="panel-warehouses">
          <thead className="text-left text-faint">
            <tr>
              <th className="py-1 font-normal">Склад</th>
              <th className="py-1 text-right font-normal">Доступно</th>
              <th className="py-1 text-right font-normal">В пути</th>
            </tr>
          </thead>
          <tbody>
            {data.stocks.map((s) => (
              <tr key={`${s.warehouse}-${s.type}`} className="border-t border-line">
                <td className="py-1 pr-2">
                  {s.cluster_name ? `${s.cluster_name} · ` : ''}
                  {s.warehouse} <span className="text-faint">{s.type}</span>
                </td>
                <td className="py-1 text-right tabular-nums">{s.present}</td>
                <td className="py-1 text-right tabular-nums text-muted">{s.in_transit ?? '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}

function PromoBlock({ data }: { data: ProductDetail }) {
  const promos = data.item.promotions
  return (
    <Section
      title="Акции"
      testid="panel-promos"
      more={
        <Link href="/dashboard/promotions" className="text-accent hover:underline">
          Все акции →
        </Link>
      }
    >
      {promos.length === 0 ? (
        <p className="text-sm text-muted">Сейчас не участвует в акциях.</p>
      ) : (
        promos.map((p) => (
          <Row
            key={p.id}
            label={
              <span>
                {p.title}
                {p.added_automatically && (
                  <>
                    {' '}
                    <Badge tone="warn" title="Ozon добавил товар в акцию без вас">
                      авто
                    </Badge>
                  </>
                )}
                {p.date_to && <span className="block text-xs text-faint">до {fmtDay(p.date_to.slice(0, 10))}</span>}
              </span>
            }
          >
            {rub(p.action_price)}
            {p.margin_in_promo !== null && (
              <span className={`block text-xs ${Number(p.margin_in_promo) < 0 ? 'text-bad' : 'text-muted'}`}>маржа {pctText(p.margin_in_promo)}</span>
            )}
          </Row>
        ))
      )}
    </Section>
  )
}

function CostBlock({ data, onSaved }: { data: ProductDetail; onSaved: () => void }) {
  const { item, cost } = data
  const [editing, setEditing] = useState(cost === null)
  const [price, setPrice] = useState(cost ? String(Number(cost.cost_price)) : '')
  const [vat, setVat] = useState(cost?.vat_multiplier ? Number(cost.vat_multiplier).toFixed(3) : '')
  const [from, setFrom] = useState('')
  const [note, setNote] = useState(cost?.note ?? '')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    const p = toNumber(price)
    if (p === null || p <= 0) return setError('Себестоимость: число больше 0')
    setBusy(true)
    try {
      const { data: res } = await api.put<{ created: boolean; recosted_reports: number }>(costPath(item.offer_id), {
        cost_price: p.toFixed(2),
        vat_multiplier: vat || null,
        note: note.trim() || null,
        effective_from: from || null,
      })
      setSaved(
        res.created
          ? `Сохранено${res.recosted_reports ? ` — пересчитано отчётов: ${res.recosted_reports}` : ''}`
          : 'Такая себестоимость уже действует — ничего не изменилось',
      )
      setEditing(false)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const input = 'w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm outline-none focus:border-accent'
  return (
    <Section
      title="Себестоимость"
      testid="panel-cost"
      more={
        <Link href="/dashboard/costs" className="text-accent hover:underline">
          Все и история →
        </Link>
      }
    >
      {cost && !editing && (
        <>
          <Row label="За 1 шт. без НДС">{rub(cost.cost_price)}</Row>
          <Row label={`С НДС поставщика (${vatText(cost.supplier_vat)}${cost.vat_multiplier === null ? ', по профилю' : ''})`}>{rub(cost.cost_with_vat)}</Row>
          <Row label="Действует">{since(cost.effective_from)}</Row>
          {cost.note && <Row label="Комментарий">{cost.note}</Row>}
          <button
            type="button"
            onClick={() => {
              setSaved(null)
              setEditing(true)
            }}
            className="mt-2 rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent"
            data-testid="cost-edit-open"
          >
            Изменить
          </button>
        </>
      )}
      {!cost && !editing && <p className="text-sm text-warn">Не указана.</p>}
      {saved && <p className="mt-2 text-sm text-ok" data-testid="cost-saved">{saved}</p>}
      {editing && (
        <form onSubmit={save} className="space-y-3" data-testid="cost-form">
          {!cost && (
            <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm">
              Себестоимость не указана — маржу и прибыль товара не считаем, чтобы не показать завышенную.
            </p>
          )}
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-muted">
              За 1 шт. без НДС, ₽
              <input autoFocus aria-label="Себестоимость" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={`${input} mt-1 text-right`} />
            </label>
            <label className="text-xs text-muted">
              НДС поставщика
              <select aria-label="Ставка НДС" value={vat} onChange={(e) => setVat(e.target.value)} className={`${input} mt-1`}>
                <option value="">как в налоговом профиле ({vatText(data.supplier_vat)})</option>
                {vat && !VAT_OPTIONS.some((o) => o.value === vat) && <option value={vat}>{vatText(vat)}</option>}
                {VAT_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-muted">
              Действует с
              <input aria-label="Действует с" type="date" value={from} onChange={(e) => setFrom(e.target.value)} className={`${input} mt-1`} />
              <span className="mt-1 block text-faint">{cost ? 'пусто — с сегодня' : 'пусто — с начала учёта, и для прошлых отчётов'}</span>
            </label>
            <label className="text-xs text-muted">
              Комментарий
              <input aria-label="Комментарий" value={note} maxLength={255} onChange={(e) => setNote(e.target.value)} className={`${input} mt-1`} />
            </label>
          </div>
          <p className="text-xs text-faint">Прежняя цена остаётся в истории: прошлые отчёты считаются по цене на их даты.</p>
          {error && <p className="text-sm text-bad">{error}</p>}
          <div className="flex gap-2">
            <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="cost-save">
              {busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
            {cost && (
              <button type="button" onClick={() => setEditing(false)} className="text-sm text-muted hover:text-fg">
                Отмена
              </button>
            )}
          </div>
        </form>
      )}
    </Section>
  )
}
