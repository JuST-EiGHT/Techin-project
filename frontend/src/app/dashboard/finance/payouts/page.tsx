'use client'
// «Выплаты и расчёты с Ozon» (шаги 3.6, 10.23) — область «Финансы Ozon»: работает без банков и планирования. Следующая
// выплата, баланс, график и прогноз (3.6); за период — баланс у Ozon и начисления по статьям, выплаты и поступления на
// счёт по каждой выплате (сверка 3.11), расхождения с объяснением и переходами; настройка графика выплат.
import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { AnalyticsFilters, useAnalyticsFilter } from '@/components/analytics'
import {
  ForecastTable,
  type Payouts,
  ScheduleBlock,
  type Settlement,
  SettlementIssues,
  SettlementSummary,
  SettlementTable,
  WeekChart,
  mondayOf,
  weekText,
  weekdayShort,
} from '@/components/payouts'
import { Warnings, fmtDay, rub } from '@/components/reports'
import { refreshSyncStatus, timeAgo } from '@/lib/sync'
import { api, errorMessage } from '@/lib/api'
import { addDays, todayMsk } from '@/lib/period'

type SyncStatus = {
  has_active_key: boolean
  balance: {
    last_run: { status: string; error: string | null } | null
    last_success_at: string | null
  }
}

const POLL_MS = 3000

