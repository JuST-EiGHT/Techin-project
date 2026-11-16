'use client'
// P&L как отчёт о прибылях и убытках (шаг 10.22): выручка, возвраты, корректировки Ozon, удержания Ozon по группам,
// себестоимость, прибыль от продаж на Ozon, прочие расходы бизнеса из «Операций» (статьи «в P&L»), прибыль до налога,
// налог по профилю, чистая прибыль и рентабельность. Строка Ozon раскрывается до статей начислений, расходы бизнеса — до
// статей с переходом к их операциям. Без «Планирования» расходов бизнеса нет — так и подписано.
import Link from 'next/link'
import { useState } from 'react'

import { opsHref } from '@/components/planning/types'
import { pctText, rub, rubSigned } from '@/components/reports'
import { HelpTip } from '@/components/ui'
import { MARGIN, PROFIT } from '@/lib/profit-terms'

export type BusinessCategory = { id: string; name: string; amount: string; children: BusinessCategory[] }

export type BusinessExpenses = {
  total: string
  operations: number
  categories: BusinessCategory[]
  excluded: { id: string; name: string; amount: string }[]
  uncategorized: { count: number; amount: string }
}

export type StatementRow = {
  key: string
  title: string
  amount: string | null
  kind: 'line' | 'subtotal' | 'total'
  pct: string | null
  types: { name: string; amount: string }[]
  categories: BusinessCategory[]
  /** Только во фронтенде: «· маржа после налога 12 %» у итоговой строки. */
  margin_title?: string
}

export type Statement = {
  rows: StatementRow[]
  revenue: string
  profit_ozon: string
  business: string | null
  profit_before_tax: string
  tax: string | null
  vat: string | null
  net: string | null
  margin: string | null
  tax_title: string | null
  tax_lines: string[]
}

const GRID = 'grid grid-cols-[minmax(0,1fr)_3.75rem_6.75rem] items-center gap-x-1.5 px-2 sm:gap-x-2 sm:px-3 sm:grid-cols-[minmax(0,1fr)_5.5rem_9.5rem]'
const NOTES: Record<string, string> = {
  profit_ozon: 'до налога и без расходов бизнеса — как на главной и в «Аналитике»',
  ozon_total: 'начислено Ozon за период',
}

/** Названия итоговых строк — одни на всех страницах (lib/profit-terms, решение владельца 16.11.2026). */
function termOf(key: string, hasBusiness: boolean) {
  if (key === 'profit_ozon') return PROFIT.ozon
  if (key === 'profit_before_tax') return PROFIT.business
  if (key === 'net') return hasBusiness ? PROFIT.businessNet : PROFIT.ozonNet
  return null
}

/** Итог без «+», минус — тем же знаком «−», что у строк. */
const totalText = (v: string) => (Number(v) < 0 ? `−${rub(String(-Number(v)))}` : rub(v))

function Amount({ row }: { row: StatementRow }) {
  if (row.amount === null) return <span className="text-right text-muted">—</span>
  const v = Number(row.amount)
  const tone = row.kind !== 'line' ? '' : row.key === 'tax' || row.key === 'vat' ? 'text-warn' : v > 0 ? 'text-ok' : v < 0 ? 'text-bad' : ''
  return <span className={`whitespace-nowrap text-right tabular-nums ${tone}`}>{row.kind === 'line' ? rubSigned(row.amount) : totalText(row.amount)}</span>
}

function Category({ c, range, depth }: { c: BusinessCategory; range: { from: string; to: string }; depth: number }) {
  return (
    <>
      <div className={`${GRID} py-1 text-[13px]`} data-testid="statement-category">
        <Link href={opsHref(range.from, range.to, c.id)} className="min-w-0 truncate text-muted hover:text-accent" style={{ paddingLeft: `${depth * 1}rem` }} title="Операции статьи за период">
          {c.name} →
        </Link>
        <span />
        <span className="text-right tabular-nums text-muted">{rubSigned(-Number(c.amount))}</span>
      </div>
      {c.children.map((x) => (
        <Category key={x.id} c={x} range={range} depth={depth + 1} />
      ))}
    </>
  )
}

function Line({ row, range, hasBusiness = false }: { row: StatementRow; range: { from: string; to: string }; hasBusiness?: boolean }) {
  const term = termOf(row.key, hasBusiness)
  const detail = row.types.length > 1 || (row.types.length === 1 && row.types[0].name !== row.title) || row.categories.length > 0
  const [open, setOpen] = useState(row.key === 'business')
  const sub = row.kind === 'subtotal'
  const total = row.kind === 'total'
  const net = total && row.amount !== null ? Number(row.amount) : null
  const box = total
    ? `mt-2 border font-semibold ${net === null ? 'border-line' : net < 0 ? 'border-bad bg-bad/10 text-bad' : 'border-ok bg-ok/10 text-ok'}`
    : sub
      ? 'bg-soft/60 font-semibold'
      : ''
  return (
    <div data-testid="statement-row" data-key={row.key}>
      <div className={`${GRID} rounded-md py-2 text-[13px] sm:text-sm ${box}`}>
        <span className="min-w-0">
          {detail ? (
            <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="text-left hover:text-accent" data-testid="statement-toggle">
              <span className="inline-block w-4 text-faint">{open ? '▾' : '▸'}</span>
              {row.title}
            </button>
          ) : (
            <span className={sub || total ? '' : 'pl-4'}>
              {term ? term.label : row.title}
              {row.margin_title && ` ${row.margin_title}`}
              {term && (
                <span className="ml-1.5 inline-block align-middle font-normal">
                  <HelpTip label={`Как считается: ${term.label}`}>{term.help}</HelpTip>
                </span>
              )}
            </span>
          )}
          {NOTES[row.key] && <span className="block text-xs font-normal text-faint">{NOTES[row.key]}</span>}
        </span>
        <span className="whitespace-nowrap text-right text-xs tabular-nums text-muted sm:text-sm">{pctText(row.pct)}</span>
        <Amount row={row} />
      </div>
      {open &&
        row.types.map((t) => (
          <div key={t.name} className={`${GRID} py-1 text-[13px] text-muted`} data-testid="statement-type">
            <span className="min-w-0 truncate pl-8">{t.name}</span>
            <span />
            <span className="text-right tabular-nums">{rubSigned(t.amount)}</span>
          </div>
        ))}
      {open && row.categories.map((c) => <Category key={c.id} c={c} range={range} depth={2} />)}
    </div>
  )
}

