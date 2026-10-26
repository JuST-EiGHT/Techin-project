'use client'
// Сводка «Интеграций» (шаг 10.26): у каждого источника — состояние, время последнего успешного обмена и ошибка простыми
// словами: Ozon — по видам данных окна «Синхронизация» (10.2), банки — по подключениям, Telegram — привязка и последнее
// отправленное. Клик — вкладка с подключением, проверкой, обновлением доступа и отключением.
import { useEffect, useState } from 'react'

import type { NotifySettings } from '@/components/settings/Telegram'
import { api } from '@/lib/api'
import { useSyncStatus } from '@/lib/sync'

type Tab = 'ozon' | 'banks' | 'telegram'
type Conn = { status: 'active' | 'error' | 'auth'; status_message: string | null; last_sync_at: string | null; title: string }
type Card = { tab: Tab; title: string; state: 'ok' | 'warn' | 'bad' | 'off'; label: string; last: string | null; note: string | null }

const fmt = (iso: string) => new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'short' })
const latest = (dates: (string | null)[]) => dates.filter((d): d is string => Boolean(d)).sort().at(-1) ?? null
const TONE = { ok: 'border-ok/40 text-ok', warn: 'border-warn/40 text-warn', bad: 'border-bad/40 text-bad', off: 'border-line text-muted' }

export function IntegrationsSummary({ onOpen }: { onOpen: (tab: Tab) => void }) {
  const sync = useSyncStatus()
  const [banks, setBanks] = useState<Conn[] | null>(null)
  const [tg, setTg] = useState<NotifySettings | null>(null)
  useEffect(() => {
    api.get<Conn[]>('/bank/connections').then(({ data }) => setBanks(data)).catch(() => setBanks([]))
    api.get<NotifySettings>('/notifications/settings').then(({ data }) => setTg(data)).catch(() => setTg(null))
  }, [])

  const cards: Card[] = []
  if (sync) {
    const ozon = sync.kinds.filter((k) => k.kind !== 'bank')
    const problem = ozon.find((k) => k.problem && (k.state === 'error' || k.state === 'stale'))
    cards.push(
      !sync.has_active_key
        ? { tab: 'ozon', title: 'Ozon', state: 'off', label: 'Не подключён', last: null, note: 'Добавьте ключ Seller API — товары, остатки, начисления и выплаты загрузятся сами.' }
        : problem
          ? { tab: 'ozon', title: 'Ozon', state: problem.state === 'error' ? 'bad' : 'warn', label: problem.state === 'error' ? 'Ошибка' : 'Давно не обновлялось', last: latest(ozon.map((k) => k.last_success_at)), note: `${problem.title}: ${problem.problem}${problem.hint ? `. ${problem.hint}` : ''}` }
          : { tab: 'ozon', title: 'Ozon', state: 'ok', label: 'Работает', last: latest(ozon.map((k) => k.last_success_at)), note: null },
    )
  }
  if (banks) {
    const bad = banks.find((b) => b.status === 'auth') ?? banks.find((b) => b.status === 'error')
    cards.push(
      banks.length === 0
        ? { tab: 'banks', title: 'Банки', state: 'off', label: 'Не подключены', last: null, note: 'Т-Банк или Точка по API — операции и остатки раз в час. Можно и без банка: выписки — файлом в «Счетах».' }
        : bad
          ? { tab: 'banks', title: 'Банки', state: bad.status === 'auth' ? 'bad' : 'warn', label: bad.status === 'auth' ? 'Нужен новый токен' : 'Не обновилось', last: latest(banks.map((b) => b.last_sync_at)), note: `${bad.title}: ${bad.status_message ?? 'ошибка обмена'}` }
          : { tab: 'banks', title: 'Банки', state: 'ok', label: `Работает (${banks.length})`, last: latest(banks.map((b) => b.last_sync_at)), note: null },
    )
  }
  if (tg) {
    cards.push(
      !tg.bot_configured
        ? { tab: 'telegram', title: 'Telegram', state: 'off', label: 'Нет на сервере', last: null, note: 'Бот на сервере не настроен — уведомления видны на сайте.' }
        : !tg.linked
          ? { tab: 'telegram', title: 'Telegram', state: 'off', label: 'Не подключён', last: null, note: 'Подключите — уведомления будут приходить в чат.' }
          : tg.blocked
            ? { tab: 'telegram', title: 'Telegram', state: 'bad', label: 'Бот не может написать', last: tg.last_sent_at, note: 'Запустите бота в Telegram снова (/start) или подключите заново.' }
            : { tab: 'telegram', title: 'Telegram', state: 'ok', label: 'Подключён', last: tg.last_sent_at, note: tg.telegram_username ? `@${tg.telegram_username}` : null },
    )
  }
  if (cards.length === 0) return null
  return (
    <div className="grid gap-3 sm:grid-cols-3" data-testid="integrations-summary">
      {cards.map((c) => (
        <button key={c.tab} type="button" onClick={() => onOpen(c.tab)} className={`flex flex-col rounded-xl border bg-card p-4 text-left hover:border-accent ${TONE[c.state].split(' ')[0]}`} data-testid={`summary-${c.tab}`} data-state={c.state}>
          <div className="flex items-baseline gap-2">
            <span className="font-medium text-fg">{c.title}</span>
            <span className={`ml-auto text-xs ${TONE[c.state].split(' ')[1]}`}>{c.label}</span>
          </div>
          <div className="mt-1 text-xs text-muted">{c.last ? `Последний успешный обмен — ${fmt(c.last)}` : 'Обмена ещё не было'}</div>
          {c.note && <div className="mt-1 line-clamp-3 text-xs text-faint">{c.note}</div>}
        </button>
      ))}
    </div>
  )
}
