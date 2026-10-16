'use client'
// Главная — дашборд Ozon (шаг 3.7): только данные Ozon. Прогноз остатка и свои операции — в «Планировании» (3.10).
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Change, FigureLink, Hint, type OzonDashboard, RevenueChart } from '@/components/dashboard'
import { SourceNote } from '@/components/finance'
import { WeekChart, weekText, weekdayShort } from '@/components/payouts'
import { fmtDay, pctText, rub } from '@/components/reports'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { type Preset, presetRange, todayMsk } from '@/lib/period'
import { useCostLabel } from '@/lib/settings'
import { type RunningOut, STATUS, num, perDay, productHref, qty } from '@/lib/stocks'

type BoardPreset = Exclude<Preset, 'custom'>

const PERIODS: { value: BoardPreset; label: string; against: string }[] = [
  { value: 'week', label: 'Эта неделя', against: 'к прошлой неделе' },
  { value: 'month', label: 'Этот месяц', against: 'к прошлому месяцу' },
  { value: 'prev_month', label: 'Прошлый месяц', against: 'к месяцу раньше' },
]

export default function DashboardPage() {
  return (
    <Suspense>
      <Dashboard />
    </Suspense>
  )
}

function Dashboard() {
  const { user } = useAuth()
  const params = useSearchParams()
  const router = useRouter()
  const costLabel = useCostLabel()
  const [today] = useState(todayMsk)
  const raw = params.get('period')
  const preset = PERIODS.find((p) => p.value === raw) ?? PERIODS[1]
  const range = presetRange(preset.value, today)
  const [data, setData] = useState<OzonDashboard | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setError(null)
    api
      .get<OzonDashboard>('/dashboard/ozon', { params: { date_from: range.from, date_to: range.to } })
      .then(({ data }) => alive && setData(data))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [range.from, range.to])

  const shown = data && data.date_from === range.from && data.date_to === range.to ? data : null
  const finance = `/dashboard/finance?period=${preset.value}`
  const setup = shown?.setup
  const nothing = setup && !setup.has_key && !setup.has_reports && !setup.has_balance

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <p className="text-sm text-muted">Добро пожаловать, {user?.full_name || user?.email}</p>
        <h1 className="text-2xl font-semibold">Сводка Ozon</h1>
      </div>

      {setup && !setup.tax_set && (
        <div
          data-testid="tax-banner"
          className="flex flex-wrap items-center gap-3 rounded-lg border border-warn/40 bg-warn/10 px-4 py-3 text-sm text-warn"
        >
          Налог не задан: укажите, кто вы и какая у вас система налогообложения, — без этого сервис не посчитает налог в P&amp;L.
          <Link href="/dashboard/settings/taxes" className="ml-auto underline">
            Указать →
          </Link>
        </div>
      )}

      {nothing ? (
        <Connect />
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <div className="flex flex-wrap gap-1 rounded-lg border border-line bg-card p-1" role="group" aria-label="Период">
              {PERIODS.map((p) => (
                <button
                  key={p.value}
                  type="button"
                  aria-pressed={p.value === preset.value}
                  onClick={() => router.replace(`/dashboard?period=${p.value}`, { scroll: false })}
                  className={`rounded-md px-3 py-1.5 text-sm ${p.value === preset.value ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
                  data-testid={`preset-${p.value}`}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <span className="text-sm text-muted" data-testid="range-label">
              {fmtDay(range.from)} – {fmtDay(range.to)}
              {shown && <span className="text-faint"> · сравниваем с {fmtDay(shown.prev_from)} – {fmtDay(shown.prev_to)}</span>}
            </span>
          </div>

          <FormError message={error} />

          {shown && (
            <>
              <Figures data={shown} finance={finance} against={preset.against} costLabel={costLabel} />
              <div className="flex flex-wrap items-start gap-x-4 gap-y-1">
                <div className="min-w-0 flex-1">
                  <SourceNote source={shown.data_source} coverage={shown.coverage} />
                </div>
                <Link href="/dashboard/reports" className="text-xs text-accent hover:underline">
                  Отчёты Ozon →
                </Link>
              </div>
              {shown.compare_note && (
                <p className="text-xs text-faint" data-testid="compare-note">
                  {shown.compare_note}
                </p>
              )}
              {shown.data_source !== 'none' && (
                <RevenueChart
                  series={shown.series}
                  against={preset.against}
                  // точка → товары за её день или неделю; «Подробнее →» → товары за весь период
                  pointHref={(p) => `/dashboard/finance?period=custom&from=${p.date}&to=${p.date_to}&tab=products`}
                  moreHref={`${finance}&tab=products`}
                />
              )}
              {shown.payouts.has_data ? (
                <WeekChart
                  weeks={shown.payouts.weeks}
                  today={today}
                  weekHref={(w) => `/dashboard/payouts?week=${w}`}
                  moreHref="/dashboard/payouts"
                />
              ) : (
                <section className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="payouts-empty">
                  <h2 className="font-semibold">Выплаты Ozon</h2>
                  <p className="mt-1 text-muted">
                    {shown.setup.has_key
                      ? 'Баланс у Ozon ещё не загружен. Он загрузится сам в течение нескольких часов — или откройте «Выплаты Ozon» и нажмите «Обновить».'
                      : 'Баланс у Ozon и даты выплат приходят только из Ozon API — в отчётах XLSX их нет. Подключите ключ, чтобы видеть, когда и сколько придёт.'}
                  </p>
                  <Link
                    href={shown.setup.has_key ? '/dashboard/payouts' : '/dashboard/settings/api-keys'}
                    className="mt-2 inline-block text-accent hover:underline"
                  >
                    {shown.setup.has_key ? 'Открыть «Выплаты Ozon» →' : 'Подключить API-ключ →'}
                  </Link>
                </section>
              )}
              {shown.setup.has_key && <RunningOutWidget />}
            </>
          )}
        </>
      )}
      {!shown && !error && !nothing && <p className="text-sm text-muted">Загружаем…</p>}
    </div>
  )
}

function Figures({ data, finance, against, costLabel }: { data: OzonDashboard; finance: string; against: string; costLabel: string }) {
  const { revenue, received, profit, margin } = data.figures
  const pay = data.payouts
  const next = pay.next_payout
  const was = (v: string | null) => (v === null ? 'в прошлом периоде нет данных' : `было ${rub(v)}`)
  const profitValue = profit.value === null ? null : Number(profit.value)
  return (
    <div
      className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-5"
      data-testid="dashboard-figures"
    >
      <FigureLink href={finance} label="Продано" value={rub(revenue.value)} tone={revenue.value === null ? undefined : 'ok'} testid="figure-revenue">
        <Change pct={revenue.change_pct} against={against} />
        <Hint>{revenue.value === null ? 'нет данных за период' : was(revenue.previous)}</Hint>
      </FigureLink>
      <FigureLink
        href={finance}
        label="К получению от Ozon"
        value={rub(received.value)}
        tone={received.value !== null && Number(received.value) < 0 ? 'bad' : undefined}
        testid="figure-received"
      >
        <Change pct={received.change_pct} against={against} />
        <Hint>{received.value === null ? 'нет данных за период' : 'начислено за период после удержаний Ozon'}</Hint>
      </FigureLink>
      <FigureLink
        href={finance}
        label={profit.kind === 'net' ? 'Остаток (прибыль)' : 'Прибыль до налога'}
        value={rub(profit.value)}
        tone={profitValue === null ? undefined : profitValue < 0 ? 'bad' : 'ok'}
        testid="figure-profit"
        highlight={profitValue === null ? '' : profitValue < 0 ? 'bg-bad/10' : 'bg-ok/10'}
      >
        {margin.value !== null && <div className="text-sm font-semibold">Маржа {pctText(margin.value)}</div>}
        <Change pct={profit.change_pct} against={against} />
        {profit.value !== null && data.missing_cost_count > 0 && (
          <Hint tone="warn">
            {costLabel}: не найдена у {data.missing_cost_count} тов. — прибыль завышена
          </Hint>
        )}
        {profit.value !== null && profit.kind === 'before_tax' && <Hint>налог не задан — без налога</Hint>}
      </FigureLink>
      <FigureLink href="/dashboard/payouts" label="На балансе у Ozon" value={rub(pay.balance)} testid="figure-balance">
        <Hint>{pay.last_day ? `на конец ${fmtDay(pay.last_day)}` : data.setup.has_key ? 'баланс ещё не загружен' : 'нужен API-ключ Ozon'}</Hint>
      </FigureLink>
      <FigureLink
        href="/dashboard/payouts"
        label="Следующая выплата"
        value={next?.amount ? rub(next.amount) : '—'}
        testid="figure-next"
      >
        {next ? (
          <>
            <div className="text-sm font-semibold">
              {fmtDay(next.date)}, {weekdayShort(next.date)}
              {next.kind === 'estimate' && <span className="ml-1 font-normal text-muted">· оценка</span>}
            </div>
            <Hint>{next.kind === 'schedule' ? `за продажи ${weekText(next.week_from, next.week_to)}` : pay.schedule_title}</Hint>
          </>
        ) : (
          <Hint>{pay.has_data ? 'выплаты нерегулярные — дату не прогнозируем' : 'нет данных о выплатах'}</Hint>
        )}
      </FigureLink>
    </div>
  )
}

/** Заканчиваются (шаг 5.1): «Критично» и «Мало» по порогам пользователя — меньше всего дней запаса первыми. */
function RunningOutWidget() {
  const [data, setData] = useState<RunningOut | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<RunningOut>('/stocks/running-out', { params: { limit: 5 } })
      .then(({ data }) => setData(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  if (error) return <FormError message={error} />
  if (!data) return null
  const total = data.critical + data.low
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="running-out">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Заканчиваются</h2>
        {data.sales_known && data.stocks_known && (
          <span className="text-sm">
            <span className={data.critical ? 'text-bad' : 'text-muted'}>критично: {data.critical}</span>
            <span className="text-faint"> · </span>
            <span className={data.low ? 'text-warn' : 'text-muted'}>мало: {data.low}</span>
          </span>
        )}
        <Link href={total ? '/dashboard/products?filter=low_stock' : '/dashboard/warehouses'} className="ml-auto text-sm text-accent hover:underline">
          {total ? 'Все →' : 'Склады →'}
        </Link>
      </div>
      {!data.stocks_known ? (
        <p className="px-4 py-3 text-sm text-muted">Остатки ещё не загружены — они обновляются из Ozon API каждые 30 минут.</p>
      ) : !data.sales_known ? (
        <p className="px-4 py-3 text-sm text-muted">
          Мало данных о продажах — дни запаса считаются, когда за последние 30 дней есть начисления хотя бы за 7 дней.
        </p>
      ) : total === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="running-out-none">
          Запаса хватает всем товарам — меньше чем на {data.thresholds.low} дн. продаж нет ни у одного.
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {data.items.map((p) => (
            <li key={p.product.id}>
              <Link href={productHref(p.product.id)} className="flex items-start gap-3 px-4 py-2 hover:bg-bg" data-testid="running-out-item">
                <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${STATUS[p.status ?? 'no_data'].dot}`} aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm font-medium">{p.product.offer_id}</span>
                  {p.product.name && <span className="block truncate text-xs text-muted">{p.product.name}</span>}
                </span>
                <span className="shrink-0 text-right text-sm">
                  <span className={`block font-medium ${STATUS[p.status ?? 'no_data'].text}`}>
                    {p.available === 0 ? 'кончился' : `на ${num(p.days_of_supply)} дн.`}
                  </span>
                  <span className="block text-xs text-faint">
                    {qty(p.available)} · {perDay(p.avg_daily_sales)} в день{p.in_transit ? ` · в пути ${p.in_transit}` : ''}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

/** Ничего не подключено: что даёт ключ и что дают отчёты — пользователь выбирает одно или оба. */
function Connect() {
  return (
    <section className="rounded-xl border border-line bg-card p-6" data-testid="connect">
      <h2 className="text-lg font-semibold">Подключите данные Ozon</h2>
      <p className="mt-1 text-sm text-muted">
        Главная показывает продажи, прибыль, баланс и выплаты Ozon. Данные можно взять двумя способами — можно один, можно
        оба: где есть отчёт, сервис берёт его, остальные дни — из API.
      </p>
      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Link href="/dashboard/settings/api-keys" className="rounded-lg border border-line p-4 hover:border-accent" data-testid="connect-key">
          <div className="font-semibold">API-ключ Ozon →</div>
          <p className="mt-1 text-sm text-muted">
            Продажи, начисления, остатки, баланс и выплаты загружаются сами каждые несколько часов. Только так видны баланс у
            Ozon и даты выплат. Цифры по API могут немного расходиться с кабинетом.
          </p>
        </Link>
        <Link href="/dashboard/reports/upload" className="rounded-lg border border-line p-4 hover:border-accent" data-testid="connect-reports">
          <div className="font-semibold">Отчёты XLSX из кабинета →</div>
          <p className="mt-1 text-sm text-muted">
            Отчёт по начислениям за неделю или месяц — P&amp;L точно как в личном кабинете Ozon. Загружаете вручную, когда
            Ozon сформирует отчёт.
          </p>
        </Link>
      </div>
    </section>
  )
}
