'use client'
// Уведомления об аномалиях (шаг 7.2): колокольчик в шапке, карточка аномалии; «Требует внимания» на главной (шаг 10.5).
import { Bell, Check } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { isSyncLink, openSyncPanel } from '@/components/sync'
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

const rubles = (v: string) => `${Math.round(Number(v)).toLocaleString('ru-RU')} ₽`

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

/** Ссылка аномалии: «/dashboard?sync=1» открывает окно «Синхронизация» на месте (шаг 10.5), остальное — переход. */
export function AlertLink({ href, className, testId, onClick, children }: {
  href: string
  className?: string
  testId?: string
  onClick?: () => void
  children: React.ReactNode
}) {
  const sync = isSyncLink(href)
  return (
    <Link
      href={href}
      className={className}
      data-testid={testId}
      onClick={(e) => {
        onClick?.()
        if (!sync) return
        e.preventDefault()
        openSyncPanel()
      }}
    >
      {children}
    </Link>
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
                  <AlertLink href={a.link ?? ALERTS} onClick={close} className={`min-w-0 flex-1 ${a.is_acknowledged ? 'text-muted' : ''}`}>
                    <span className={`block text-sm ${a.is_acknowledged ? '' : 'font-medium'}`}>{a.title}</span>
                    <span className="block truncate text-xs text-faint">
                      {a.product ? `${a.product.offer_id} · ` : ''}
                      {whenText(a.detected_at)}
                    </span>
                  </AlertLink>
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
      {a.money_at_risk !== null && !resolved && (
        <p className={`mt-1 text-sm ${SEVERITY[a.severity].text}`} data-testid="alert-money">
          Под риском ≈ {rubles(a.money_at_risk)}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        {a.link && (
          <AlertLink href={a.link} className="text-sm text-accent hover:underline" testId="alert-link">
            Подробнее →
          </AlertLink>
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
const ATTENTION_LIMIT = 7

/** «Требует внимания» (главная, шаг 10.5): единый список проблем магазина — аномалии продаж, цен, маржи, остатков, акций,
 * денег и данных. Активные без «Ок»: красные первыми, внутри — больше денег под риском, затем новые; первые 7 и «Все». */
export function AttentionWidget() {
  const counts = useAlertCounts()
  const [data, setData] = useState<AnomaliesPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(() => {
    api
      .get<AnomaliesPage>('/anomalies', { params: { status: 'active', acknowledged: false, order: 'severity', limit: ATTENTION_LIMIT } })
      .then(({ data }) => setData(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    load()
  }, [load, counts?.unread, counts?.active])
  if (error) return <FormError message={error} />
  if (!data) return null
  const replace = (a: Anomaly) => setData((d) => d && { ...d, items: d.items.filter((x) => x.id !== a.id), total: d.total - 1 })
  const more = data.total - data.items.length
  const c = data.counts
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="attention">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Требует внимания</h2>
        {c.unread > 0 && (
          <span className="text-sm" data-testid="attention-counts">
            {c.red > 0 && <span className="text-bad">красных {c.red}</span>}
            {c.red > 0 && c.yellow > 0 && <span className="text-faint"> · </span>}
            {c.yellow > 0 && <span className="text-warn">жёлтых {c.yellow}</span>}
          </span>
        )}
        <Link href={ALERTS} className="ml-auto text-sm text-accent hover:underline" data-testid="attention-all">
          Все → Уведомления
        </Link>
      </div>
      {data.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="attention-empty">
          Всё в порядке: продажи, цены, остатки, деньги и данные — без проблем, которых вы ещё не видели.
          {c.active > 0 && ` Отмеченных «Ок», но ещё не прошедших: ${c.active}.`}
          {data.checked_at ? ` Проверено ${whenText(data.checked_at)}.` : ' Сервис проверяет данные раз в час.'}
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {data.items.map((a) => (
            <AttentionItem key={a.id} anomaly={a} onDone={replace} />
          ))}
        </ul>
      )}
      {more > 0 && (
        <Link href={ALERTS} className="block border-t border-line px-4 py-2.5 text-sm text-accent hover:underline" data-testid="attention-more">
          И ещё {more} — все в «Уведомлениях» →
        </Link>
      )}
    </section>
  )
}

function AttentionItem({ anomaly: a, onDone }: { anomaly: Anomaly; onDone: (a: Anomaly) => void }) {
  const sev = SEVERITY[a.severity]
  return (
    <li className="flex gap-3 px-4 py-3" data-testid="attention-item" data-severity={a.severity} data-type={a.type}>
      {/* полоска важности — отдельным элементом: рамку слева перекрасил бы разделитель списка (divide-line) */}
      <span className={`w-1 shrink-0 rounded-full ${sev.dot}`} aria-hidden />
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
            <span className={`font-medium ${sev.text}`}>{a.severity === 'red' ? 'Красная' : 'Жёлтая'}</span>
            <span>· {a.type_title}</span>
            {a.product && <span className="truncate">· {a.product.offer_id}</span>}
          </div>
          <p className="mt-0.5 font-medium">{a.title}</p>
          <p className="mt-0.5 line-clamp-2 text-sm text-muted">{a.message}</p>
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {a.money_at_risk !== null && (
              <span className={sev.text} data-testid="attention-money" title="Сколько денег под риском — оценка по последней проверке">
                Под риском ≈ {rubles(a.money_at_risk)}
              </span>
            )}
            <AlertLink href={a.link ?? ALERTS} className="text-accent hover:underline" testId="attention-link">
              Решить →
            </AlertLink>
          </div>
        </div>
        <OkButton anomaly={a} onDone={onDone} small />
      </div>
    </li>
  )
}