/** Строка «Прочие расходы бизнеса», когда «Планирование» выключено: расходов в P&L нет — и почему. */
function NoBusiness() {
  return (
    <div className={`${GRID} py-1.5 text-[13px] text-muted sm:text-sm`} data-testid="statement-no-business">
      <span className="min-w-0 pl-4">
        Прочие расходы бизнеса
        <span className="block text-xs text-faint">
          не учитываются: аренда, зарплата и другие расходы войдут в P&amp;L, если вести их в «Операциях».{' '}
          <Link href="/dashboard/planning" className="text-accent hover:underline">
            Включить «Планирование» →
          </Link>
        </span>
      </span>
      <span className="text-right">—</span>
      <span className="text-right">—</span>
    </div>
  )
}

export function StatementTable({ statement, business, range }: {
  statement: Statement
  business: BusinessExpenses | null
  range: { from: string; to: string }
}) {
  const rows = statement.rows
  const at = rows.findIndex((r) => r.key === 'profit_ozon')
  const loose = business?.uncategorized
  const hasBusiness = business !== null && statement.business !== null
  return (
    <section className="rounded-xl border border-line bg-card pb-3 shadow-card" data-testid="pnl-statement">
      <div className="flex flex-wrap items-baseline gap-x-3 px-5 pb-3 pt-4">
        <h2 className="text-base font-semibold">Прибыли и убытки</h2>
        <span className="text-sm text-muted">% — от выручки; ▸ — из чего сложилась строка</span>
      </div>
      <div className="px-1 sm:px-2">
        <div className={`${GRID} rounded-md bg-soft/60 py-2 text-xs font-medium text-muted`}>
          <span>Статья</span>
          <span className="text-right">%</span>
          <span className="text-right">Сумма</span>
        </div>
        {rows.slice(0, at + 1).map((r) => (
          <Line key={r.key} row={r} range={range} hasBusiness={hasBusiness} />
        ))}
        {business === null && <NoBusiness />}
        {rows.slice(at + 1).map((r) =>
          r.key === 'net' ? (
            <Line
              key={r.key}
              row={{ ...r, margin_title: statement.margin === null ? undefined : `· ${MARGIN.after.label.toLowerCase()} ${pctText(statement.margin)}` }}
              range={range}
              hasBusiness={hasBusiness}
            />
          ) : r.key === 'tax' && r.amount === null ? (
            <div key={r.key} className={`${GRID} py-1.5 text-[13px] sm:text-sm`} data-testid="statement-row" data-key="tax">
              <span className="pl-4">
                Налог — <span className="text-warn">не задан</span>{' '}
                <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                  указать →
                </Link>
              </span>
              <span className="text-right text-muted">—</span>
              <span className="text-right text-muted">—</span>
            </div>
          ) : (
            <Line key={r.key} row={r} range={range} hasBusiness={hasBusiness} />
          ),
        )}
      </div>
      {business && (business.excluded.length > 0 || (loose && loose.count > 0)) && (
        <ul className="mx-4 mt-3 space-y-1 text-xs text-muted" data-testid="statement-business-notes">
          {business.excluded.length > 0 && (
            <li>
              Не входят в P&amp;L: {business.excluded.map((x) => `${x.name} ${rub(x.amount)}`).join(', ')} — себестоимость и налог здесь
              уже посчитаны, кредиты и вывод владельцу — не расход.{' '}
              <Link href="/dashboard/operations/categories" className="text-accent hover:underline">
                Изменить в «Статьях» →
              </Link>
            </li>
          )}
          {loose && loose.count > 0 && (
            <li className="text-warn">
              Расходов без статьи: {loose.count} на {rub(loose.amount)} — в P&amp;L не вошли.{' '}
              <Link href={`/dashboard/operations?category=none&from=${range.from}&to=${range.to}`} className="text-accent hover:underline">
                Разметить →
              </Link>
            </li>
          )}
        </ul>
      )}
      {statement.tax_lines.length > 0 && (
        <details className="mx-4 mt-3 text-xs text-muted" data-testid="statement-tax-lines">
          <summary className="cursor-pointer text-accent">Как посчитан налог</summary>
          <ul className="mt-1 space-y-0.5">
            {statement.tax_lines.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
