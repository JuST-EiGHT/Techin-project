'use client'
// Уведомления об аномалиях (шаг 7.2): колокольчик в шапке, карточка аномалии, виджет «Требуют внимания» на главной.
import { Bell, Check } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { useDismiss } from '@/hooks/useDismiss'
import { api, errorMessage } from '@/lib/api'
import {
  type Anomaly,
  type AnomaliesPage,
  RESOLUTION,
  SEVERITY,
  acknowledge,
  acknowledgeAll,
  metricText,
  useAlertCounts,
  whenText,
} from '@/lib/alerts'

const ALERTS = '/dashboard/alerts'

/** Кнопка «Ок»: видел — аномалия остаётся активной, пока не вернётся в норму, но в счётчик больше не входит. */
function OkButton({ anomaly, onDone, small }: { anomaly: Anomaly; onDone: (a: Anomaly) => void; small?: boolean }) {
  const [busy, setBusy] = useState(false)
  return (
    <button
      type="button"
      disabled={busy}
      title="Видел — больше не считать в уведомлениях. Аномалия закроется сама, когда всё вернётся в норму."
      onClick={async (e) => {
        e.preventDefault()
        e.stopPropagation()
        setBusy(true)
        try {
          onDone(await acknowledge(anomaly.id))
        } finally {
          setBusy(false)
        }
      }}
      className={`shrink-0 rounded-lg border border-line font-medium hover:border-accent hover:text-accent disabled:opacity-60 ${
        small ? 'px-2 py-0.5 text-xs' : 'px-3 py-1 text-sm'
      }`}
      data-testid="alert-ok"
    >
      Ок
    </button>
  )
}

