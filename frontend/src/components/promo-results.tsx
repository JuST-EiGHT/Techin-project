'use client'
// Результаты акций (шаг 10.16): продажи, выручка и прибыль за время участия против такого же числа дней до входа.
// Дни без начислений — пропуск, а не ноль: сравниваем средние за день с данными. Строка — для истории участия и
// страницы товара; блок — для страницы акции (по акции в целом и по каждому товару).
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { fmtDay, rub, rubSigned } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { type ParticipationResult, type PromotionResults } from '@/lib/promotions'
import { productHref } from '@/lib/stocks'

const pctSigned = (v: string | null) => (v === null ? '' : `${Number(v) > 0 ? '▲' : Number(v) < 0 ? '▼' : ''}${Math.abs(Number(v)).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} %`)
const tone = (v: string | null) => (v === null ? 'text-muted' : Number(v) > 0 ? 'text-ok' : Number(v) < 0 ? 'text-bad' : 'text-muted')
const days = (n: number) => `${n} ${n % 10 === 1 && n % 100 !== 11 ? 'день' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'дня' : 'дней'}`

/** Одна строка: «Продано 12 шт. против 6 до акции ▲100 % · прибыль +2 400 ₽». */
export function ResultLine({ r }: { r: ParticipationResult | null }) {
  if (!r) return null
  if (!r.during.days_with_data) return <span className="text-xs text-faint">результат: за дни участия ещё нет начислений</span>
  if (!r.known)
    return (
      <span className="text-xs text-muted" data-testid="result-line">
        за {days(r.during.days_with_data)} — {r.during.qty} шт., {rub(r.during.revenue)}; до акции данных нет — сравнить не с чем
      </span>
    )
  return (
    <span className="text-xs" data-testid="result-line">
      <span className="text-muted">
        продано {r.during.qty} шт. против {r.base!.qty} до акции{' '}
      </span>
      <span className={tone(r.qty_change_pct)}>{pctSigned(r.qty_change_pct)}</span>
      {r.profit_change !== null && (
        <>
          <span className="text-muted"> · прибыль </span>
          <span className={tone(r.profit_change)}>{rubSigned(r.profit_change)}</span>
        </>
      )}
    </span>
  )
}

/** Результаты акции: в целом и по товарам. */
export function PromotionResultsBlock({ promotionId }: { promotionId: string }) {
  const [data, setData] = useState<PromotionResults | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<PromotionResults>(`/promotions/${promotionId}/results`)
      .then(({ data: d }) => setData(d))
      .catch((err) => setError(errorMessage(err)))
  }, [promotionId])

  if (!data) return error ? <FormError message={error} /> : null
  if (data.items.length === 0) return null
  const t = data.totals
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="promo-results">
      <h2 className="font-semibold">Результаты акции</h2>
      {t.products === 0 ? (
        <p className="text-sm text-muted">Сравнить пока не с чем: нет начислений за дни участия или за такие же дни до входа в акцию.</p>
      ) : (
        <div className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-3" data-testid="promo-results-totals">
          <Total label="Продано, шт." now={String(t.qty)} before={String(t.qty_before)} change={pctSigned(t.qty_change_pct)} changeTone={tone(t.qty_change_pct)} />
          <Total label="Выручка" now={rub(t.revenue)} before={rub(t.revenue_before)} change={pctSigned(t.revenue_change_pct)} changeTone={tone(t.revenue_change_pct)} />
          <Total
            label="Прибыль до налога"
            now={t.profit === null ? '—' : rub(t.profit)}
            before={t.profit_before === null ? '—' : rub(t.profit_before)}
            change={t.profit_change === null ? 'нет себестоимости' : rubSigned(t.profit_change)}
            changeTone={tone(t.profit_change)}
          />
        </div>
      )}
      {t.products > 0 && (
        <p className="text-xs text-faint">
          По {t.products} {t.products === 1 ? 'товару' : 'товарам'} с данными до и во время акции; «до» приведено к тому же числу дней с данными.
        </p>
      )}
      <div className="-mx-4 overflow-x-auto px-4">
        <table className="w-full min-w-[38rem] text-[13px]" data-testid="promo-results-table">
          <thead className="text-left text-xs text-faint">
            <tr>
              <th className="py-1 pr-2 font-normal">Товар</th>
              <th className="py-1 pr-2 font-normal">Участие</th>
              <th className="py-1 pr-2 text-right font-normal">Продано</th>
              <th className="py-1 pr-2 text-right font-normal">Выручка</th>
              <th className="py-1 text-right font-normal">Прибыль</th>
            </tr>
          </thead>
          <tbody>
            {data.items.map((i) => {
              const r = i.result
              return (
                <tr key={i.participation_id} className="border-t border-line" data-testid="promo-result-row">
                  <td className="max-w-[12rem] py-1.5 pr-2">
                    <Link href={productHref(i.product.id)} className="block truncate font-medium hover:text-accent">
                      {i.product.offer_id}
                    </Link>
                  </td>
                  <td className="whitespace-nowrap py-1.5 pr-2 text-muted">
                    {fmtDay(r.during.date_from)} – {i.is_current ? 'сейчас' : fmtDay(r.during.date_to)}
                    <span className="block text-xs text-faint">
                      с данными {r.during.days_with_data} из {r.during.days} дн.
                    </span>
                  </td>
                  <Cell now={String(r.during.qty)} before={r.known ? String(r.base!.qty) : null} change={r.known ? pctSigned(r.qty_change_pct) : ''} changeTone={tone(r.qty_change_pct)} />
                  <Cell now={rub(r.during.revenue)} before={r.known ? rub(r.base!.revenue) : null} change={r.known ? pctSigned(r.revenue_change_pct) : ''} changeTone={tone(r.revenue_change_pct)} />
                  <Cell
                    now={r.during.profit === null ? '—' : rub(r.during.profit)}
                    before={r.known && r.base!.profit !== null ? rub(r.base!.profit) : null}
                    change={r.profit_change === null ? '' : rubSigned(r.profit_change)}
                    changeTone={tone(r.profit_change)}
                    last
                  />
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-faint">{data.note}</p>
    </section>
  )
}

function Total({ label, now, before, change, changeTone }: { label: string; now: string; before: string; change: string; changeTone: string }) {
  return (
    <div className="bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className="mt-1 text-lg font-bold tabular-nums">{now}</div>
      <div className="text-xs tabular-nums">
        <span className="text-faint">до акции {before}</span> <span className={changeTone}>{change}</span>
      </div>
    </div>
  )
}

function Cell({ now, before, change, changeTone, last }: { now: string; before: string | null; change: string; changeTone: string; last?: boolean }) {
  return (
    <td className={`whitespace-nowrap py-1.5 text-right tabular-nums ${last ? '' : 'pr-2'}`}>
      {now}
      <span className="block text-xs">
        {before === null ? <span className="text-faint">до — нет данных</span> : <span className="text-faint">до {before} </span>}
        {change && <span className={changeTone}>{change}</span>}
      </span>
    </td>
  )
}
