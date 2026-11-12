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
import { SearchBox } from '@/components/filters'
import { pctText, rub } from '@/components/reports'
import { Badge, HelpTip, PageHeader, Stat, StatGrid, Tabs, buttonClass } from '@/components/ui'
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
  const [search, setSearch] = useState('')

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
  const q = search.trim().toLowerCase()
  const rows = data.items.filter((r) => tabOf(r) === tab && (!q || `${r.product.offer_id} ${r.product.name ?? ''} ${r.product.ozon_sku ?? ''}`.toLowerCase().includes(q)))
  const frozenText = p.is_frozen ? `С ${fmtDate(p.freeze_date)} акция заморожена: Ozon не даёт менять список товаров и повышать цены.` : null
  const discount = p.discount_value && Number(p.discount_value) > 0 ? ` · скидка ${Number(p.discount_value).toLocaleString('ru-RU')}${p.discount_type === 'PERCENT' ? ' %' : ' ₽'}` : ''
  const avg = p.avg_margin === null ? undefined : Number(p.avg_margin) < 0 ? 'bad' : Number(p.avg_margin) < 10 ? undefined : 'ok'
  return (
    <div className="mx-auto max-w-7xl">
      <Link href="/dashboard/products/promotions" className="text-sm text-muted hover:text-accent">
        ← Акции
      </Link>
      {/* 10.27c — как страница акции в Ozon: заголовок с датами, сводка, вкладки со счётчиками, поиск, таблица */}
      <div className="mt-2">
        <PageHeader
          title={p.title}
          titleTestid="promo-title"
          description={
            <span data-testid="promo-title-sub">
              {fmtDate(p.date_from)} — {fmtDate(p.date_to)}
              {discount}
              {p.freeze_date && !p.is_frozen && ` · заморозка ${fmtDate(p.freeze_date)}`}
            </span>
          }
          actions={
            <>
              <span className="flex flex-wrap gap-1">
                {p.urgent && <Badge tone="bad">СРОЧНО — заморозка {fmtDate(p.freeze_date)}</Badge>}
                {p.is_participating ? <Badge tone="ok">Участвую</Badge> : <Badge tone="muted">Не участвую</Badge>}
                {p.whole_stock && <Badge tone="warn">весь остаток</Badge>}
                {p.is_frozen && <Badge tone="muted">заморожена</Badge>}
              </span>
              <button
                type="button"
                onClick={exportXlsx}
                disabled={exporting}
                className={buttonClass('secondary', 'sm')}
                title="Все товары акции и кандидаты с ценами, минимальной ценой для участия, маржой и прибылью"
                data-testid="promo-export"
              >
                {exporting ? 'Готовим…' : 'Скачать Excel'}
              </button>
            </>
          }
        />
      </div>

      <div className="space-y-4">
        <StatGrid testid="promo-summary">
          <Stat label="Участвует" value={String(p.products_active)} hint={p.auto_added ? `из них авто: ${p.auto_added}` : 'ваших товаров'} />
          <Stat label="Средняя маржа" value={pctText(p.avg_margin)} tone={avg} hint={p.no_margin ? `без маржи: ${p.no_margin}` : 'по калькулятору цены'} />
          <Stat label="Ниже минимальной" value={String(p.loss)} tone={p.loss ? 'bad' : undefined} hint="каждая продажа в минус" />
          <Stat label="Можно добавить" value={String(p.candidates)} hint="Ozon предлагает" />
        </StatGrid>

        {p.loss > 0 && (
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg bg-bad/10 px-3 py-2 text-sm text-bad" data-testid="promo-below-min">
            <span>
              <b>
                {p.loss} {p.loss === 1 ? 'товар' : 'товара'} ниже минимальной цены
              </b>{' '}
              — каждая продажа в минус: уберите из акции или поднимите свою цену.
            </span>
            {tab !== 'negative' && (
              <button type="button" onClick={() => setTab('negative')} className="font-medium underline">
                Показать
              </button>
            )}
          </p>
        )}
        {p.whole_stock_warning && <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm text-warn">{p.whole_stock_warning}</p>}
        {frozenText && <p className="rounded-lg bg-soft px-3 py-2 text-sm text-muted">{frozenText}</p>}
        {done && (
          <p className="rounded-lg bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="promo-done">
            {done}
          </p>
        )}
        <FormError message={error} />

        <section className="rounded-xl border border-line bg-card shadow-card">
          <div className="px-4 pt-2">
            <Tabs
              label="Товары акции"
              value={tab}
              onChange={setTab}
              items={promoTabs(low)
                .filter((t) => t.value !== 'none' || counts.none > 0)
                .map((t) => ({ value: t.value, label: t.label, count: counts[t.value], title: t.hint, tone: t.value === 'negative' ? ('bad' as const) : undefined }))}
              itemTestid={(t) => `promo-tab-${t}`}
            />
          </div>
          <div className="flex flex-wrap items-center gap-2 px-4 py-3">
            <SearchBox value={search} onChange={setSearch} placeholder="Название, артикул, SKU" testid="promo-product-search" className="w-full sm:w-72" />
            <span className="ml-auto flex items-center gap-1.5 text-xs text-muted">
              Какие цены в таблице
              <HelpTip label="Какие цены в таблице и как считается прибыль" testid="promo-prices-help">
                <PricesHelp lowPct={lowPct} />
              </HelpTip>
            </span>
          </div>
          {rows.length === 0 ? (
            <p className="border-t border-line px-4 py-6 text-center text-sm text-muted">
              {q ? 'Ничего не нашлось — проверьте артикул или название.' : tab === 'candidates' ? 'Ozon сейчас не предлагает добавить в эту акцию другие ваши товары.' : 'Таких товаров в акции нет.'}
            </p>
          ) : (
            <div className="overflow-x-auto border-t border-line">
              <table className="w-full min-w-[66rem] text-[13px]" data-testid="promo-table">
                <thead className="bg-soft/60 text-left text-xs text-muted">
                  <tr>
                    <th className="px-3 py-2.5 font-medium">Товар</th>
                    <th className="px-3 py-2.5 text-right font-medium" title={tab === 'candidates' ? 'Максимальная цена, по которой Ozon примет товар в акцию; под ней — ваша цена без акции' : 'Цена для покупателя во время акции; под ней — ваша цена без акции и скидка'}>
                      {tab === 'candidates' ? 'Цена входа' : 'Цена в акции'}
                      <div className="text-[11px] font-normal text-faint">ваша цена · скидка</div>
                    </th>
                    <th className="px-3 py-2.5 font-medium" title="В каких ещё идущих акциях товар уже участвует: цена там, до какого дня акция; дешевле, чем здесь, — жёлтым">
                      Есть в других акциях
                      <div className="text-[11px] font-normal text-faint">цена · до какого дня</div>
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium" title="Калькулятор цены: ниже — каждая продажа в минус; под ней — цена с вашей целевой маржой">
                      Мин. для участия
                      <div className="text-[11px] font-normal text-faint">целевая</div>
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium" title="Минимальная цена, которую вы задали в Ozon: ниже неё Ozon цену в акциях не опустит">
                      Мин. в Ozon
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium">
                      Остаток
                      <div className="text-[11px] font-normal text-faint">в акции</div>
                    </th>
                    <th className="px-3 py-2.5 text-right font-medium" title="Калькулятор цены при цене в акции: с комиссией, логистикой, рекламой, доп. расходами и налогами">
                      Маржа в акции
                      <div className="text-[11px] font-normal text-faint">прибыль с 1 шт.</div>
                    </th>
                    <th className="px-3 py-2.5" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <Row key={r.product.id} r={r} wholeStock={p.whole_stock} frozen={p.is_frozen} onAction={(mode) => setAction({ row: r, mode })} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data.margin_note && <p className="border-t border-line px-4 py-2 text-xs text-faint">{data.margin_note}</p>}
        </section>
        <PromotionResultsBlock promotionId={p.id} />
      </div>

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

/** Какие цены в таблице — словами (правка владельца 06.11: «непонятно, какие цены показаны»); 10.27c — в подсказке «?». */
function PricesHelp({ lowPct }: { lowPct: string }) {
  return (
    <span className="block space-y-1.5">
      <span className="block"><b className="text-fg">Ваша цена</b> — цена товара в Ozon без акции.</span>
      <span className="block"><b className="text-fg">Цена в акции</b> — по ней купят во время акции; от неё считаются комиссия, логистика, маржа и прибыль.</span>
      <span className="block"><b className="text-fg">Цена входа</b> (у кандидатов) — самая высокая цена, по которой Ozon примет товар в акцию.</span>
      <span className="block"><b className="text-fg">Мин. для участия</b> — без прибыли и без убытка (себестоимость, комиссия, логистика, реклама, налоги); под ней — целевая с вашей маржой.</span>
      <span className="block"><b className="text-fg">Мин. в Ozon</b> — ваша минимальная цена в Ozon: ниже неё Ozon цену в акциях не опустит.</span>
      <span className="block"><b className="text-fg">Есть в других акциях</b> — цена там и до какого дня; жёлтым — дешевле, чем здесь.</span>
      <span className="block">Выгодные — маржа от {lowPct}, невыгодные — от 0 до {lowPct}, убыточные — ниже нуля. Добавить и убрать товар — только после подтверждения, всё пишется в журнал.</span>
    </span>
  )
}

function Row({ r, wholeStock, frozen, onAction }: { r: PromoProductRow; wholeStock: boolean; frozen: boolean; onAction: (mode: 'join' | 'leave') => void }) {
  const td = 'whitespace-nowrap px-3 py-2 text-right tabular-nums'
  const price = r.is_active ? r.action_price : r.max_action_price ?? r.action_price
  return (
    <tr
      className={r.below_breakeven ? 'bg-bad/5' : undefined}
      data-testid="promo-product"
      data-below={r.below_breakeven ? 'true' : undefined}
    >
      <td className="max-w-[16rem] px-3 py-2">
        <Link href={`/dashboard/products/${r.product.id}`} className="block truncate font-medium hover:text-accent">
          {r.product.offer_id}
        </Link>
        <div className="flex items-center gap-1.5 text-xs text-faint">
          <span className="truncate">{r.product.name}</span>
          {r.added_automatically && <Badge tone="warn">авто</Badge>}
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
            className={`${buttonClass('secondary', 'sm')} hover:border-bad hover:text-bad`}
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
            className={buttonClass('primary', 'sm')}
            data-testid="promo-join"
          >
            Добавить
          </button>
        )}
      </td>
    </tr>
  )
}
