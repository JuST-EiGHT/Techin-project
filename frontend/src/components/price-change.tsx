'use client'
// Изменение цены в Ozon (шаг 6.2): ввод → «Рассчитать последствия» (предпросмотр — в Ozon ничего не меняется) →
// «Применить в Ozon» по preview_id. Предпросмотр живёт 15 минут; правила безопасности — BUSINESS_LOGIC §8.
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Modal } from '@/components/planning/Operations'
import { pctText, rub, rubSigned } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { fieldNumber, type CalculateResponse, type PriceApplyResult, type PricePreview } from '@/lib/pricing'

type Props = {
  product: { id: string; offer_id: string; name: string | null }
  current: string | null
  onClose: () => void
  onDone: (message: string) => void
}

const time = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
const plain = (v: string) => String(Number(v))

/** Введённая цена → строка для API или текст ошибки. */
function parsePrice(text: string): { value: string | null; bad: string | null } {
  const n = fieldNumber(text)
  if (n === null) return { value: null, bad: null }
  if (!Number.isFinite(n) || n <= 0) return { value: null, bad: 'Новая цена — число больше 0' }
  if (/[.,]\d{3,}\s*$/.test(text.trim())) return { value: null, bad: 'Цена — в рублях и копейках: не больше двух знаков после запятой' }
  return { value: n.toFixed(2), bad: null }
}