function Figure({ label, value, hint, tone, testid }: { label: string; value: string; hint: string; tone?: string; testid: string }) {
  return (
    <div className="bg-card px-4 py-3" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-xl font-bold tabular-nums ${tone ?? ''}`}>{value}</div>
      <div className="mt-0.5 text-xs text-faint">{hint}</div>
    </div>
  )
}

export default function PayoutsPage() {
  return (
    <Suspense>
      <PayoutsView />
    </Suspense>
  )
}

const DAY = /^\d{4}-\d{2}-\d{2}$/

/** Выбранная на графике неделя (?week=понедельник): что в ней выплачено и ожидается; строки — выделены в таблицах. */
function WeekFocus({ week, data, onClear }: { week: string; data: Payouts; onClear: () => void }) {
  const bar = data.weeks.find((w) => w.week_from === week)
  const end = addDays(week, 6)
  const paid = data.history.filter((p) => mondayOf(p.date) === week)
  const ahead = data.forecast.filter((f) => mondayOf(f.date) === week)
  return (
    <section className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-xl border border-accent/40 bg-accent/5 px-4 py-3 text-sm" data-testid="week-focus">
      <span className="font-semibold">
        Неделя {weekText(week, end)}
      </span>
      {bar && Number(bar.paid) > 0 && <span className="text-ok">выплачено {rub(bar.paid)}</span>}
      {bar && Number(bar.schedule) > 0 && <span className="text-accent">ожидается по графику {rub(bar.schedule)}</span>}
      {bar && Number(bar.estimate) > 0 && <span className="text-muted">оценка {rub(bar.estimate)}</span>}
      <span className="text-xs text-muted">
        {paid.length + ahead.length > 0 ? 'строки этой недели выделены в таблицах' : 'выплат на этой неделе нет'}
      </span>
      <button type="button" onClick={onClear} className="ml-auto text-xs text-muted hover:text-fg" data-testid="week-clear">
        ✕ Снять выделение
      </button>
    </section>
  )
}

function PayoutsView() {
  const params = useSearchParams()
  const router = useRouter()
  const rawWeek = params.get('week')
  const week = rawWeek && DAY.test(rawWeek) ? mondayOf(rawWeek) : null
  const [data, setData] = useState<Payouts | null>(null)
  const [filter, update] = useAnalyticsFilter('90d')
  const { from, to } = filter.range
  const [settlement, setSettlement] = useState<Settlement | null>(null)
  const [sync, setSync] = useState<SyncStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const wasActive = useRef(false)
  const today = todayMsk()

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Payouts>('/ozon/payouts')
      setData(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  // расчёты за период — свой запрос: меняется с фильтром, выплаты 3.6 — нет
  const [version, setVersion] = useState(0)
  useEffect(() => {
    let alive = true
    api
      .get<Settlement>('/ozon/settlement', { params: { date_from: from, date_to: to } })
      .then(({ data }) => alive && setSettlement(data))
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [from, to, version])

  const loadSync = useCallback(async () => {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const nowActive = ['queued', 'running'].includes(data.balance.last_run?.status ?? '')
      if (wasActive.current && !nowActive) {
        load() // загрузка баланса закончилась — перечитываем выплаты и расчёты
        setVersion((v) => v + 1)
      }
      wasActive.current = nowActive
      setSync(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [load])

  useEffect(() => {
    load()
    loadSync()
  }, [load, loadSync])

  // выбрали неделю на графике (здесь или на главной) — показать её выплаты
  const loaded = data !== null && settlement !== null
  useEffect(() => {
    if (!week || !loaded) return
    const target = document.querySelector('[data-highlight]') ?? document.querySelector('[data-testid=week-focus]')
    target?.scrollIntoView({ block: 'center', behavior: 'smooth' })
  }, [week, loaded])

  const active = ['queued', 'running'].includes(sync?.balance.last_run?.status ?? '')
  useEffect(() => {
    if (!active) return
    const t = setInterval(loadSync, POLL_MS)
    return () => clearInterval(t)
  }, [active, loadSync])

  async function syncNow() {
    setStarting(true)
    setError(null)
    try {
      await api.post('/sync/balance')
      refreshSyncStatus() // кнопка «Синхронизация» в шапке сразу покажет загрузку
    } catch (err) {
      if ((err as { response?: { status?: number } }).response?.status !== 409) setError(errorMessage(err))
    } finally {
      setStarting(false)
      await loadSync()
    }
  }

  const lastRun = sync?.balance.last_run
  const lastSuccess = sync?.balance.last_success_at
  const noKey = sync !== null && !sync.has_active_key
  const next = data?.next_payout ?? null
  const stale = data?.last_day ? data.last_day < addDays(today, -1) : false

  return (
    <div className="mx-auto max-w-5xl space-y-6">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="text-2xl font-semibold">Выплаты и расчёты с Ozon</h1>
        {sync?.has_active_key && (
          <div className="flex items-center gap-3 text-sm sm:ml-auto">
            <span className="text-muted" data-testid="balance-updated">
              {active || starting
                ? 'Баланс загружается…'
                : lastSuccess
                  ? `Баланс обновлён ${timeAgo(lastSuccess)}`
                  : 'Баланс ещё не загружался'}
            </span>
            <button
              onClick={syncNow}
              disabled={active || starting}
              className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent disabled:opacity-60"
              data-testid="balance-sync"
            >
              Обновить
            </button>
          </div>
        )}
      </div>

      <FormError message={error ?? (lastRun?.status === 'failed' ? `Последнее обновление не удалось: ${lastRun.error}` : null)} />

      {data && !data.has_data && sync && (
        <section className="rounded-xl border border-line bg-card p-6" data-testid="payouts-empty">
          <h2 className="text-lg font-semibold">
            {noKey ? 'Выплаты появятся после подключения API-ключа Ozon' : active || starting ? 'Загружаем баланс у Ozon…' : 'Баланс у Ozon ещё не загружен'}
          </h2>
          <p className="mt-2 text-sm text-muted">
            Сервис загрузит баланс у Ozon по дням за 90 дней: сколько начислено и сколько и когда выплачено. По этим данным
            он определит ваш график выплат и покажет, когда и сколько придёт дальше.
            {!noKey && !(active || starting) && ' Нажмите «Обновить» — загрузка займёт несколько минут.'}
          </p>
          <p className="mt-2 text-sm text-muted">
            Пока данных нет, ориентир — стандартный график Ozon: {data.schedule.title}.
          </p>
          {noKey && (
            <Link
              href="/dashboard/settings/integrations"
              className="mt-4 inline-block rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              data-testid="connect-key"
            >
              Подключить API-ключ →
            </Link>
          )}
        </section>
      )}

      {data?.has_data && (
        <>
          <div className="grid grid-cols-1 gap-px overflow-hidden rounded-xl border border-line bg-line sm:grid-cols-3" data-testid="payout-figures">
            <div className="bg-card sm:col-span-1" data-testid="figure-next">
              <div className="h-full bg-ok/10 px-4 py-3">
                <div className="text-[11px] uppercase tracking-wider text-muted">Следующая выплата</div>
                {next === null ? (
                  <>
                    <div className="mt-1 text-xl font-bold">—</div>
                    <div className="mt-0.5 text-xs text-faint">выплаты нерегулярные — дату не прогнозируем</div>
                  </>
                ) : (
                  <>
                    <div className="mt-1 text-2xl font-bold tabular-nums text-ok">{next.amount === null ? '—' : rub(next.amount)}</div>
                    <div className="text-sm font-semibold">
                      {fmtDay(next.date)}, {weekdayShort(next.date)}
                      {next.kind === 'estimate' && <span className="ml-1 font-normal text-muted">· оценка</span>}
                    </div>
                    <div className="mt-0.5 text-xs text-faint">
                      {next.amount === null
                        ? 'сумму оценить нечем — нет ни одной полной недели начислений'
                        : next.kind === 'schedule'
                          ? `за продажи ${weekText(next.week_from, next.week_to)}`
                          : 'неделя продаж ещё идёт — среднее за 4 недели'}
                    </div>
                  </>
                )}
              </div>
            </div>
            <Figure
              label="На балансе у Ozon"
              value={rub(data.balance)}
              hint={data.last_day ? `на конец ${fmtDay(data.last_day)}${stale ? ' — данные устарели' : ''}` : ''}
              tone={Number(data.balance) < 0 ? 'text-bad' : undefined}
              testid="figure-balance"
            />
            <Figure label="Выплачено за 30 дней" value={rub(data.paid_30d)} hint="по загруженным дням" testid="figure-paid" />
          </div>

          <Warnings items={data.warnings} />
          <WeekChart weeks={data.weeks} today={today} weekHref={(w) => `/dashboard/finance/payouts?week=${w}`} selected={week} />
          {week && <WeekFocus week={week} data={data} onClear={() => router.replace('/dashboard/finance/payouts', { scroll: false })} />}
          <ForecastTable items={data.forecast.slice(0, 8)} week={week} />
        </>
      )}

      <div className="space-y-3 pt-2" data-testid="settlement">
        <div>
          <h2 className="text-lg font-semibold">Расчёты за период</h2>
          <p className="text-sm text-muted">Сколько Ozon начислил и удержал, сколько выплатил и дошло ли это до счёта.</p>
        </div>
        <AnalyticsFilters
          value={filter}
          onChange={update}
          showProduct={false}
          showCategory={false}
          showGranularity={false}
          showCompare={false}
          kinds={['balance', 'finance', 'bank']}
        />
        {settlement === null ? (
          <p className="text-sm text-muted">Загрузка…</p>
        ) : (
          <>
            <SettlementSummary data={settlement} />
            <SettlementIssues data={settlement} />
            <SettlementTable data={settlement} week={week} />
          </>
        )}
      </div>

      {data && <ScheduleBlock schedule={data.schedule} detected={data.detected} onSaved={load} />}

      {data?.has_data && (
        <p className="text-xs text-faint">
          Баланс и выплаты — из Ozon API («Финансы → Баланс»), обновляются каждые 6 часов. Выплаты по дням сверяем с
          финансовым отчётом Ozon за полмесяца; расхождение — в предупреждениях.
        </p>
      )}
    </div>
  )
}
