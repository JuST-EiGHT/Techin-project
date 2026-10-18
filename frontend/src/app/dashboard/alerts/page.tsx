'use client'
// Уведомления (шаг 7.2): аномалии, которые раз в час находит сервис (шаг 7.1), — фильтры, группы по типу, «Ок»,
// «Отметить все прочитанными». Telegram — шаг 7.3.
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useCallback, useEffect, useState } from 'react'

import { AlertCard } from '@/components/alerts'
import { FormError } from '@/components/AuthForm'
import { api, errorMessage } from '@/lib/api'
import {
  type AlertGroup,
  type AlertStatus,
  type Anomaly,
  type AnomaliesPage,
  GROUPS,
  type Severity,
  acknowledgeAll,
  useAlertCounts,
  whenText,
} from '@/lib/alerts'

const PAGE = 100
const STATUSES: { value: AlertStatus; label: string }[] = [
  { value: 'active', label: 'Активные' },
  { value: 'resolved', label: 'Закрытые' },
  { value: 'all', label: 'Все' },
]

export default function AlertsRoute() {
  return (
    <Suspense>
      <Alerts />
    </Suspense>
  )
}

/** Аномалии одного типа подряд — в порядке, в каком пришли (у активных красные первыми). */
function byType(items: Anomaly[]): { type: string; title: string; items: Anomaly[] }[] {
  const groups = new Map<string, { type: string; title: string; items: Anomaly[] }>()
  for (const a of items) {
    const g = groups.get(a.type) ?? { type: a.type, title: a.type_title, items: [] }
    g.items.push(a)
    groups.set(a.type, g)
  }
  return Array.from(groups.values())
}

