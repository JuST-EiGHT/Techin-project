'use client'
// «Статистика товара» (шаг 10.14, docs/SITEMAP.md п. 3.2): всё о товаре на одной странице — показатели и динамика
// (10.3), юнит-экономика за период (3.4, 3.7a), цены и скидки с маржой (4.2), история цен и акций (6.4, 4.3), остатки по
// кластерам и поставки (5.1, 5.2), себестоимость (2.3), рекомендации (10.6) и быстрые переходы. Данные —
// GET /products/{id}/stats и готовые запросы аналитики; своих расчётов на странице нет.
import Link from 'next/link'

import { Badge, fmtDateTime, fmtDay, pctText, rub, rubSigned } from '@/components/reports'
import { UnitEconomics, signTone } from '@/components/products-pnl'
import { since } from '@/lib/costs'
import { FLAGS, marginTone, type ClusterStock, type PriceLevel, type ProductStats, type RiskLevel } from '@/lib/products'
import { num, perDay, qty } from '@/lib/stocks'
import { URGENCY } from '@/lib/supply'

/** Карточка раздела страницы: заголовок, ссылки справа, содержимое. */
export function Box({ title, more, testid, id, children }: { title: string; more?: React.ReactNode; testid: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="min-w-0 scroll-mt-20 rounded-xl border border-line bg-card p-4" data-testid={testid}>
      <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-semibold">{title}</h2>
        {more && <div className="ml-auto flex flex-wrap gap-x-3 text-sm">{more}</div>}
      </div>
      {children}
    </section>
  )
}

const more = 'text-accent hover:underline'

