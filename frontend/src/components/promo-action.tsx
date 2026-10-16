'use client'
// Вход и выход из акции (шаг 4.3): предпросмотр → подтверждение в окне → запрос в Ozon. Пока не нажата кнопка
// подтверждения, в Ozon ничего не уходит; каждое применённое действие пишется в журнал.
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Modal } from '@/components/planning/Operations'
import { pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { fieldNumber } from '@/lib/pricing'
import type { ActionPreview } from '@/lib/promotions'

type Props = {
  promotionId: string
  promotionTitle: string
  product: { id: string; offer_id: string; name: string | null }
  mode: 'join' | 'leave'
  onClose: () => void
  onDone: (message: string) => void
}

export function PromoActionModal({ promotionId, promotionTitle, product, mode, onClose, onDone }: Props) {
  const [price, setPrice] = useState('')
  const [stock, setStock] = useState('')
  const [ack, setAck] = useState(false)
  const [preview, setPreview] = useState<ActionPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const seq = useRef(0)
  const path = `/promotions/${promotionId}/products/${product.id}/${mode}`

  function body(): { body: Record<string, unknown>; bad: string | null } {
    if (mode === 'leave') return { body: {}, bad: null }
    const p = fieldNumber(price)
    const s = fieldNumber(stock)
    if (p !== null && (!Number.isFinite(p) || p <= 0)) return { body: {}, bad: 'Цена в акции — число больше 0' }
    if (s !== null && (!Number.isInteger(s) || s < 1)) return { body: {}, bad: 'Количество — целое число от 1' }
    return { body: { ...(p !== null ? { action_price: p.toFixed(2) } : {}), ...(s !== null ? { stock: s } : {}) }, bad: null }
  }
  const { body: payload, bad } = body()
  const key = JSON.stringify(payload)

  useEffect(() => {
    if (bad) return
    const id = ++seq.current
    setBusy(true)
    const t = setTimeout(() => {
      api
        .post<ActionPreview>(path, payload)
        .then((r) => {
          if (id !== seq.current) return
          setPreview(r.data)
          setError(null)
          // первый предпросмотр подставляет цену и количество по умолчанию
          if (mode === 'join' && price === '' && r.data.action_price) setPrice(Number(r.data.action_price).toString())
          if (mode === 'join' && stock === '' && r.data.stock_required && r.data.stock) setStock(String(r.data.stock))
        })
        .catch((err) => id === seq.current && setError(errorMessage(err)))
        .finally(() => id === seq.current && setBusy(false))
    }, preview ? 400 : 0)
    return () => clearTimeout(t)
    // payload сравниваем по содержимому (key)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, key, bad])

  async function confirm() {
    setSending(true)
    setError(null)
    try {
      const { data } = await api.post<ActionPreview>(path, { ...payload, confirm: true, accept_loss: ack })
      onDone(data.message ?? 'Готово')
    } catch (err) {
      setError(errorMessage(err))
      setSending(false)
    }
  }

  const p = preview
  const join = mode === 'join'
  const title = join ? (p?.action === 'update' ? 'Изменить участие в акции' : 'Добавить в акцию') : 'Убрать из акции'
  const blocked = !p || !p.can_apply || busy || sending || !!bad || (p.needs_loss_ack && !ack)
  const input = 'w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-right text-sm outline-none focus:border-accent'
  return (
    <Modal title={title} onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="promo-action">
        <p>
          <span className="font-medium">{product.offer_id}</span>
          {product.name && <span className="text-muted"> · {product.name}</span>}
          <span className="block text-muted">Акция «{promotionTitle}»</span>
        </p>

        {join && (
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs text-muted">
              Цена в акции, ₽
              <input inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} className={`${input} mt-1`} data-testid="promo-price" />
              {p?.max_action_price && <span className="mt-1 block text-faint">не выше {rub(p.max_action_price)} — так требует Ozon</span>}
            </label>
            {p?.stock_required !== false ? (
              <label className="text-xs text-muted">
                Сколько штук по акции
                <input inputMode="numeric" value={stock} onChange={(e) => setStock(e.target.value)} className={`${input} mt-1`} data-testid="promo-stock" />
                <span className="mt-1 block text-faint">
                  на складах {p?.on_hand ?? '—'} шт.{p?.min_stock ? ` · Ozon требует от ${p.min_stock}` : ''}
                </span>
              </label>
            ) : (
              <p className="self-end text-xs text-warn">Количество не указывается: в этой акции участвует весь остаток.</p>
            )}
          </div>
        )}

        <FormError message={error ?? bad} />
        {!p && !error && <p className="text-muted">Проверяем…</p>}
        {p && (
          <div className={`space-y-3 transition-opacity ${busy ? 'opacity-60' : ''}`}>
            {join ? (
              <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5" data-testid="promo-figures">
                <dt className="text-muted">Обычная цена</dt>
                <dd className="text-right tabular-nums">{rub(p.price)}</dd>
                <dt className="text-muted">Цена в акции</dt>
                <dd className="text-right font-medium tabular-nums">
                  {rub(p.action_price)}
                  {p.discount_pct && <span className="text-xs text-muted"> −{Number(p.discount_pct).toLocaleString('ru-RU')} %</span>}
                </dd>
                <dt className="text-muted">Останется с 1 шт.</dt>
                <dd className={`text-right font-medium tabular-nums ${p.unit_profit === null ? 'text-muted' : Number(p.unit_profit) < 0 ? 'text-bad' : 'text-ok'}`} data-testid="promo-profit">
                  {rub(p.unit_profit)}
                  {p.margin !== null && <span className="text-xs"> · маржа {pctText(p.margin)}</span>}
                </dd>
                {p.min_price && (
                  <>
                    <dt className="text-muted">Ваша минимальная цена</dt>
                    <dd className="text-right tabular-nums text-muted">{rub(p.min_price)}</dd>
                  </>
                )}
                {p.whole_stock_total?.profit != null && p.whole_stock_total.qty > 0 && (
                  <>
                    <dt className="text-muted">Если уйдёт весь остаток ({p.whole_stock_total.qty} шт.)</dt>
                    <dd className={`text-right tabular-nums ${Number(p.whole_stock_total.profit) < 0 ? 'text-bad' : ''}`}>{rub(p.whole_stock_total.profit)}</dd>
                  </>
                )}
              </dl>
            ) : (
              p.before.participating && (
                <p className="text-muted">
                  Сейчас в акции по {rub(p.before.action_price)}
                  {p.before.stock ? `, заявлено ${p.before.stock} шт.` : ''}. После выхода товар продаётся по обычной цене.
                </p>
              )
            )}
            {p.errors.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-bad/10 px-3 py-2 text-bad" data-testid="promo-errors">
                {p.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            )}
            {p.warnings.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-warn/10 px-3 py-2" data-testid="promo-warnings">
                {p.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {p.needs_loss_ack && p.can_apply && (
              <label className="flex items-start gap-2 text-bad">
                <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" data-testid="promo-ack" />
                Понимаю: каждая продажа по этой цене — в минус
              </label>
            )}
          </div>
        )}

        <p className="text-xs text-faint">
          Изменение уйдёт в Ozon сразу после подтверждения. Каждое действие записывается в журнал: что было, что стало, ответ
          Ozon.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
            Отмена
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={blocked}
            className={`rounded-lg px-4 py-2 font-medium text-white disabled:opacity-50 ${join ? 'bg-accent' : 'bg-bad'}`}
            data-testid="promo-confirm"
          >
            {sending ? 'Отправляем в Ozon…' : join ? (p?.action === 'update' ? 'Изменить в Ozon' : 'Добавить в акцию') : 'Убрать из акции'}
          </button>
        </div>
      </div>
    </Modal>
  )
}
