'use client'
// Групповые действия «Все товары» (шаг 10.13): выбранные товары → «Цены списком» (6.3, с предпросмотром), шаблон
// себестоимости по ним (2.4), добавить в акцию или убрать из неё (4.3: по каждому товару предпросмотр → подтверждение →
// журнал, правило 6), выгрузка выбранных в CSV.
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { saveFile } from '@/lib/download'
import { handOverSelection } from '@/lib/products'

const PROMO_LIMIT = 50 // за раз в акцию — не больше: каждый товар — отдельный запрос в Ozon

/** Панель над таблицей, пока что-то выбрано. */
export function BulkBar({
  ids,
  period,
  selectAll,
  onClear,
  onDone,
}: {
  ids: string[]
  period: { date_from: string; date_to: string }
  /** «Выбрать все N по фильтру» — пока выбраны не все. */
  selectAll: React.ReactNode
  onClear: () => void
  onDone: () => void
}) {
  const router = useRouter()
  const [promo, setPromo] = useState<'join' | 'leave' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<React.ReactNode>(null)

  function prices() {
    handOverSelection(ids)
    router.push('/dashboard/products/prices/bulk?from=products')
  }
  async function costs() {
    setError(null)
    try {
      await saveFile(api.post('/costs/template/selected', { product_ids: ids, format: 'xlsx' }, { responseType: 'blob' }), 'costs.xlsx')
      setNote(
        <>
          Шаблон скачан: заполните себестоимость и загрузите файл в{' '}
          <Link href="/dashboard/products/costs" className="text-accent hover:underline">
            «Себестоимость»
          </Link>
          .
        </>,
      )
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать шаблон'))
    }
  }
  async function exportSelected() {
    setError(null)
    try {
      await saveFile(api.post('/products/export/selected', { product_ids: ids, ...period }, { responseType: 'blob' }), 'products.csv')
    } catch (err) {
      setError(errorMessage(err, 'Не удалось скачать файл'))
    }
  }

  const btn = 'rounded-lg border border-line bg-card px-3 py-1.5 text-sm hover:border-accent hover:text-accent'
  return (
    <section className="space-y-2 rounded-xl border border-accent/50 bg-accent/5 p-3" data-testid="bulk-bar">
      <div className="flex flex-wrap items-center gap-2">
        <span className="mr-1 text-sm font-medium" data-testid="bulk-count">
          Выбрано: {ids.length}
        </span>
        {selectAll}
        <button type="button" onClick={prices} className={btn} data-testid="bulk-prices">
          Изменить цены
        </button>
        <button type="button" onClick={costs} className={btn} data-testid="bulk-costs">
          Заполнить себестоимость
        </button>
        <button type="button" onClick={() => setPromo('join')} className={btn} data-testid="bulk-promo-join">
          В акцию
        </button>
        <button type="button" onClick={() => setPromo('leave')} className={btn} data-testid="bulk-promo-leave">
          Из акции
        </button>
        <button type="button" onClick={exportSelected} className={btn} data-testid="bulk-export">
          Выгрузить
        </button>
        <button type="button" onClick={onClear} className="ml-auto px-2 text-sm text-muted hover:text-fg" data-testid="bulk-clear">
          Снять выбор
        </button>
      </div>
      <p className="text-xs text-muted">
        Цены и акции меняются в Ozon только после предпросмотра и вашего подтверждения, каждое изменение — в журнале.
      </p>
      {note && <p className="text-sm" data-testid="bulk-note">{note}</p>}
      <FormError message={error} />
      {promo && (
        <PromoDialog
          mode={promo}
          ids={ids}
          onClose={() => setPromo(null)}
          onDone={() => {
            setPromo(null)
            onDone()
          }}
        />
      )}
    </section>
  )
}

type Promotion = { id: string; title: string; date_from: string | null; date_to: string | null; products_active: number; candidates: number; is_frozen: boolean }
type PromoItem = { product: { id: string; offer_id: string; name: string | null }; is_active: boolean }
type Preview = {
  can_apply: boolean
  errors: string[]
  warnings: string[]
  needs_loss_ack: boolean
  action_price: string | null
  margin: string | null
}
type Row = { id: string; offer_id: string; name: string | null; preview?: Preview; error?: string; result?: 'applied' | 'failed'; message?: string }