/** Значки проблем товара с текстом — на странице место есть, подсказка не нужна. */
export function ProblemList({ flags }: { flags: ProductStats['detail']['item']['flags'] }) {
  if (!flags.length) return null
  return (
    <ul className="flex flex-wrap gap-2" data-testid="product-flags">
      {flags.map((f) => {
        const meta = FLAGS[f.code]
        return (
          <li key={f.code} className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs ${meta.tone}`} data-testid={`product-flag-${f.code}`}>
            <span className="font-bold">{meta.mark}</span>
            {f.text}
          </li>
        )
      })}
    </ul>
  )
}

// ── юнит-экономика и расходы за период ──
export function UnitBlock({ data, costLabel, profitHref, lowMargin }: { data: ProductStats; costLabel: string; profitHref: string; lowMargin: number }) {
  const p = data.pnl
  const costTitle = data.cost_basis === 'without_vat' ? 'Себестоимость без НДС' : costLabel
  const period = `${fmtDay(data.period.date_from)} – ${fmtDay(data.period.date_to)}`
  return (
    <Box
      title="Юнит-экономика и расходы"
      testid="product-unit"
      more={
        <Link href={profitHref} className={more}>
          Исходные начисления →
        </Link>
      }
    >
      <p className="-mt-2 mb-3 text-xs text-faint">за {period}, по начислениям Ozon — как в «Прибыли по товарам»</p>
      {!p ? (
        <p className="text-sm text-muted" data-testid="unit-empty">
          {data.period.data_source === 'none'
            ? 'За период нет начислений — продажи и расходы неизвестны (это не ноль). Обновите начисления или загрузите отчёт.'
            : 'За период товар не продавался — расходов на продажу нет.'}
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm">
            <dt className="text-muted">Продано</dt>
            <dd className="text-right tabular-nums">
              {p.qty} шт.{p.avg_price && <span className="text-muted"> по {rub(p.avg_price)}</span>}
            </dd>
            {p.returned_qty > 0 && (
              <>
                <dt className="text-muted">Возвращено</dt>
                <dd className="text-right tabular-nums text-warn">{p.returned_qty} шт.</dd>
              </>
            )}
            <dt className="text-muted">Выручка</dt>
            <dd className="text-right tabular-nums">{rub(p.revenue)}</dd>
            <dt className="text-muted">Комиссия Ozon</dt>
            <dd className={`text-right tabular-nums ${signTone(p.commission)}`}>{rub(p.commission)}</dd>
            <dt className="text-muted">Логистика</dt>
            <dd className={`text-right tabular-nums ${signTone(p.logistics)}`}>{rub(p.logistics)}</dd>
            <dt className="text-muted">Прочее: баллы, эквайринг, возвраты</dt>
            <dd className={`text-right tabular-nums ${signTone(p.other)}`}>{rub(p.other)}</dd>
            <dt className="text-muted">= Начислено Ozon</dt>
            <dd className={`text-right font-medium tabular-nums ${signTone(p.total)}`}>{rub(p.total)}</dd>
            <dt className="text-muted">− {costTitle}</dt>
            <dd className={`text-right tabular-nums ${p.cost_missing ? 'text-warn' : ''}`}>{p.cost_missing ? 'не указана' : rub(p.cost)}</dd>
            <dt className="font-semibold">= Прибыль до налога</dt>
            <dd className={`text-right font-semibold tabular-nums ${p.profit === null ? 'text-muted' : signTone(p.profit)}`} data-testid="unit-profit">
              {rub(p.profit)}
              {p.margin !== null && <span className={`ml-1 text-xs ${marginTone(p.margin, lowMargin)}`}>{pctText(p.margin)}</span>}
            </dd>
          </dl>
          {p.cost_missing && (
            <p className="mt-3 rounded-lg bg-warn/10 px-3 py-2 text-sm">
              Себестоимость не указана — прибыль не считаем, чтобы не показать завышенную.{' '}
              <a href="#cost" className={more}>
                Указать ниже →
              </a>
            </p>
          )}
          {p.per_unit && <UnitEconomics u={p.per_unit} costTitle={costTitle} />}
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-muted hover:text-fg">Начисления по статьям: {Object.keys(p.by_type).length}</summary>
            <table className="mt-1 w-full" data-testid="unit-by-type">
              <tbody>
                {Object.entries(p.by_type).map(([name, v]) => (
                  <tr key={name} className="border-b border-line last:border-b-0">
                    <td className="py-1.5 pr-3">{name}</td>
                    <td className={`whitespace-nowrap py-1.5 text-right tabular-nums ${signTone(v)}`}>{rub(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </>
      )}
      {data.period.data_source !== 'none' && data.period.missing_days > 0 && (
        <p className="mt-3 text-xs text-faint">
          Дней без начислений: {data.period.missing_days} из {data.period.days_total} — цифры могут быть меньше настоящих.
        </p>
      )}
    </Box>
  )
}

// ── цены и скидки ──
const LEVEL_NOTE: Record<PriceLevel['kind'], string> = {
  old: 'видит покупатель зачёркнутой',
  price: 'от неё считаются остальные скидки',
  marketing: 'цена с учётом ваших акций в Ozon',
  promo: 'участвует',
  candidate: 'можно добавить — по максимальной цене для участия',
}

export function PricesBlock({ data, lowMargin, onChange }: { data: ProductStats; lowMargin: number; onChange: (() => void) | null }) {
  const { levels, missing, note } = data.prices
  const id = data.detail.item.id
  return (
    <Box
      title="Цена и скидки"
      testid="product-prices"
      more={
        <>
          {onChange && (
            <button type="button" onClick={onChange} className={more} data-testid="price-change-open">
              Изменить цену
            </button>
          )}
          <a href="#calc" className={more}>
            Расчёт цены
          </a>
        </>
      }
    >
      {levels.length === 0 ? (
        <p className="text-sm text-muted">Цену из Ozon ещё не загружали — обновите товары.</p>
      ) : (
        <div className="-mx-4 overflow-x-auto px-4">
          <table className="w-full min-w-[26rem] text-sm" data-testid="price-levels">
            <thead className="text-left text-xs text-faint">
              <tr>
                <th className="py-1 pr-2 font-normal">Цена</th>
                <th className="py-1 pr-2 text-right font-normal">₽</th>
                <th className="py-1 pr-2 text-right font-normal">Маржа</th>
                <th className="py-1 text-right font-normal">С 1 шт.</th>
              </tr>
            </thead>
            <tbody>
              {levels.map((lv, i) => (
                <tr key={`${lv.kind}-${lv.promotion_id ?? i}`} className={`border-t border-line ${lv.kind === 'candidate' || lv.kind === 'old' ? 'text-muted' : ''}`} data-testid="price-level" data-kind={lv.kind}>
                  <td className="py-1.5 pr-2">
                    {lv.promotion_id ? (
                      <Link href={`/dashboard/products/promotions/${lv.promotion_id}`} className="hover:text-accent">
                        {lv.kind === 'candidate' ? `Можно в акцию «${lv.title}»` : `В акции «${lv.title}»`}
                      </Link>
                    ) : (
                      lv.title
                    )}
                    {lv.added_automatically && (
                      <>
                        {' '}
                        <Badge tone="warn" title="Ozon добавил товар в акцию без вас">
                          авто
                        </Badge>
                      </>
                    )}
                    <span className="block text-xs text-faint">
                      {LEVEL_NOTE[lv.kind]}
                      {lv.date_to && ` · до ${fmtDay(lv.date_to.slice(0, 10))}`}
                    </span>
                  </td>
                  <td className="whitespace-nowrap py-1.5 pr-2 text-right tabular-nums">
                    {lv.kind === 'old' ? <span className="line-through">{rub(lv.price)}</span> : rub(lv.price)}
                    {lv.discount_pct && <span className="block text-xs text-faint">−{pctText(lv.discount_pct)}</span>}
                  </td>
                  <td className={`whitespace-nowrap py-1.5 pr-2 text-right tabular-nums ${marginTone(lv.margin, lowMargin)}`} data-testid="level-margin">
                    {lv.kind === 'old' ? '' : pctText(lv.margin)}
                  </td>
                  <td className="whitespace-nowrap py-1.5 text-right tabular-nums">
                    {lv.kind === 'old' ? '' : rub(lv.unit_profit)}
                    {lv.profit_change !== null && (
                      <span className={`block text-xs ${Number(lv.profit_change) < 0 ? 'text-bad' : 'text-ok'}`} data-testid="level-change">
                        {rubSigned(lv.profit_change)} к вашей цене
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {missing.length > 0 && (
        <ul className="mt-3 space-y-1 rounded-lg bg-warn/10 px-3 py-2 text-sm" data-testid="price-missing">
          {missing.map((m) => (
            <li key={m}>{m}</li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-xs text-faint">{note}</p>
      <p className="mt-1 text-xs">
        <Link href={`/dashboard/products/promotions?tab=history&product=${id}`} className={more}>
          Акции товара →
        </Link>
      </p>
    </Box>
  )
}

// ── история цены (через сервис) и участия в акциях ──
export function PriceChangesList({ data }: { data: ProductStats }) {
  const id = data.detail.item.id
  return (
    <div className="mt-4" data-testid="service-price-changes">
      <div className="mb-1 flex items-baseline gap-3">
        <h3 className="text-xs uppercase tracking-wider text-muted">Изменения через сервис за год</h3>
        <Link href={`/dashboard/products/prices/history?product=${id}`} className={`ml-auto text-xs ${more}`}>
          История цен{data.price_changes_total > data.price_changes.length ? ` (${data.price_changes_total})` : ''} →
        </Link>
      </div>
      {data.price_changes.length === 0 ? (
        <p className="text-sm text-muted">Через сервис цену не меняли.</p>
      ) : (
        <ul className="divide-y divide-line text-sm">
          {data.price_changes.map((c) => (
            <li key={c.id} className="flex flex-wrap items-baseline gap-x-3 py-1.5">
              <span className="text-muted">{fmtDateTime(c.created_at)}</span>
              <span className="tabular-nums">
                {c.price_before !== null && <span className="text-faint">{rub(c.price_before)} → </span>}
                {rub(c.price_after)}
              </span>
              <span className="text-xs text-faint">{c.kind_title}</span>
              {c.status === 'applied' ? null : <Badge tone="muted">{c.status_title}</Badge>}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

const PROMO_STATUS = { active: 'участвует', left: 'вышел', ended: 'акция закончилась' } as const

export function PromosBlock({ data, lowMargin }: { data: ProductStats; lowMargin: number }) {
  const id = data.detail.item.id
  return (
    <Box
      title="Участие в акциях"
      testid="product-promos"
      more={
        <Link href={`/dashboard/products/promotions?tab=history&product=${id}`} className={more}>
          Вся история{data.promotions_total > data.promotions.length ? ` (${data.promotions_total})` : ''} →
        </Link>
      }
    >
      {data.promotions.length === 0 ? (
        <p className="text-sm text-muted">За год товар не участвовал в акциях (история копится с первой синхронизации акций).</p>
      ) : (
        <ul className="divide-y divide-line text-sm">
          {data.promotions.map((h) => (
            <li key={h.id} className="py-2" data-testid="promo-history-row">
              <div className="flex flex-wrap items-baseline gap-x-3">
                <Link href={`/dashboard/products/promotions/${h.promotion.id}`} className="min-w-0 flex-1 truncate font-medium hover:text-accent">
                  {h.promotion.title}
                </Link>
                <span className="tabular-nums">{rub(h.action_price)}</span>
              </div>
              <div className="flex flex-wrap gap-x-3 text-xs text-muted">
                <span>
                  {fmtDay(h.entered_at.slice(0, 10))} – {h.left_at ? fmtDay(h.left_at.slice(0, 10)) : 'сейчас'}
                </span>
                <span>{PROMO_STATUS[h.status]}</span>
                {h.added_automatically && <span className="text-warn">добавил Ozon</span>}
                {h.margin_in_promo !== null && <span className={marginTone(h.margin_in_promo, lowMargin)}>маржа {pctText(h.margin_in_promo)}</span>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Box>
  )
}

// ── остатки, кластеры, поставки ──
const RISK_TONE: Record<RiskLevel, string> = {
  critical: 'bg-bad/10 text-bad',
  low: 'bg-warn/10 text-warn',
  ok: 'bg-ok/10 text-ok',
  excess: 'bg-accent/10 text-accent',
  unknown: 'bg-line/60 text-muted',
}
const RISK_TITLE: Record<RiskLevel, string> = {
  critical: 'Риск дефицита — срочно',
  low: 'Риск дефицита — скоро',
  ok: 'Дефицита не ожидается',
  excess: 'Запас больше нужного',
  unknown: 'Риск дефицита не посчитать',
}

export function StocksBlock({ data }: { data: ProductStats }) {
  const item = data.detail.item
  return (
    <Box
      title="Остатки и поставки"
      testid="product-stocks"
      more={
        <>
          <Link href="/dashboard/warehouses/supply" className={more}>
            Поставки →
          </Link>
          <Link href="/dashboard/warehouses" className={more}>
            Все остатки →
          </Link>
        </>
      }
    >
      <p className={`rounded-lg px-3 py-2 text-sm ${RISK_TONE[data.risk.level]}`} data-testid="stock-risk" data-level={data.risk.level}>
        <b>{RISK_TITLE[data.risk.level]}.</b> {data.risk.text}
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <Fact label="Доступно" value={qty(item.stock)} />
        <Fact label="В пути на склады" value={qty(item.in_transit)} />
        <Fact label="Продажи в день (30 дн.)" value={item.avg_daily_sales === null ? 'мало данных' : perDay(item.avg_daily_sales)} />
        <Fact label="Хватит на" value={item.days_of_supply === null ? '—' : `${num(item.days_of_supply)} дн.`} />
      </div>
      {data.clusters.length === 0 ? (
        <p className="mt-3 text-sm text-muted">На складах товара нет{item.stock === null ? ' — остатки ещё не загружались' : ''}.</p>
      ) : (
        <div className="-mx-4 mt-3 overflow-x-auto px-4">
          <table className="w-full min-w-[34rem] text-sm" data-testid="product-clusters">
            <thead className="text-left text-xs text-faint">
              <tr>
                <th className="py-1 pr-2 font-normal">Кластер или склад</th>
                <th className="py-1 pr-2 text-right font-normal">Доступно</th>
                <th className="py-1 pr-2 text-right font-normal">В пути</th>
                <th className="py-1 pr-2 text-right font-normal">Хватит на</th>
                <th className="py-1 text-right font-normal">{data.supply_known ? 'Отправить' : 'Поставка'}</th>
              </tr>
            </thead>
            <tbody>
              {data.clusters.map((c) => (
                <ClusterRow key={`${c.type}-${c.name}`} c={c} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-2 text-xs text-faint">
        {data.supply_known
          ? 'Хватит на и «Отправить» — «Поставки»: продажи по кластеру покупателя за 7 и 30 дней, запас и дни в пути — ваши настройки.'
          : 'Отправления из Ozon ещё не загружались — сколько везти в каждый кластер, посчитаем после первой синхронизации отправлений.'}
      </p>
    </Box>
  )
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-bg/60 px-3 py-2">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-0.5 font-medium tabular-nums">{value}</div>
    </div>
  )
}

function ClusterRow({ c }: { c: ClusterStock }) {
  const s = c.supply
  return (
    <tr className="border-t border-line" data-testid="cluster-row">
      <td className="py-1.5 pr-2">
        {c.name} <span className="text-xs text-faint">{c.type === 'FBO' ? 'кластер Ozon' : `свой склад, ${c.type}`}</span>
        {c.warehouses.length > 0 && <span className="block truncate text-xs text-faint">{c.warehouses.join(', ')}</span>}
      </td>
      <td className="py-1.5 pr-2 text-right tabular-nums">{c.present}</td>
      <td className="py-1.5 pr-2 text-right tabular-nums text-muted">{c.in_transit ?? '—'}</td>
      <td className={`py-1.5 pr-2 text-right tabular-nums ${s ? URGENCY[s.urgency].text : 'text-muted'}`}>
        {s ? (s.days_left === null ? 'нет на складе' : `${num(s.days_left)} дн.`) : '—'}
      </td>
      <td className="py-1.5 text-right">
        {s ? (
          <span className="whitespace-nowrap">
            {s.to_order > 0 ? `${s.to_order} шт.` : 'не нужно'}{' '}
            <span className={`rounded px-1.5 py-0.5 text-xs ${URGENCY[s.urgency].badge}`}>{URGENCY[s.urgency].label}</span>
            {s.arrives_late && <span className="block text-xs text-bad">кончится раньше, чем доедет</span>}
          </span>
        ) : (
          <span className="text-faint">—</span>
        )}
      </td>
    </tr>
  )
}

// ── себестоимость: история ──
const SOURCE_LABEL: Record<string, string> = { manual: 'вручную', import: 'из файла' }

export function CostHistory({ data }: { data: ProductStats }) {
  if (data.cost_history.length === 0) return null
  return (
    <div className="mt-4" data-testid="cost-history">
      <h3 className="mb-1 text-xs uppercase tracking-wider text-muted">История себестоимости</h3>
      <ul className="divide-y divide-line text-sm">
        {data.cost_history.map((r, i) => (
          <li key={`${r.effective_from}-${i}`} className="flex flex-wrap items-baseline gap-x-3 py-1.5">
            <span className="w-32 text-muted">{since(r.effective_from)}</span>
            <span className="tabular-nums">{rub(r.cost_price)}</span>
            <span className="text-xs text-faint">
              {fmtDateTime(r.created_at)} · {SOURCE_LABEL[r.source] ?? r.source}
              {r.note ? ` · ${r.note}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}
