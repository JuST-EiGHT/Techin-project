'use client'
// Меню аккаунта → «Уведомления» (шаг 7.3): что присылать в Telegram, тихие часы. Подключение бота, тестовое сообщение и
// отключение — с шага 10.1 в «Интеграции» → «Telegram» (components/settings/Telegram.tsx), здесь — статус и ссылка туда.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type NotifySettings, TELEGRAM_HREF } from '@/components/settings/Telegram'
import { api, errorMessage } from '@/lib/api'

type Prefs = Pick<NotifySettings, 'notify_red' | 'notify_yellow' | 'notify_auto_promo' | 'notify_money' | 'quiet_enabled' | 'quiet_from' | 'quiet_to' | 'timezone'>

// Что присылать: категории не пересекаются — каждая аномалия ровно в одной (правило 15: у каждой — что изменится)
const CATEGORIES: { key: keyof Prefs; label: string; why: string }[] = [
  {
    key: 'notify_red',
    label: 'Красные',
    why: 'Срочное по товарам: продажи почти встали, цена изменилась больше чем на 30 %, остаток на исходе, маржа ниже порога, убыток в акции.',
  },
  {
    key: 'notify_yellow',
    label: 'Жёлтые',
    why: 'Стоит присмотреться: падение продаж, изменение цены или комиссии, остатка мало. Их больше, поэтому по умолчанию выключены — на сайте они видны всегда.',
  },
  {
    key: 'notify_auto_promo',
    label: 'Автоакции',
    why: 'Ozon сам добавил товар в акцию — успейте убрать его, если акция невыгодна.',
  },
  {
    key: 'notify_money',
    label: 'Деньги',
    why: 'Выплата Ozon не пришла или разошлась с прогнозом; с «Планированием» — не дошла до счёта, кассовый разрыв, операции без статьи.',
  },
]
const TIMES = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`)
const PREF_KEYS: (keyof Prefs)[] = ['notify_red', 'notify_yellow', 'notify_auto_promo', 'notify_money', 'quiet_enabled', 'quiet_from', 'quiet_to', 'timezone']

function Section({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
      <h2 className="text-lg font-semibold">{title}</h2>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

export default function NotificationsSettingsPage() {
  const [data, setData] = useState<NotifySettings | null>(null)
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  const apply = useCallback((s: NotifySettings) => {
    setData(s)
    setPrefs(Object.fromEntries(PREF_KEYS.map((k) => [k, s[k]])) as Prefs)
  }, [])

  useEffect(() => {
    api
      .get<NotifySettings>('/notifications/settings')
      .then(({ data }) => apply(data))
      .catch((err) => setError(errorMessage(err)))
  }, [apply])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await action()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const save = (e: React.FormEvent) => {
    e.preventDefault()
    setSaved(false)
    run(async () => {
      apply((await api.patch<NotifySettings>('/notifications/settings', prefs)).data)
      setSaved(true)
    })
  }
  const set = <K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    setSaved(false)
    setPrefs((p) => p && { ...p, [key]: value })
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Уведомления</h1>
        <p className="mt-1 text-sm text-muted">
          Раз в час сервис проверяет продажи, цены, остатки, акции и выплаты. Найденное видно в колокольчике на сайте, а если подключить
          Telegram — приходит и туда, не нужно держать сайт открытым.{' '}
          <Link href="/dashboard/alerts" className="text-accent hover:underline">
            Все уведомления →
          </Link>
        </p>
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загружаем…</p>}

      {data && (
        <Section title="Telegram">
          <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="tg-status">
            {!data.bot_configured ? (
              <span className="text-muted">Бот на сервере ещё не подключён — уведомления видны только на сайте.</span>
            ) : data.linked ? (
              <span>
                <span className="font-medium text-ok">Подключено</span>
                {data.telegram_username ? ` — @${data.telegram_username}` : ''}
                {data.blocked && <span className="text-warn"> · бот не может вам написать</span>}
              </span>
            ) : (
              <span className="text-muted">Не подключён — уведомления видны только на сайте, в колокольчике.</span>
            )}
            <Link href={TELEGRAM_HREF} className="ml-auto text-accent hover:underline" data-testid="tg-integrations">
              {data.linked ? 'Тестовое сообщение, отключить →' : 'Подключить в «Интеграциях» →'}
            </Link>
          </div>
        </Section>
      )}

      {prefs && data && (
        <form onSubmit={save} className="space-y-6" data-testid="notify-prefs">
          <Section
            title="Что присылать в Telegram"
            hint={
              <>
                Каждое уведомление — ровно в одной группе. На сайте видны все.{' '}
                <Link href="/dashboard/settings#anomalies" className="text-accent hover:underline">
                  Пороги — когда считать проблемой →
                </Link>
              </>
            }
          >
            <div className="space-y-3">
              {CATEGORIES.map((c) => (
                <label key={c.key} className="flex cursor-pointer items-start gap-3">
                  <input
                    type="checkbox"
                    checked={Boolean(prefs[c.key])}
                    onChange={(e) => set(c.key, e.target.checked as never)}
                    className="mt-1 accent-accent"
                    data-testid={`pref-${c.key}`}
                  />
                  <span>
                    <span className="block text-sm font-medium">{c.label}</span>
                    <span className="block text-xs text-faint">{c.why}</span>
                  </span>
                </label>
              ))}
            </div>
          </Section>

          <Section title="Тихие часы" hint="В это время сообщения не приходят — соберём их и пришлём одним сообщением, когда тихие часы кончатся. Тестовое сообщение приходит всегда.">
            <label className="flex cursor-pointer items-center gap-3 text-sm">
              <input
                type="checkbox"
                checked={prefs.quiet_enabled}
                onChange={(e) => set('quiet_enabled', e.target.checked)}
                className="accent-accent"
                data-testid="pref-quiet"
              />
              Не присылать в это время
            </label>
            <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
              <label className="block">
                <span className="block text-muted">с</span>
                <select
                  value={prefs.quiet_from}
                  disabled={!prefs.quiet_enabled}
                  onChange={(e) => set('quiet_from', e.target.value)}
                  className="mt-1 rounded-lg border border-line bg-bg px-3 py-1.5 disabled:opacity-60"
                  data-testid="pref-quiet-from"
                >
                  {TIMES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="block text-muted">до</span>
                <select
                  value={prefs.quiet_to}
                  disabled={!prefs.quiet_enabled}
                  onChange={(e) => set('quiet_to', e.target.value)}
                  className="mt-1 rounded-lg border border-line bg-bg px-3 py-1.5 disabled:opacity-60"
                  data-testid="pref-quiet-to"
                >
                  {TIMES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="mt-3 block text-sm">
              <span className="block text-muted">Часовой пояс — тихие часы считаем по нему</span>
              <select
                value={prefs.timezone}
                onChange={(e) => set('timezone', e.target.value)}
                className="mt-1 w-full max-w-xs rounded-lg border border-line bg-bg px-3 py-1.5"
                data-testid="pref-timezone"
              >
                {data.timezones.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          </Section>

          <div className="flex flex-wrap items-center gap-3">
            <button
              type="submit"
              disabled={busy}
              className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              data-testid="prefs-save"
            >
              Сохранить
            </button>
            {saved && <span className="text-sm text-ok">Сохранено</span>}
          </div>
        </form>
      )}
    </div>
  )
}