const fmtDate = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' }) : '…')

/** В акцию / из акции: выбрать акцию → кто из выбранных подходит → предпросмотр по каждому → подтвердить. */
function PromoDialog({ mode, ids, onClose, onDone }: { mode: 'join' | 'leave'; ids: string[]; onClose: () => void; onDone: () => void }) {
  const [promos, setPromos] = useState<Promotion[] | null>(null)
  const [promo, setPromo] = useState<Promotion | null>(null)
  const [rows, setRows] = useState<Row[] | null>(null)
  const [skipped, setSkipped] = useState(0)
  const [stage, setStage] = useState<'choose' | 'checked' | 'applying' | 'done'>('choose')
  const [acceptLoss, setAcceptLoss] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const join = mode === 'join'

  useEffect(() => {
    api
      .get<Promotion[]>('/promotions')
      .then(({ data }) => setPromos(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  async function check(p: Promotion) {
    setPromo(p)
    setBusy(true)
    setError(null)
    setRows(null)
    try {
      const { data } = await api.get<{ items: PromoItem[] }>(`/promotions/${p.id}/products`)
      const chosen = new Set(ids)
      const fit = data.items.filter((i) => chosen.has(i.product.id) && i.is_active !== join)
      const taken = fit.slice(0, PROMO_LIMIT)
      setSkipped(ids.length - taken.length)
      const out: Row[] = []
      for (const i of taken) {
        const row: Row = { id: i.product.id, offer_id: i.product.offer_id, name: i.product.name }
        try {
          const { data: pv } = await api.post<Preview>(`/promotions/${p.id}/products/${i.product.id}/${mode}`, { confirm: false })
          row.preview = pv
        } catch (err) {
          row.error = errorMessage(err)
        }
        out.push(row)
        setRows([...out])
      }
      setRows(out)
      setStage('checked')
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const ready = (rows ?? []).filter((r) => r.preview?.can_apply && (!r.preview.needs_loss_ack || acceptLoss))
  const needLoss = (rows ?? []).some((r) => r.preview?.can_apply && r.preview.needs_loss_ack)

  async function apply() {
    if (!promo) return
    setStage('applying')
    const out = [...(rows ?? [])]
    for (const r of out) {
      if (!ready.includes(r)) continue
      try {
        await api.post(`/promotions/${promo.id}/products/${r.id}/${mode}`, join ? { confirm: true, accept_loss: acceptLoss } : { confirm: true })
        r.result = 'applied'
      } catch (err) {
        r.result = 'failed'
        r.message = errorMessage(err)
      }
      setRows([...out])
    }
    setStage('done')
  }

  const applied = (rows ?? []).filter((r) => r.result === 'applied').length
  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-bg/70 sm:items-center sm:p-4" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-xl border border-line bg-card p-5 shadow-xl sm:rounded-xl"
        data-testid="promo-dialog"
      >
        <div className="flex items-start gap-3">
          <h2 className="text-lg font-semibold">{join ? 'Добавить в акцию' : 'Убрать из акции'}</h2>
          <button type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-muted hover:text-fg" aria-label="Закрыть">
            ✕
          </button>
        </div>
        <p className="mt-1 text-sm text-muted">
          Выбрано товаров: {ids.length}. Сначала предпросмотр по каждому товару — цена в акции, маржа, проверки; в Ozon уйдут только те, что
          вы подтвердите.
        </p>
        <FormError message={error} />

        {stage === 'choose' && (
          <div className="mt-4 space-y-2" data-testid="promo-list">
            {!promos && !error && <p className="text-sm text-muted">Загрузка акций…</p>}
            {promos && promos.length === 0 && <p className="text-sm text-muted">Текущих акций Ozon нет.</p>}
            {promos?.map((p) => (
              <button
                key={p.id}
                type="button"
                disabled={busy || (join && p.is_frozen)}
                onClick={() => check(p)}
                className={`block w-full rounded-lg border px-3 py-2 text-left text-sm hover:border-accent disabled:opacity-50 ${promo?.id === p.id ? 'border-accent' : 'border-line'}`}
                data-testid="promo-option"
              >
                <span className="font-medium">{p.title}</span>
                <span className="block text-xs text-muted">
                  {fmtDate(p.date_from)} – {fmtDate(p.date_to)} · участвует {p.products_active}, могут участвовать {p.candidates}
                  {join && p.is_frozen && ' · список товаров уже не меняется'}
                </span>
              </button>
            ))}
            {busy && rows && <p className="text-sm text-muted">Проверяем товары: {rows.length}…</p>}
          </div>
        )}

        {stage !== 'choose' && promo && rows && (
          <div className="mt-4 space-y-3" data-testid="promo-preview">
            <p className="text-sm">
              «{promo.title}»: подходят {rows.length}
              {skipped > 0 && (
                <span className="text-muted">
                  {' '}
                  · не подходят {skipped} — {join ? 'уже участвуют или Ozon не предлагает их для этой акции' : 'не участвуют в ней'}
                  {rows.length === PROMO_LIMIT && `; за раз — не больше ${PROMO_LIMIT}`}
                </span>
              )}
            </p>
            {rows.length > 0 && (
              <ul className="divide-y divide-line rounded-lg border border-line text-sm">
                {rows.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-3 py-2" data-testid="promo-row">
                    <span className="min-w-0 flex-1">
                      <span className="block font-medium">{r.offer_id}</span>
                      {r.preview && join && (
                        <span className="block text-xs text-muted">
                          цена в акции {rub(r.preview.action_price)} · маржа {pctText(r.preview.margin)}
                        </span>
                      )}
                      {(r.error || r.preview?.errors.length || r.preview?.warnings.length || r.message) && (
                        <span className={`block text-xs ${r.error || r.preview?.errors.length || r.result === 'failed' ? 'text-bad' : 'text-warn'}`}>
                          {r.message ?? r.error ?? [...(r.preview?.errors ?? []), ...(r.preview?.warnings ?? [])].join(' ')}
                        </span>
                      )}
                    </span>
                    {r.result === 'applied' ? (
                      <Badge tone="ok">готово</Badge>
                    ) : r.result === 'failed' ? (
                      <Badge tone="bad">не вышло</Badge>
                    ) : r.preview?.can_apply ? (
                      r.preview.needs_loss_ack ? <Badge tone="warn">в минус</Badge> : <Badge tone="ok">можно</Badge>
                    ) : (
                      <Badge tone="bad">нельзя</Badge>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {stage === 'checked' && needLoss && join && (
              <label className="flex items-start gap-2 text-sm">
                <input type="checkbox" checked={acceptLoss} onChange={(e) => setAcceptLoss(e.target.checked)} className="mt-1" data-testid="promo-accept-loss" />
                Цена в акции у части товаров ниже себестоимости с расходами — всё равно добавить их
              </label>
            )}
            <div className="flex flex-wrap justify-end gap-3">
              {stage === 'checked' && (
                <>
                  <button type="button" onClick={() => setStage('choose')} className="rounded-lg border border-line px-4 py-2 text-sm hover:border-muted">
                    Другая акция
                  </button>
                  <button
                    type="button"
                    disabled={ready.length === 0}
                    onClick={apply}
                    className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-50"
                    data-testid="promo-apply"
                  >
                    {join ? 'Добавить' : 'Убрать'} в Ozon: {ready.length}
                  </button>
                </>
              )}
              {stage === 'applying' && <span className="text-sm text-muted">Отправляем в Ozon…</span>}
              {stage === 'done' && (
                <>
                  <span className="mr-auto text-sm" data-testid="promo-done">
                    Готово: {applied} из {ready.length}. Каждое изменение записано в журнал.
                  </span>
                  <button type="button" onClick={onDone} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90">
                    Закрыть
                  </button>
                </>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
