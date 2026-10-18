'use client'
// Меню аккаунта → «Уведомления» (шаг 7.3): Telegram — подключение по коду, что присылать, тихие часы, тестовое сообщение.
// Токен бота — только на сервере (.env), сюда не приходит.
import { Send } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { api, errorMessage } from '@/lib/api'
import { whenText } from '@/lib/alerts'

type Settings = {
  bot_configured: boolean
  bot_username: string | null
  linked: boolean
  telegram_username: string | null
  telegram_name: string | null
  linked_at: string | null
  blocked: boolean
  notify_red: boolean
  notify_yellow: boolean
  notify_auto_promo: boolean
  notify_money: boolean
  quiet_enabled: boolean
  quiet_from: string
  quiet_to: string
  timezone: string
  timezones: { value: string; label: string }[]
  pending: number
  last_sent_at: string | null
}
type LinkCode = { code: string; expires_at: string; bot_username: string | null; deep_link: string | null }
type Prefs = Pick<Settings, 'notify_red' | 'notify_yellow' | 'notify_auto_promo' | 'notify_money' | 'quiet_enabled' | 'quiet_from' | 'quiet_to' | 'timezone'>

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
  const [data, setData] = useState<Settings | null>(null)
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [code, setCode] = useState<LinkCode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  const apply = useCallback((s: Settings) => {
    setData(s)
    setPrefs(Object.fromEntries(PREF_KEYS.map((k) => [k, s[k]])) as Prefs)
  }, [])

  useEffect(() => {
    api
      .get<Settings>('/notifications/settings')
      .then(({ data }) => apply(data))
      .catch((err) => setError(errorMessage(err)))
  }, [apply])

  // код показан — ждём, пока пользователь отправит его боту: раз в 3 с проверяем, подключился ли чат
  useEffect(() => {
    if (!code) return
    const timer = window.setInterval(async () => {
      if (new Date(code.expires_at) < new Date()) {
        window.clearInterval(timer)
        return
      }
      try {
        const { data: s } = await api.get<Settings>('/notifications/settings')
        if (s.linked) {
          window.clearInterval(timer)
          setCode(null)
          setData(s)
          setNotice('Telegram подключён — уведомления будут приходить туда. Проверьте тестовым сообщением.')
        }
      } catch {
        // сеть мигнула — проверим в следующий раз
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [code])

  async function run(action: () => Promise<void>) {
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      await action()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const getCode = () => run(async () => setCode((await api.post<LinkCode>('/notifications/link-code')).data))
  const sendTest = () => run(async () => {
    const { data: r } = await api.post<{ message: string }>('/notifications/test')
    setNotice(r.message)
    apply((await api.get<Settings>('/notifications/settings')).data)
  })
  const unlink = () => run(async () => {
    await api.post('/notifications/unlink')
    apply((await api.get<Settings>('/notifications/settings')).data)
    setNotice('Telegram отключён — уведомления остаются на сайте, в колокольчике.')
  })
  const save = (e: React.FormEvent) => {
    e.preventDefault()
    setSaved(false)
    run(async () => {
      apply((await api.patch<Settings>('/notifications/settings', prefs)).data)
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
      {notice && (
        <p className="rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm text-ok" data-testid="notify-notice">
          {notice}
        </p>
      )}
      {!data && !error && <p className="text-sm text-muted">Загружаем…</p>}

      {data && (
        <Section title="Telegram">
          {!data.bot_configured ? (
            <p className="text-sm text-muted" data-testid="bot-not-configured">
              Telegram-бот на сервере ещё не подключён — уведомления видны только на сайте, в колокольчике. Администратору: задать
              TELEGRAM_BOT_TOKEN и TELEGRAM_BOT_USERNAME в .env и запустить сервис bot.
            </p>
          ) : data.linked ? (
            <div className="space-y-3 text-sm" data-testid="tg-linked">
              <p>
                <span className="font-medium text-ok">Подключено</span>
                {data.telegram_username ? ` — @${data.telegram_username}` : ''}
                {data.telegram_name ? ` (${data.telegram_name})` : ''}
                {data.linked_at && <span className="text-muted"> · {whenText(data.linked_at)}</span>}
              </p>
              {data.blocked && (
                <p className="rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-warn" data-testid="tg-blocked">
                  Бот не может вам написать: его остановили или заблокировали в Telegram. Откройте бота и нажмите «Запустить» — или
                  отключите и подключите заново.
                </p>
              )}
              <p className="text-muted">
                {data.last_sent_at ? `Последнее сообщение — ${whenText(data.last_sent_at)}.` : 'Сообщений ещё не было.'}
                {data.pending > 0 && ` Ждут отправки: ${data.pending} (тихие часы или повтор).`}
              </p>
              <div className="flex flex-wrap gap-3">
                <button
                  type="button"
                  disabled={busy}
                  onClick={sendTest}
                  className="flex items-center gap-2 rounded-lg bg-accent px-4 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-60"
                  data-testid="tg-test"
                >
                  <Send className="h-4 w-4" aria-hidden /> Отправить тестовое сообщение
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={unlink}
                  className="rounded-lg border border-line px-3 py-1.5 hover:border-bad hover:text-bad disabled:opacity-60"
                  data-testid="tg-unlink"
                >
                  Отключить
                </button>
              </div>
            </div>
          ) : code ? (
            <div className="space-y-3 text-sm" data-testid="tg-code">
              <p className="text-muted">Отправьте этот код боту — он привяжет ваш чат к аккаунту. Код действует 10 минут, один раз.</p>
              <p className="font-mono text-3xl font-semibold tracking-[0.3em]" data-testid="tg-code-value">
                {code.code}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                {code.deep_link && (
                  <a
                    href={code.deep_link}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-lg bg-accent px-4 py-1.5 font-medium text-white hover:opacity-90"
                    data-testid="tg-open-bot"
                  >
                    Открыть бота @{code.bot_username} →
                  </a>
                )}
                <button type="button" disabled={busy} onClick={getCode} className="text-accent hover:underline disabled:opacity-60" data-testid="tg-new-code">
                  Новый код
                </button>
              </div>
              <p className="text-xs text-faint">
                {code.deep_link ? 'Кнопка откроет бота и отправит код сама. Или найдите бота в Telegram и отправьте: ' : 'Найдите бота в Telegram и отправьте: '}
                <span className="font-mono">/start {code.code}</span>. Страница сама увидит, что Telegram подключён.
              </p>
            </div>
          ) : (
            <div className="space-y-3 text-sm" data-testid="tg-not-linked">
              <p className="text-muted">
                Зачем: красные аномалии и проблемы с деньгами придут сразу, где бы вы ни были. Что присылать и тихие часы — ниже.
              </p>
              <button
                type="button"
                disabled={busy}
                onClick={getCode}
                className="rounded-lg bg-accent px-4 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-60"
                data-testid="tg-connect"
              >
                Подключить Telegram
              </button>
            </div>
          )}
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