export function PriceChangeModal({ product, current, onClose, onDone }: Props) {
  const [price, setPrice] = useState('')
  const [reason, setReason] = useState('')
  const [calc, setCalc] = useState<CalculateResponse['result']>(null)
  const [preview, setPreview] = useState<PricePreview | null>(null)
  const [previewKey, setPreviewKey] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sending, setSending] = useState(false)
  const [now, setNow] = useState(() => Date.now())

  // наша минимальная и целевая цена — подсказки для ввода (только расчёт, ничего не меняет)
  useEffect(() => {
    api
      .post<CalculateResponse>('/price/calculate', { product_id: product.id })
      .then((r) => setCalc(r.data.result))
      .catch(() => setCalc(null))
  }, [product.id])

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])

  const { value, bad } = parsePrice(price)
  const key = JSON.stringify([value, reason.trim()])
  const p = preview
  const stale = !!p && previewKey !== key
  const expired = !!p && now > Date.parse(p.expires_at)

  async function calculate() {
    if (!value) return
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.post<PricePreview>('/prices/preview', {
        product_id: product.id,
        price: value,
        ...(reason.trim() ? { reason: reason.trim() } : {}),
      })
      setPreview(data)
      setPreviewKey(key)
      setNow(Date.now())
    } catch (err) {
      setError(errorMessage(err))
      setPreview(null)
    } finally {
      setBusy(false)
    }
  }

  async function confirm() {
    if (!p) return
    setSending(true)
    setError(null)
    try {
      const { data } = await api.post<PriceApplyResult>('/prices/apply', { preview_id: p.preview_id })
      onDone(data.message)
    } catch (err) {
      setError(errorMessage(err))
      setPreview(null) // предпросмотр израсходован или устарел — нужен новый
      setSending(false)
    }
  }

  function fill(v: string | null) {
    if (v) setPrice(plain(v))
  }

  const canApply = !!p && p.can_apply && !stale && !expired && !busy && !sending
  const suggest = [
    calc?.target_price ? { label: 'Целевая', value: calc.target_price } : null,
    calc?.min_price ? { label: 'Минимальная', value: calc.min_price } : null,
  ].filter(Boolean) as { label: string; value: string }[]
  // вне допустимого за один раз — подсказать ближайшую допустимую цену
  const want = value ? Number(value) : null
  const nearest =
    p && !stale && want !== null && p.allowed_from && p.allowed_to
      ? want > Number(p.allowed_to)
        ? p.allowed_to
        : want < Number(p.allowed_from)
          ? p.allowed_from
          : null
      : null
  const input = 'w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-sm text-fg outline-none placeholder:text-faint focus:border-accent'
  return (
    <Modal title="Изменить цену в Ozon" onClose={onClose}>
      <div className="space-y-4 text-sm" data-testid="price-change">
        <p>
          <span className="font-medium">{product.offer_id}</span>
          {product.name && <span className="text-muted"> · {product.name}</span>}
          <span className="block text-muted">Сейчас в Ozon: {rub(current)}</span>
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-muted">
            Новая цена, ₽
            <input
              inputMode="decimal"
              value={price}
              placeholder={current ? plain(current) : ''}
              onChange={(e) => setPrice(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && calculate()}
              className={`${input} mt-1 text-right`}
              autoFocus
              data-testid="price-new"
            />
            {suggest.length > 0 && (
              <span className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
                {suggest.map((s) => (
                  <button key={s.label} type="button" onClick={() => fill(s.value)} className="text-accent hover:underline">
                    {s.label} {rub(s.value)}
                  </button>
                ))}
              </span>
            )}
          </label>
          <label className="text-xs text-muted">
            Комментарий — необязательно
            <input
              value={reason}
              maxLength={500}
              placeholder="Например: поднять маржу"
              onChange={(e) => setReason(e.target.value)}
              className={`${input} mt-1`}
              data-testid="price-reason"
            />
            <span className="mt-1 block text-faint">Попадёт в историю изменений — потом проще вспомнить, зачем меняли.</span>
          </label>
        </div>

        <FormError message={bad ?? error} />
        <button
          type="button"
          onClick={calculate}
          disabled={!value || busy || sending}
          className="rounded-lg border border-accent px-4 py-2 font-medium text-accent hover:bg-accent/10 disabled:opacity-50"
          data-testid="price-preview"
        >
          {busy ? 'Считаем…' : 'Рассчитать последствия'}
        </button>

        {p && (
          <div className={`space-y-3 ${stale ? 'opacity-60' : ''}`} data-testid="price-preview-result">
            {stale && <p className="text-warn">Цена или комментарий изменились — рассчитайте последствия заново.</p>}
            <dl className="grid grid-cols-[1fr_auto_auto] items-baseline gap-x-4 gap-y-1.5" data-testid="price-figures">
              <span />
              <dt className="text-right text-xs text-faint">было</dt>
              <dt className="text-right text-xs text-faint">станет</dt>
              <dt className="text-muted">Цена</dt>
              <dd className="text-right tabular-nums">{rub(p.price_before)}</dd>
              <dd className="text-right font-medium tabular-nums">
                {rub(p.price_after)}
                {p.change_pct !== null && (
                  <span className="block text-xs font-normal text-muted">
                    {rubSigned(p.change_rub)} · {Number(p.change_pct) > 0 ? '+' : ''}
                    {pctText(p.change_pct)}
                  </span>
                )}
              </dd>
              <dt className="text-muted">Останется с 1 шт.</dt>
              <dd className={`text-right tabular-nums ${tone(p.profit_before)}`}>{rub(p.profit_before)}</dd>
              <dd className={`text-right font-medium tabular-nums ${tone(p.profit_after)}`} data-testid="price-profit-after">
                {rub(p.profit_after)}
              </dd>
              <dt className="text-muted">Маржа</dt>
              <dd className="text-right tabular-nums">{pctText(p.margin_before)}</dd>
              <dd className="text-right font-medium tabular-nums">{pctText(p.margin_after)}</dd>
            </dl>
            {p.margin_note && <p className="text-xs text-muted">{p.margin_note}</p>}
            <ul className="space-y-0.5 text-xs text-muted">
              {(p.min_price || p.target_price) && (
                <li>
                  Ваша минимальная цена {rub(p.min_price)}, целевая {rub(p.target_price)} — по расчёту цены.
                </li>
              )}
              {p.allowed_from && p.allowed_to && (
                <li>
                  За один раз сейчас можно от {rub(p.allowed_from)} до {rub(p.allowed_to)} (не больше 20 %, не ниже себестоимости с
                  НДС + 5 %{p.old_price ? ', ниже зачёркнутой' : ''}).
                </li>
              )}
              {p.old_price && <li>Зачёркнутая цена (до скидки) {rub(p.old_price)} — не меняется.</li>}
              <li>
                {p.price_fresh ? 'Текущую цену проверили в Ozon только что.' : 'Текущую цену в Ozon сейчас не проверить — расчёт по последним данным.'}
              </li>
            </ul>
            {nearest && (
              <button type="button" onClick={() => fill(nearest)} className="text-xs text-accent hover:underline" data-testid="price-nearest">
                Поставить ближайшую допустимую — {rub(nearest)}
              </button>
            )}
            {p.errors.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-bad/10 px-3 py-2 text-bad" data-testid="price-errors">
                {p.errors.map((e) => (
                  <li key={e}>{e}</li>
                ))}
              </ul>
            )}
            {p.warnings.length > 0 && (
              <ul className="space-y-1 rounded-lg bg-warn/10 px-3 py-2" data-testid="price-warnings">
                {p.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}
            {p.can_apply && !stale && (
              <p className={`text-xs ${expired ? 'text-warn' : 'text-faint'}`}>
                {expired ? 'Предпросмотр устарел — рассчитайте последствия заново.' : `Предпросмотр действует до ${time(p.expires_at)}.`}
              </p>
            )}
          </div>
        )}

        <p className="text-xs text-faint">
          В Ozon цена уйдёт только после «Применить», Ozon обработает её за 1–3 минуты. Каждое изменение записывается в журнал:
          что было, что стало, ответ Ozon.
        </p>
        <div className="flex flex-wrap justify-end gap-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
            Отмена
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!canApply}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
            data-testid="price-apply"
          >
            {sending ? 'Отправляем в Ozon…' : p && p.can_apply ? `Применить ${rub(p.price_after)}` : 'Применить'}
          </button>
        </div>
      </div>
    </Modal>
  )
}

function tone(v: string | null) {
  return v === null ? 'text-muted' : Number(v) < 0 ? 'text-bad' : 'text-ok'
}