function Alerts() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const counts = useAlertCounts()
  const status = (STATUSES.find((s) => s.value === params.get('status'))?.value ?? 'active') as AlertStatus
  const severity = (['red', 'yellow'].includes(params.get('severity') ?? '') ? params.get('severity') : null) as Severity | null
  const group = (GROUPS.find((g) => g.value === params.get('group'))?.value ?? null) as AlertGroup | null
  const unreadOnly = params.get('unread') === '1' && status === 'active'
  const [data, setData] = useState<AnomaliesPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const query = useCallback(
    (offset: number) => ({
      status,
      ...(severity ? { severity } : {}),
      ...(group ? { group } : {}),
      ...(unreadOnly ? { acknowledged: false } : {}),
      limit: PAGE,
      offset,
    }),
    [status, severity, group, unreadOnly],
  )

  const load = useCallback(() => {
    setError(null)
    api
      .get<AnomaliesPage>('/anomalies', { params: query(0) })
      .then(({ data }) => setData(data))
      .catch((err) => setError(errorMessage(err)))
  }, [query])
  // новые аномалии (опрос колокольчика раз в минуту) — список перечитывается сам
  useEffect(() => {
    load()
  }, [load, counts?.active, counts?.unread])

  const more = async () => {
    if (!data) return
    try {
      const { data: next } = await api.get<AnomaliesPage>('/anomalies', { params: query(data.items.length) })
      setData({ ...next, items: [...data.items, ...next.items] })
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params.toString())
    if (value === null) next.delete(key)
    else next.set(key, value)
    if (key === 'status' && value !== 'active') next.delete('unread')
    const q = next.toString()
    router.replace(q ? `${pathname}?${q}` : pathname, { scroll: false })
    setNotice(null)
  }

  const replace = (a: Anomaly) =>
    setData((d) => d && { ...d, items: unreadOnly ? d.items.filter((x) => x.id !== a.id) : d.items.map((x) => (x.id === a.id ? a : x)) })

  const readAll = async () => {
    setBusy(true)
    setError(null)
    try {
      const n = await acknowledgeAll({ ...(severity ? { severity } : {}), ...(group ? { group } : {}) })
      setNotice(n ? `Отмечено прочитанными: ${n}. Аномалии закроются сами, когда всё вернётся в норму.` : 'Непрочитанных нет.')
      load()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const unreadHere = data ? data.items.filter((a) => !a.resolved_at && !a.is_acknowledged).length : 0
  const chip = (active: boolean) =>
    `rounded-full border px-3 py-1 text-sm ${active ? 'border-accent bg-accent/10 text-accent' : 'border-line text-muted hover:text-fg'}`

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Уведомления</h1>
        <p className="mt-1 text-sm text-muted">
          Раз в час сервис проверяет продажи, цены, остатки, акции и выплаты и сообщает, если что-то пошло не так. «Ок» — видел:
          уведомление больше не считается, а закроется само, когда всё вернётся в норму.
        </p>
        <p className="mt-1 text-xs text-faint" data-testid="alerts-checked">
          {data?.checked_at ? `Последняя проверка — ${whenText(data.checked_at)}. ` : 'Проверок ещё не было. '}
          <Link href="/dashboard/settings#anomalies" className="text-accent hover:underline">
            Пороги — когда считать проблемой →
          </Link>{' '}
          ·{' '}
          <Link href="/dashboard/settings/notifications" className="text-accent hover:underline" data-testid="alerts-telegram">
            Получать в Telegram →
          </Link>
        </p>
      </div>

      <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Какие уведомления">
        {STATUSES.map((s) => (
          <button
            key={s.value}
            type="button"
            role="tab"
            aria-selected={status === s.value}
            onClick={() => setParam('status', s.value === 'active' ? null : s.value)}
            className={`-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm ${status === s.value ? 'border-accent text-accent' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`alerts-status-${s.value}`}
          >
            {s.label}
            {s.value === 'active' && counts ? <span className={status === s.value ? '' : 'text-faint'}> {counts.active}</span> : null}
          </button>
        ))}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap gap-2" role="group" aria-label="Важность">
          <button type="button" className={chip(severity === null)} onClick={() => setParam('severity', null)} data-testid="alerts-severity-all">
            Все
          </button>
          <button type="button" className={chip(severity === 'red')} onClick={() => setParam('severity', 'red')} data-testid="alerts-severity-red">
            <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-bad" aria-hidden />
            Красные{status === 'active' && counts ? ` ${unreadOnly ? counts.red : counts.red_active}` : ''}
          </button>
          <button type="button" className={chip(severity === 'yellow')} onClick={() => setParam('severity', 'yellow')} data-testid="alerts-severity-yellow">
            <span className="mr-1.5 inline-block h-2 w-2 rounded-full bg-warn" aria-hidden />
            Жёлтые{status === 'active' && counts ? ` ${unreadOnly ? counts.yellow : counts.yellow_active}` : ''}
          </button>
          {status === 'active' && (
            <label className="ml-auto flex cursor-pointer items-center gap-2 text-sm text-muted">
              <input
                type="checkbox"
                checked={unreadOnly}
                onChange={(e) => setParam('unread', e.target.checked ? '1' : null)}
                className="accent-accent"
                data-testid="alerts-unread"
              />
              Только без «Ок»
            </label>
          )}
        </div>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Что проверяли">
          <button type="button" className={chip(group === null)} onClick={() => setParam('group', null)} data-testid="alerts-group-all">
            Все разделы
          </button>
          {GROUPS.map((g) => (
            <button
              key={g.value}
              type="button"
              title={g.hint}
              className={chip(group === g.value)}
              onClick={() => setParam('group', g.value)}
              data-testid={`alerts-group-${g.value}`}
            >
              {g.label}
              <span className="text-faint"> {data?.by_group[g.value] ?? 0}</span>
            </button>
          ))}
        </div>
      </div>

      <FormError message={error} />
      {notice && (
        <p className="rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm text-ok" data-testid="alerts-notice">
          {notice}
        </p>
      )}

      {status !== 'resolved' && unreadHere > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={readAll}
            className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
            data-testid="alerts-read-all"
          >
            Отметить все прочитанными{severity || group ? ' — в этом фильтре' : ''}
          </button>
          <span className="text-xs text-faint">«Ок» у всех активных{severity || group ? ' по выбранному фильтру' : ''}: закрываться они будут сами.</span>
        </div>
      )}

      {!data && !error && <p className="text-sm text-muted">Загружаем…</p>}
      {data && data.items.length === 0 && <Empty status={status} filtered={Boolean(severity || group || unreadOnly)} checked={data.checked_at} />}
      {data &&
        byType(data.items).map((g) => (
          <section key={g.type} className="space-y-2" data-testid="alerts-group">
            <h2 className="text-sm font-semibold text-muted">
              {g.title} <span className="font-normal text-faint">· {g.items.length}</span>
            </h2>
            <div className="grid gap-3 md:grid-cols-2">
              {g.items.map((a) => (
                <AlertCard key={a.id} anomaly={a} onChange={replace} />
              ))}
            </div>
          </section>
        ))}
      {data && data.items.length < data.total && (
        <button type="button" onClick={more} className="rounded-lg border border-line px-4 py-1.5 text-sm hover:border-accent hover:text-accent" data-testid="alerts-more">
          Показать ещё ({data.total - data.items.length})
        </button>
      )}
    </div>
  )
}

function Empty({ status, filtered, checked }: { status: AlertStatus; filtered: boolean; checked: string | null }) {
  if (filtered)
    return (
      <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted" data-testid="alerts-empty">
        По этому фильтру уведомлений нет.
      </p>
    )
  if (status === 'resolved')
    return (
      <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted" data-testid="alerts-empty">
        Закрытых уведомлений пока нет: здесь будут те, что вернулись в норму.
      </p>
    )
  return (
    <section className="rounded-xl border border-line bg-card p-5 text-sm" data-testid="alerts-empty">
      <h2 className="font-semibold">{checked ? 'Всё спокойно' : 'Проверок ещё не было'}</h2>
      <p className="mt-1 text-muted">
        {checked
          ? 'Активных аномалий нет: продажи, цены, остатки, акции и выплаты в пределах ваших порогов.'
          : 'Сервис проверяет данные раз в час — когда подключён ключ Ozon или включено «Планирование». Первая проверка пройдёт в течение часа.'}
      </p>
      {!checked && (
        <Link href="/dashboard/settings/api-keys" className="mt-2 inline-block text-accent hover:underline">
          Подключить ключ Ozon →
        </Link>
      )}
    </section>
  )
}
