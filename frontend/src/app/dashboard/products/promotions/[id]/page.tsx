'use client'
// Страница акции (шаг 4.3): товары по марже — выгодные (от 10 %), невыгодные (0–10 %), убыточные, без маржи — и
// кандидаты; вход и выход — через окно подтверждения (предпросмотр → Ozon → журнал).
// 10.16: минимальная и целевая цена для участия у каждого товара (калькулятор цены), «ниже минимальной» — заметно,
// «весь остаток» — сколько выйдет, если уйдёт весь; результаты акции — во время против такого же периода до.
import Link from 'next/link'
import { useParams, usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PromoActionModal } from '@/components/promo-action'
import { PromotionResultsBlock } from '@/components/promo-results'
import { Badge, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import {
  MARGIN_TEXT,
  type OtherPromotion,
  PROMO_TAB_VALUES,
  fmtDate,
  promoTabs,
  tabOf,
  type PromoProductRow,
  type PromoTab,
  type ProductsResponse,
} from '@/lib/promotions'
import { useLowMargin } from '@/lib/settings'

export default function PromotionRoute() {
  return (
    <Suspense>
      <PromotionPage />
    </Suspense>
  )
}

function PromotionPage() {
  const { id } = useParams<{ id: string }>()
  const params = useSearchParams()
  const low = useLowMargin()
  const router = useRouter()
  const pathname = usePathname()
  const [data, setData] = useState<ProductsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [action, setAction] = useState<{ row: PromoProductRow; mode: 'join' | 'leave' } | null>(null)
  const [done, setDone] = useState<string | null>(null)
  const [exporting, setExporting] = useState(false)

  async function exportXlsx() {
    setExporting(true)
    try {
      await saveFile(api.get(`/promotions/${id}/products/export`, { responseType: 'blob' }), 'promotion.xlsx')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setExporting(false)
    }
  }

  const load = useCallback(() => {
    api
      .get<ProductsResponse>(`/promotions/${id}/products`)
      .then((r) => {
        setData(r.data)
        setError(null)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  const counts = Object.fromEntries(PROMO_TAB_VALUES.map((t) => [t, 0])) as Record<PromoTab, number>
  data?.items.forEach((r) => (counts[tabOf(r)] += 1))
  const first = PROMO_TAB_VALUES.find((t) => counts[t] > 0) ?? 'ok'
  const asked = params.get('tab') as PromoTab | null
  const tab: PromoTab = asked && PROMO_TAB_VALUES.includes(asked) ? asked : first
  const lowPct = `${String(low).replace('.', ',')} %`
  const setTab = (t: PromoTab) => router.replace(`${pathname}?tab=${t}`, { scroll: false })

  if (error && !data) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>

  const p = data.promotion
  const rows = data.items.filter((r) => tabOf(r) === tab)
  const frozenText = p.is_frozen ? `С ${fmtDate(p.freeze_date)} акция заморожена: Ozon не даёт менять список товаров и повышать цены.` : null
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Link href="/dashboard/products/promotions" className="text-sm text-accent hover:underline">
        ← Все акции
      </Link>
      <div>
        <div className="flex flex-wrap items-center gap-1.5">
          {p.urgent && <Badge tone="bad">СРОЧНО — заморозка {fmtDate(p.freeze_date)}</Badge>}
          {p.is_participating ? <Badge tone="ok">Участвую</Badge> : <Badge tone="muted">Не участвую</Badge>}
          {p.whole_stock && <Badge tone="warn">весь остаток</Badge>}
          {p.is_frozen && <Badge tone="muted">заморожена</Badge>}
        </div>
        <h1 className="mt-2 text-2xl font-semibold" data-testid="promo-title">
          {p.title}
        </h1>
        <p className="text-sm text-muted">
          {fmtDate(p.date_from)} — {fmtDate(p.date_to)}
          {p.discount_value && Number(p.discount_value) > 0 && ` · скидка ${Number(p.discount_value).toLocaleString('ru-RU')}${p.discount_type === 'PERCENT' ? ' %' : ' ₽'}`}
          {p.freeze_date && !p.is_frozen && ` · заморозка ${fmtDate(p.freeze_date)}`}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-4" data-testid="promo-summary">
        <Stat label="Участвует" value={String(p.products_active)} hint={p.auto_added ? `из них авто: ${p.auto_added}` : 'ваших товаров'} />
        <Stat
          label="Средняя маржа"
          value={pctText(p.avg_margin)}
          hint={p.no_margin ? `без маржи: ${p.no_margin}` : 'по калькулятору цены'}
          tone={p.avg_margin === null ? '' : Number(p.avg_margin) < 0 ? 'text-bad' : Number(p.avg_margin) < 10 ? 'text-warn' : 'text-ok'}
        />
        <Stat label="Ниже минимальной" value={String(p.loss)} hint="цена в акции — каждая продажа в минус" tone={p.loss ? 'text-bad' : ''} />
        <Stat label="Можно добавить" value={String(p.candidates)} hint="Ozon предлагает" />
      </div>

      {p.loss > 0 && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="promo-below-min">
          <span>
            <b>Цена в акции ниже минимальной у {p.loss} {p.loss === 1 ? 'товара' : 'товаров'}</b> — каждая продажа в минус. Минимальная цена для
            участия — в колонке «Мин. для участия»: уберите товар из акции или поднимите себе цену, чтобы скидка не уводила ниже.
          </span>
          {tab !== 'negative' && (
            <button type="button" onClick={() => setTab('negative')} className="font-medium underline">
              Показать
            </button>
          )}
        </div>
      )}
      {p.whole_stock_warning && <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm">{p.whole_stock_warning}</p>}
      {frozenText && <p className="rounded-lg bg-line/60 px-3 py-2 text-sm text-muted">{frozenText}</p>}
      {done && (
        <p className="rounded-lg bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="promo-done">
          {done}
        </p>
      )}
      <FormError message={error} />

      <PricesHelp />

      <div className="flex items-end gap-2 border-b border-line">
      <div className="flex min-w-0 flex-1 gap-1 overflow-x-auto" role="tablist">
        {promoTabs(low).filter((t) => t.value !== 'none' || counts.none > 0).map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={tab === t.value}
            title={t.hint}
            onClick={() => setTab(t.value)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${tab === t.value ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`promo-tab-${t.value}`}
          >
            {t.label} <span className={tab === t.value ? '' : 'text-faint'}>{counts[t.value]}</span>
          </button>
        ))}
      </div>
        <button
          type="button"
          onClick={exportXlsx}
          disabled={exporting}
          className="mb-1.5 shrink-0 rounded-lg border border-line px-3 py-1 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
          title="Все товары акции и кандидаты с ценами, минимальной ценой для участия, маржой и прибылью"
          data-testid="promo-export"
        >
          {exporting ? 'Готовим…' : 'Скачать Excel'}
        </button>
      </div>

      {rows.length === 0 ? (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">
          {tab === 'candidates' ? 'Ozon сейчас не предлагает добавить в эту акцию другие ваши товары.' : 'Таких товаров в акции нет.'}
        </p>
      ) : (
        <section className="overflow-hidden rounded-xl border border-line bg-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[66rem] text-[13px]" data-testid="promo-table">
              <thead className="border-b border-line text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-normal">Товар</th>
                  <th
                    className="px-3 py-2 text-right font-normal"
                    title={
                      tab === 'candidates'
                        ? 'Максимальная цена, по которой Ozon примет товар в акцию; под ней — ваша цена без акции'
                        : 'Цена для покупателя во время акции — от неё считаются маржа и прибыль; под ней — ваша цена без акции и скидка'
                    }
                  >
                    {tab === 'candidates' ? 'Цена входа' : 'Цена в акции'}
                    <div className="text-[11px] text-faint">ваша цена · скидка</div>
                  </th>
                  <th className="px-3 py-2 font-normal" title="В каких ещё идущих акциях товар уже участвует: цена там, до какого дня акция; дешевле, чем здесь, — жёлтым">
                    Уже участвует
                    <div className="text-[11px] text-faint">цена · до какого дня</div>
                  </th>
                  <th className="px-3 py-2 text-right font-normal" title="Калькулятор цены: ниже — каждая продажа в минус; под ней — цена с вашей целевой маржой">
                    Мин. для участия
                  </th>
                  <th className="px-3 py-2 text-right font-normal" title="Минимальная цена, которую вы задали в Ozon: ниже неё Ozon цену в акциях не опустит">
                    Мин. в Ozon
                  </th>
                  <th className="px-3 py-2 text-right font-normal">Остаток</th>
                  <th className="px-3 py-2 text-right font-normal" title="Калькулятор цены при цене в акции: с комиссией, логистикой, рекламой, доп. расходами и налогами">
                    Маржа в акции
                    <div className="text-[11px] text-faint">прибыль с 1 шт.</div>
                  </th>
                  <th className="px-3 py-2" />
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Row key={r.product.id} r={r} wholeStock={p.whole_stock} frozen={p.is_frozen} onAction={(mode) => setAction({ row: r, mode })} />
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {data.margin_note && <p className="text-xs text-faint">{data.margin_note}</p>}
      <PromotionResultsBlock promotionId={p.id} />
      <p className="text-xs text-faint">
        Выгодные — маржа в акции от {lowPct}, невыгодные — от 0 до {lowPct} (порог — в «Настройках»), убыточные — ниже нуля. У кандидатов маржа — при максимальной цене для
        участия. Минимальная цена для участия — без прибыли и без убытка (себестоимость, комиссия, логистика, реклама, налоги), целевая — с
        вашей маржой. Добавить и убрать товар можно только после подтверждения; каждое действие записывается в журнал.
      </p>

      {action && (
        <PromoActionModal
          promotionId={p.id}
          promotionTitle={p.title}
          product={action.row.product}
          mode={action.mode}
          onClose={() => setAction(null)}
          onDone={(message) => {
            setAction(null)
            setDone(message)
            load()
          }}
        />
      )}
    </div>
  )
}

/** «Уже участвует»: все другие идущие акции товара — цена там (дешевле, чем здесь, — жёлтым: покупатель увидит ту
 * цену), до какого дня акция и заморожена ли. По ним видно, где товар в цикле акций и когда освободится. */
function OtherPromos({ items }: { items: OtherPromotion[] }) {
  if (items.length === 0) return <span className="text-faint">нет</span>
  return (
    <ul className="space-y-1" data-testid="promo-other">
      {items.map((o) => (
        <li key={o.id} className="max-w-[15rem]" data-testid={o.cheaper ? 'promo-other-cheaper' : undefined}>
          <div className="flex items-baseline gap-1.5">
            <Link href={`/dashboard/products/promotions/${o.id}`} className={`truncate hover:underline ${o.cheaper ? 'text-warn' : 'hover:text-accent'}`} title={o.title}>
              {o.title}
            </Link>
            <span className={`shrink-0 tabular-nums ${o.cheaper ? 'font-medium text-warn' : ''}`}>{o.action_price ? rub(o.action_price) : '—'}</span>
          </div>
          <div className="text-[11px] text-faint">
            {o.cheaper && <span className="text-warn">дешевле, чем здесь · </span>}
            {o.date_to ? `до ${fmtDate(o.date_to)}` : 'без даты окончания'}
            {o.is_frozen && ' · заморожена'}
          </div>
        </li>
      ))}
    </ul>
  )
}

/** Какие цены в таблице — словами (правка владельца: «непонятно, какие цены показаны»). */
function PricesHelp() {
  return (
    <details className="rounded-lg border border-line bg-card px-3 py-2 text-sm" data-testid="promo-prices-help">
      <summary className="cursor-pointer select-none text-muted hover:text-fg">Какие цены в таблице и как считается прибыль</summary>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-muted">
        <li>
          <b className="text-fg">Ваша цена</b> — цена товара в Ozon без акции.
        </li>
        <li>
          <b className="text-fg">Цена в акции</b> — по ней покупатель купит товар во время акции. От неё считаются комиссия, логистика,
          маржа и прибыль.
        </li>
        <li>
          <b className="text-fg">Цена входа</b> (у кандидатов) — «до …»: самая высокая цена, по которой Ozon примет товар в акцию. Добавляем
          по ней; ниже — меньше прибыль.
        </li>
        <li>
          <b className="text-fg">Мин. для участия</b> — наш расчёт: цена без прибыли и без убытка (себестоимость, комиссия, логистика,
          реклама, доп. расходы, налоги). Ниже — каждая продажа в минус. Под ней — целевая цена с вашей маржой.
        </li>
        <li>
          <b className="text-fg">Мин. в Ozon</b> — минимальная цена, которую вы задали в Ozon: ниже неё Ozon цену в акциях не опустит.
        </li>
        <li>
          <b className="text-fg">Маржа в акции</b> и <b className="text-fg">прибыль с 1 шт.</b> — при цене в акции, до налога на прибыль.
        </li>
        <li>
          <b className="text-fg">Уже участвует</b> — в каких ещё идущих акциях товар: цена там, до какого дня акция, заморожена ли.
          Жёлтым — там дешевле, чем здесь: покупатель увидит ту цену.
        </li>
      </ul>
    </details>
  )
}

function Stat({ label, value, hint, tone = '' }: { label: string; value: string; hint: string; tone?: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-lg font-bold tabular-nums ${tone}`}>{value}</div>
      <div className="mt-0.5 text-xs text-faint">{hint}</div>
    </div>
  )
}

function Row({ r, wholeStock, frozen, onAction }: { r: PromoProductRow; wholeStock: boolean; frozen: boolean; onAction: (mode: 'join' | 'leave') => void }) {
  const td = 'whitespace-nowrap px-3 py-2 text-right tabular-nums'
  const price = r.is_active ? r.action_price : r.max_action_price ?? r.action_price
  return (
    <tr
      className={`border-b border-l-2 border-line last:border-b-0 ${r.below_breakeven ? 'border-l-bad bg-bad/5' : 'border-l-transparent'}`}
      data-testid="promo-product"
      data-below={r.below_breakeven ? 'true' : undefined}
    >
      <td className="max-w-[16rem] px-3 py-2">
        <Link href={`/dashboard/products/${r.product.id}`} className="block truncate font-medium hover:text-accent">
          {r.product.offer_id}
        </Link>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <span className="truncate">{r.product.name}</span>
          {r.added_automatically && <Badge tone="accent">авто</Badge>}
        </div>
      </td>
      <td className={td} data-testid="promo-price">
        <span className="font-medium">{r.is_active ? rub(price) : `до ${rub(price)}`}</span>
        <div className="text-xs text-faint">
          ваша {rub(r.price)}
          {r.discount_pct !== null && r.is_active && ` · −${r.discount_pct} %`}
        </div>
      </td>
      <td className="px-3 py-2 align-top text-xs" data-testid="promo-other-cell">
        <OtherPromos items={r.other_promotions ?? []} />
      </td>
      <td className={`${td} ${r.below_breakeven ? 'font-semibold text-bad' : ''}`} data-testid="promo-breakeven">
        {r.breakeven_price === null ? <span className="text-muted">нет данных</span> : rub(r.breakeven_price)}
        {r.below_breakeven && price && r.breakeven_price ? (
          <div className="text-xs font-normal">ниже на {rub(String(Number(r.breakeven_price) - Number(price)))}</div>
        ) : (
          r.target_price && <div className="text-xs text-faint">целевая {rub(r.target_price)}</div>
        )}
      </td>
      <td className={`${td} ${r.below_min_price ? 'text-bad' : 'text-muted'}`} title={r.below_min_price ? 'Цена в акции ниже минимальной в Ozon' : undefined}>
        {rub(r.min_price)}
      </td>
      <td className={td}>
        {r.stock_on_hand}
        {wholeStock && r.is_active && r.stock_on_hand > 0 && (
          <div className={`text-xs ${r.whole_stock_profit !== null && Number(r.whole_stock_profit) < 0 ? 'text-bad' : 'text-warn'}`} data-testid="promo-whole-stock">
            может уйти весь
            {r.whole_stock_profit !== null && `: ${Number(r.whole_stock_profit) < 0 ? 'убыток' : 'прибыль'} ${rub(String(Math.abs(Number(r.whole_stock_profit))))}`}
          </div>
        )}
        {!wholeStock && r.is_active && r.stock !== null && <div className="text-xs text-faint">в акции {r.stock}</div>}
      </td>
      <td className={`${td} font-medium ${r.margin_level ? MARGIN_TEXT[r.margin_level] : 'text-muted'}`} data-testid="promo-margin">
        {pctText(r.margin_in_promo)}
        {r.unit_profit !== null && <div className="text-xs font-normal">{rub(r.unit_profit)} с шт.</div>}
      </td>
      <td className="px-3 py-2 text-right">
        {r.is_active ? (
          <button
            type="button"
            onClick={() => onAction('leave')}
            disabled={frozen}
            title={frozen ? 'Акция заморожена — список товаров не поменять' : undefined}
            className="rounded-lg border border-line px-3 py-1 text-sm hover:border-bad hover:text-bad disabled:opacity-40"
            data-testid="promo-leave"
          >
            Убрать
          </button>
        ) : (
          <button
            type="button"
            onClick={() => onAction('join')}
            disabled={frozen}
            title={frozen ? 'Акция заморожена — список товаров не поменять' : undefined}
            className="rounded-lg bg-accent px-3 py-1 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            data-testid="promo-join"
          >
            Добавить
          </button>
        )}
      </td>
    </tr>
  )
}
