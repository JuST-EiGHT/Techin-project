'use client'
// Страница акции (шаг 4.3): товары по марже — выгодные (от 10 %), невыгодные (0–10 %), убыточные, без маржи — и
// кандидаты; вход и выход — через окно подтверждения (предпросмотр → Ozon → журнал).
import Link from 'next/link'
import { useParams, usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PromoActionModal } from '@/components/promo-action'
import { Badge, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { MARGIN_TEXT, PROMO_TABS, fmtDate, tabOf, type PromoProductRow, type PromoTab, type ProductsResponse } from '@/lib/promotions'

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
  const router = useRouter()
  const pathname = usePathname()
  const [data, setData] = useState<ProductsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [action, setAction] = useState<{ row: PromoProductRow; mode: 'join' | 'leave' } | null>(null)
  const [done, setDone] = useState<string | null>(null)

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

  const counts = Object.fromEntries(PROMO_TABS.map((t) => [t.value, 0])) as Record<PromoTab, number>
  data?.items.forEach((r) => (counts[tabOf(r)] += 1))
  const first = PROMO_TABS.find((t) => counts[t.value] > 0)?.value ?? 'ok'
  const asked = params.get('tab') as PromoTab | null
  const tab: PromoTab = asked && PROMO_TABS.some((t) => t.value === asked) ? asked : first
  const setTab = (t: PromoTab) => router.replace(`${pathname}?tab=${t}`, { scroll: false })

  if (error && !data) return <FormError message={error} />
  if (!data) return <p className="text-sm text-muted">Загрузка…</p>

  const p = data.promotion
  const rows = data.items.filter((r) => tabOf(r) === tab)
  const frozenText = p.is_frozen ? `С ${fmtDate(p.freeze_date)} акция заморожена: Ozon не даёт менять список товаров и повышать цены.` : null
  return (
    <div className="mx-auto max-w-7xl space-y-5">
      <Link href="/dashboard/promotions" className="text-sm text-accent hover:underline">
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
        <Stat label="Убыточных" value={String(p.loss)} hint="каждая продажа в минус" tone={p.loss ? 'text-bad' : ''} />
        <Stat label="Можно добавить" value={String(p.candidates)} hint="Ozon предлагает" />
      </div>

      {p.whole_stock_warning && <p className="rounded-lg bg-warn/10 px-3 py-2 text-sm">{p.whole_stock_warning}</p>}
      {frozenText && <p className="rounded-lg bg-line/60 px-3 py-2 text-sm text-muted">{frozenText}</p>}
      {done && (
        <p className="rounded-lg bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="promo-done">
          {done}
        </p>
      )}
      <FormError message={error} />

      <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist">
        {PROMO_TABS.filter((t) => t.value !== 'none' || counts.none > 0).map((t) => (
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

      {rows.length === 0 ? (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">
          {tab === 'candidates' ? 'Ozon сейчас не предлагает добавить в эту акцию другие ваши товары.' : 'Таких товаров в акции нет.'}
        </p>
      ) : (
        <section className="overflow-hidden rounded-xl border border-line bg-card">
          <div className="overflow-x-auto">
            <table className="w-full min-w-[46rem] text-[13px]" data-testid="promo-table">
              <thead className="border-b border-line text-left text-xs text-muted">
                <tr>
                  <th className="px-3 py-2 font-normal">Товар</th>
                  <th className="px-3 py-2 text-right font-normal">{tab === 'candidates' ? 'Цена для входа' : 'Цена в акции'}</th>
                  <th className="px-3 py-2 text-right font-normal" title="Минимальная цена в Ozon">
                    Мин. цена
                  </th>
                  <th className="px-3 py-2 text-right font-normal">Остаток</th>
                  <th className="px-3 py-2 text-right font-normal" title="Калькулятор цены: с логистикой, рекламой и налогами">
                    Маржа
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
      <p className="text-xs text-faint">
        Выгодные — маржа в акции от 10 %, невыгодные — от 0 до 10 %, убыточные — ниже нуля. У кандидатов маржа — при максимальной цене для
        участия. Добавить и убрать товар можно только после подтверждения; каждое действие записывается в журнал.
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
    <tr className="border-b border-line last:border-b-0" data-testid="promo-product">
      <td className="max-w-[16rem] px-3 py-2">
        <Link href={`/dashboard/products?product=${r.product.id}`} className="block truncate font-medium hover:text-accent">
          {r.product.offer_id}
        </Link>
        <div className="flex items-center gap-1.5 text-xs text-muted">
          <span className="truncate">{r.product.name}</span>
          {r.added_automatically && <Badge tone="accent">авто</Badge>}
        </div>
      </td>
      <td className={td}>
        {r.is_active ? rub(price) : `до ${rub(price)}`}
        <div className="text-xs text-faint">
          {rub(r.price)}
          {r.discount_pct !== null && r.is_active && ` · −${r.discount_pct} %`}
        </div>
      </td>
      <td className={`${td} ${r.below_min_price ? 'text-bad' : 'text-muted'}`} title={r.below_min_price ? 'Цена в акции ниже минимальной в Ozon' : undefined}>
        {rub(r.min_price)}
      </td>
      <td className={td}>
        {r.stock_on_hand}
        {wholeStock && r.is_active && r.stock_on_hand > 0 && <div className="text-xs text-warn">может уйти весь</div>}
        {!wholeStock && r.is_active && r.stock !== null && <div className="text-xs text-faint">в акции {r.stock}</div>}
      </td>
      <td className={`${td} font-medium ${r.margin_level ? MARGIN_TEXT[r.margin_level] : 'text-muted'}`} data-testid="promo-margin">
        {pctText(r.margin_in_promo)}
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
