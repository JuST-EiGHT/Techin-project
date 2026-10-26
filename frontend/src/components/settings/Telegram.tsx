'use client'
// Подключение Telegram-бота (шаг 7.3): код с сайта, тестовое сообщение, отключение. С шага 10.1 — вкладка «Telegram»
// страницы «Интеграции»; что присылать и тихие часы — в «Уведомлениях». Токен бота — только на сервере (.env).
import { Send } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { api, errorMessage } from '@/lib/api'
import { whenText } from '@/lib/alerts'

export type NotifySettings = {
  bot_configured: boolean
  bot_username: string | null
  linked: boolean
  telegram_username: string | null
  telegram_name: string | null
  linked_at: string | null
  blocked: boolean
  sections: NotifySection[] // по разделам карты (10.25)
  channels: NotifyChannel[] // в сервисе, Telegram, e-mail (после почты)
  digest_at: string // «09:00» — сводка раз в день
  quiet_enabled: boolean
  quiet_from: string
  quiet_to: string
  timezone: string
  timezones: { value: string; label: string }[]
  pending: number
  last_sent_at: string | null
}
export type SectionKey = 'sales' | 'margin' | 'stock' | 'cash' | 'payouts' | 'auto_promo' | 'prices' | 'data' | 'automation'
export type NotifySection = {
  key: SectionKey
  title: string
  hint: string
  severities: ('red' | 'yellow')[] // какие бывают; одна — выбор важности не нужен
  soon: string | null // раздела ещё нет — с какого шага
  on: boolean // уведомлять: колокольчик, «Требует внимания», Telegram
  telegram: boolean
  severity: 'all' | 'red' // в Telegram: все или только красные
  daily: boolean // в Telegram сводкой раз в день
}
export type NotifyChannel = { key: 'service' | 'telegram' | 'email'; title: string; available: boolean; connected: boolean; note: string }
type LinkCode = { code: string; expires_at: string; bot_username: string | null; deep_link: string | null }

export const TELEGRAM_HREF = '/dashboard/settings/integrations?tab=telegram'

export function TelegramPanel() {
  const [data, setData] = useState<NotifySettings | null>(null)
  const [code, setCode] = useState<LinkCode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => setData((await api.get<NotifySettings>('/notifications/settings')).data), [])

  useEffect(() => {
    load().catch((err) => setError(errorMessage(err)))
  }, [load])

  // код показан — ждём, пока пользователь отправит его боту: раз в 3 с проверяем, подключился ли чат
  useEffect(() => {
    if (!code) return
    const timer = window.setInterval(async () => {
      if (new Date(code.expires_at) < new Date()) {
        window.clearInterval(timer)
        return
      }
      try {
        const { data: s } = await api.get<NotifySettings>('/notifications/settings')
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
    await load()
  })
  const unlink = () => run(async () => {
    await api.post('/notifications/unlink')
    await load()
    setNotice('Telegram отключён — уведомления остаются на сайте, в колокольчике.')
  })

  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-5" data-testid="telegram">
      <div>
        <h2 className="text-lg font-semibold">Telegram</h2>
        <p className="mt-1 text-sm text-muted">
          Уведомления сервиса приходят в Telegram — не нужно держать сайт открытым. Что присылать и тихие часы —{' '}
          <Link href="/dashboard/settings/notifications" className="text-accent hover:underline">
            в «Уведомлениях»
          </Link>
          .
        </p>
      </div>
      <FormError message={error} />
      {notice && (
        <p className="rounded-lg border border-ok/40 bg-ok/10 px-4 py-2 text-sm text-ok" data-testid="notify-notice">
          {notice}
        </p>
      )}
      {!data && !error && <p className="text-sm text-muted">Загружаем…</p>}
      {data &&
        (!data.bot_configured ? (
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
              Зачем: красные аномалии и проблемы с деньгами придут сразу, где бы вы ни были. Что присылать и тихие часы — в
              «Уведомлениях».
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
        ))}
    </section>
  )
}