// ── колокольчик ──
export function AlertBell() {
  const counts = useAlertCounts()
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Anomaly[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const button = useRef<HTMLButtonElement>(null)
  const close = useCallback(() => setOpen(false), [])
  useDismiss(open, close, box)
  // Esc — закрыть, даже если фокус ушёл со списка (после «Ок» кнопка исчезает)
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      close()
      button.current?.focus()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, close])

  const load = useCallback(() => {
    setError(null)
    api
      .get<AnomaliesPage>('/anomalies', { params: { status: 'active', order: 'new', limit: 5 } })
      .then(({ data }) => setItems(data.items))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    if (open) load()
  }, [open, load, counts?.active])

  const unread = counts?.unread ?? 0
  const tone = counts && counts.red > 0 ? 'bg-bad' : 'bg-warn'
  const replace = (a: Anomaly) => setItems((list) => list?.map((x) => (x.id === a.id ? a : x)) ?? null)
  return (
    <div ref={box} className="relative">
      <button
        ref={button}
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={unread ? `Уведомления: непрочитанных ${unread}` : 'Уведомления'}
        onClick={() => setOpen((o) => !o)}
        className={`relative rounded-full border p-1.5 ${open ? 'border-accent text-accent' : 'border-line text-muted hover:border-muted hover:text-fg'}`}
        data-testid="alert-bell"
      >
        <Bell className="h-5 w-5" aria-hidden />
        {unread > 0 && (
          <span
            className={`absolute -right-1.5 -top-1.5 min-w-[1.25rem] rounded-full px-1 text-center text-[11px] font-semibold leading-5 text-white ${tone}`}
            data-testid="alert-count"
          >
            {unread > 99 ? '99+' : unread}
          </span>
        )}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Уведомления"
          className="fixed inset-x-2 top-14 z-30 rounded-xl border border-line bg-card shadow-xl shadow-black/20 sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96"
          data-testid="alert-dropdown"
        >
          <div className="flex items-baseline gap-2 border-b border-line px-4 py-3">
            <span className="font-semibold">Уведомления</span>
            {counts && (
              <span className="text-xs text-muted">
                {counts.unread ? `непрочитанных ${counts.unread}` : counts.active ? 'все прочитаны' : 'всё спокойно'}
              </span>
            )}
          </div>
          <FormError message={error} />
          {items === null ? (
            <p className="px-4 py-3 text-sm text-muted">Загружаем…</p>
          ) : items.length === 0 ? (
            <p className="px-4 py-3 text-sm text-muted" data-testid="alert-dropdown-empty">
              Активных аномалий нет — продажи, цены, остатки и выплаты в норме.
            </p>
          ) : (
            <ul className="max-h-[60vh] divide-y divide-line overflow-y-auto">
              {items.map((a) => (
                <li key={a.id} className="flex items-start gap-2 px-4 py-2.5" data-testid="alert-dropdown-item">
                  <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${SEVERITY[a.severity].dot}`} aria-label={SEVERITY[a.severity].label} />
                  <Link href={a.link ?? ALERTS} onClick={close} className={`min-w-0 flex-1 ${a.is_acknowledged ? 'text-muted' : ''}`}>
                    <span className={`block text-sm ${a.is_acknowledged ? '' : 'font-medium'}`}>{a.title}</span>
                    <span className="block truncate text-xs text-faint">
                      {a.product ? `${a.product.offer_id} · ` : ''}
                      {whenText(a.detected_at)}
                    </span>
                  </Link>
                  {!a.is_acknowledged && <OkButton anomaly={a} onDone={replace} small />}
                </li>
              ))}
            </ul>
          )}
          <div className="flex flex-wrap items-center gap-3 border-t border-line px-4 py-2.5 text-sm">
            <Link href={ALERTS} onClick={close} className="text-accent hover:underline" data-testid="alert-dropdown-all">
              Все уведомления →
            </Link>
            {unread > 0 && (
              <button
                type="button"
                onClick={async () => {
                  await acknowledgeAll()
                  load()
                }}
                className="ml-auto text-muted hover:text-fg"
                data-testid="alert-dropdown-read-all"
              >
                Отметить все прочитанными
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

// ── карточка ──
export function AlertCard({ anomaly: a, onChange }: { anomaly: Anomaly; onChange: (a: Anomaly) => void }) {
  const metric = metricText(a)
  const resolved = a.resolved_at !== null
  return (
    <article
      className={`rounded-xl border border-l-4 border-line bg-card p-4 ${SEVERITY[a.severity].stripe} ${a.is_acknowledged || resolved ? 'opacity-80' : ''}`}
      data-testid="alert-card"
      data-severity={a.severity}
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-muted">
        <span className={`font-medium ${SEVERITY[a.severity].text}`}>{a.severity === 'red' ? 'Красная' : 'Жёлтая'}</span>
        <span>· {whenText(a.detected_at)}</span>
        {resolved ? (
          <span className="text-faint">
            · закрыта {whenText(a.resolved_at as string)}
            {a.resolution ? `: ${RESOLUTION[a.resolution]}` : ''}
          </span>
        ) : (
          a.last_seen_at !== a.detected_at && <span className="text-faint">· ещё есть на {whenText(a.last_seen_at)}</span>
        )}
      </div>
      <h3 className={`mt-1 ${a.is_acknowledged ? 'font-medium' : 'font-semibold'}`}>{a.title}</h3>
      {a.product && (
        <Link href={`/dashboard/products?product=${a.product.id}`} className="mt-0.5 block truncate text-sm text-accent hover:underline" data-testid="alert-product">
          {a.product.offer_id}
          {a.product.name ? ` · ${a.product.name}` : ''}
        </Link>
      )}
      <p className="mt-1 text-sm text-muted">{a.message}</p>
      {metric && <p className="mt-1 text-xs text-faint">{metric}</p>}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {a.link && (
          <Link href={a.link} className="text-sm text-accent hover:underline" data-testid="alert-link">
            Подробнее →
          </Link>
        )}
        {!resolved && !a.is_acknowledged && (
          <span className="ml-auto">
            <OkButton anomaly={a} onDone={onChange} />
          </span>
        )}
        {a.is_acknowledged && (
          <span className="ml-auto flex items-center gap-1 text-xs text-faint" data-testid="alert-read">
            <Check className="h-3.5 w-3.5" aria-hidden /> прочитано
          </span>
        )}
      </div>
    </article>
  )
}

// ── главная ──
/** «Требуют внимания» (главная): последние красные аномалии без «Ок». */
export function AlertsWidget() {
  const counts = useAlertCounts()
  const [data, setData] = useState<AnomaliesPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api
      .get<AnomaliesPage>('/anomalies', { params: { status: 'active', severity: 'red', acknowledged: false, order: 'new', limit: 5 } })
      .then(({ data }) => setData(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    load()
  }, [load, counts?.red, counts?.active])
  if (error) return <FormError message={error} />
  if (!data) return null
  const replace = (a: Anomaly) => setData((d) => d && { ...d, items: d.items.filter((x) => x.id !== a.id), total: d.total - 1 })
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="alerts-widget">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Требуют внимания</h2>
        {data.total > 0 && <span className="text-sm text-bad">красных: {data.total}</span>}
        <Link href={ALERTS} className="ml-auto text-sm text-accent hover:underline">
          Все уведомления →
        </Link>
      </div>
      {data.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="alerts-widget-none">
          Срочного нет — красных аномалий без «Ок» нет.
          {data.counts.unread > 0 && ` Жёлтых непрочитанных: ${data.counts.unread}.`}
          {data.checked_at ? ` Проверено ${whenText(data.checked_at)}.` : ' Сервис проверяет данные раз в час.'}
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {data.items.map((a) => (
            <li key={a.id} className="flex items-start gap-3 px-4 py-2.5" data-testid="alerts-widget-item">
              <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-bad" aria-hidden />
              <Link href={a.link ?? ALERTS} className="min-w-0 flex-1">
                <span className="block text-sm font-medium">{a.title}</span>
                <span className="block truncate text-xs text-faint">
                  {a.product ? `${a.product.offer_id} · ` : `${a.type_title} · `}
                  {whenText(a.detected_at)}
                </span>
              </Link>
              <OkButton anomaly={a} onDone={replace} small />
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
