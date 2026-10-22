'use client'
// История изменений цен и откат (шаг 6.4): окно подтверждения отката и виджет «Последние изменения цен» на главной.
// Откат — тот же путь, что изменение одного товара (6.2): предпросмотр (в Ozon ничего не меняется) → «Откатить» по
// preview_id. Без часовой паузы и лимита 20 %, только к прежней цене и не старше 7 дней (BUSINESS_LOGIC §8).
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Modal } from '@/components/planning/Operations'
import { Badge, fmtDateTime, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { CHANGE_STATUS_TONE, type PriceApplyResult, type PriceHistoryItem, type PriceHistoryPage, type PricePreview } from '@/lib/pricing'

const time = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
export const signedPct = (v: string | null) => (v === null ? '' : `${Number(v) > 0 ? '+' : ''}${pctText(v)}`)

/** Подтверждение отката: что вернётся, проверки, «Откатить». */
export function RollbackModal({ change, onClose, onDone }: { change: PriceHistoryItem; onClose: () => void; onDone: (message: string) => void }) {
  const [p, setP] = useState<PricePreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    api
      .post<PricePreview>(`/prices/rollback/${change.id}`)
      .then((r) => setP(r.data))
      .catch((err) => setError(errorMessage(err)))
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [change.id])
  const expired = !!p && now > Date.parse(p.expires_at)

  async function confirm() {
    if (!p) return
    setSending(true)
    setError(null)
    try {
      const { data } = await api.post<PriceApplyResult>('/prices/apply', { preview_id: p.preview_id })
      onDone(data.message)
    } catch (err) {
      setError(errorMessage(err))
      setP(null)
      setSending(false)
    }
  }

  return (
    <Modal title="Откатить изменение цены" onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="rollback">
        <p>
          <span className="font-medium">{change.product.offer_id}</span>
          {change.product.name && <span className="text-muted"> · {change.product.name}</span>}
          <span className="block text-muted">
            Изменение от {fmtDateTime(change.created_at)}: {rub(change.price_before)} → {rub(change.price_after)}
          </span>
        </p>
        {!p && !error && <p className="text-muted">Проверяем текущую цену в Ozon…</p>}
        <FormError message={error} />
        {p && (
          <>
            <dl className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-4 gap-y-1.5" data-testid="rollback-figures">
              <span />
              <dt className="text-right text-xs text-faint">сейчас</dt>
              <dt className="text-right text-xs text-faint">вернём</dt>
              <dt className="text-muted">Цена</dt>
              <dd className="text-right tabular-nums">{rub(p.price_before)}</dd>
              <dd className="text-right font-medium tabular-nums">
                {rub(p.price_after)}
                {p.change_pct !== null && <span className="block text-xs font-normal text-muted">{signedPct(p.change_pct)}</span>}
              </dd>
              <dt className="text-muted">Маржа</dt>
              <dd className="text-right tabular-nums">{pctText(p.margin_before)}</dd>
              <dd className="text-right font-medium tabular-nums">{pctText(p.margin_after)}</dd>
            </dl>
            <p className="text-xs text-muted">
              Откат возвращает ровно прежнюю цену — без часовой паузы и лимита 20 %. Остальные проверки те же: не ниже себестоимости с
              НДС + 5 %, ниже зачёркнутой.
            </p>
            {p.errors.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-bad/10 px-3 py-2 text-bad" data-testid="rollback-errors">
                {p.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            )}
            {p.warnings.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-warn/10 px-3 py-2">
                {p.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {p.can_apply && (
              <p className={`text-xs ${expired ? 'text-warn' : 'text-faint'}`}>
                {expired ? 'Предпросмотр устарел — закройте окно и откройте снова.' : `Предпросмотр действует до ${time(p.expires_at)}.`}
              </p>
            )}
          </>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
            Отмена
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!p || !p.can_apply || expired || sending}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
            data-testid="rollback-confirm"
          >
            {sending ? 'Отправляем в Ozon…' : p?.can_apply ? `Вернуть ${rub(p.price_after)}` : 'Откатить'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

/** Главная: последние изменения цен через сервис (шаг 6.4). */
export function RecentPriceChanges() {
  const [data, setData] = useState<PriceHistoryPage | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<PriceHistoryPage>('/prices/history', { params: { limit: 5 } })
      .then((r) => setData(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  if (error) return <FormError message={error} />
  if (!data) return null
  return (
    <section className="rounded-xl border border-line bg-card" data-testid="recent-prices">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-line px-4 py-3">
        <h2 className="font-semibold">Последние изменения цен</h2>
        <span className="text-sm text-muted">за 30 дней: {data.counts.applied} применено</span>
        <Link href="/dashboard/products/prices/history" className="ml-auto text-sm text-accent hover:underline">
          Вся история →
        </Link>
      </div>
      {data.items.length === 0 ? (
        <p className="px-4 py-3 text-sm text-muted" data-testid="recent-prices-none">
          За 30 дней цены через сервис не меняли. Изменить цену — в карточке товара или на странице{' '}
          <Link href="/dashboard/products/prices/bulk" className="text-accent hover:underline">
            «Цены списком»
          </Link>
          .
        </p>
      ) : (
        <ul className="divide-y divide-line">
          {data.items.map((i) => (
            <li key={i.id} className={`flex items-start gap-3 px-4 py-2 text-sm ${i.status === 'applied' || i.status === 'unknown' ? '' : 'text-muted'}`} data-testid="recent-price">
              <span className="min-w-0 flex-1">
                <Link href={`/dashboard/products?product=${i.product.id}`} className="block truncate font-medium hover:underline">
                  {i.product.offer_id}
                </Link>
                <span className="block text-xs text-faint">
                  {fmtDateTime(i.created_at)} · {i.kind_title.toLowerCase()}
                </span>
              </span>
              <span className="shrink-0 text-right">
                <span className="block tabular-nums">
                  {rub(i.price_before)} → <b className={i.kind === 'rollback' ? 'text-accent' : ''}>{rub(i.price_after)}</b>
                </span>
                <Badge tone={CHANGE_STATUS_TONE[i.status]}>{i.status_title}</Badge>
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
