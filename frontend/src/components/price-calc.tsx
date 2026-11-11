'use client'
// Калькулятор цены (шаг 4.2): блок «Расчёт цены» в панели товара и форма настроек «реклама % и целевая маржа %».
// «А если поставить …» (10.15): прибыль, расходы и маржа сразу при нескольких ценах — тем же расчётом.
// Юнит-экономика как у Ozon (4.2a): реклама, возвраты и невыкупы, хранение — по начислениям, у каждой цифры — откуда она.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { pctText, rub } from '@/components/reports'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { AD_SETTING_WHY, AD_WHY, EXTRA_WHY, MARGIN_WHY, fieldNumber, type Breakdown, type CalculateResponse, type Overrides } from '@/lib/pricing'
import { notifyPriceInputsChanged, saveSettings, useSettings } from '@/lib/settings'

const EMPTY: Overrides = { ad_pct: '', target_margin_pct: '', logistics: '', storage: '', other: '', extra_pct: '', returns: '', price: '' }
const LIMITS: Record<keyof Overrides, [number, number, string]> = {
  ad_pct: [0, 90, 'Реклама — от 0 до 90 %'],
  target_margin_pct: [0, 90, 'Целевая маржа — от 0 до 90 %'],
  logistics: [0, 1_000_000, 'Логистика — число не меньше 0'],
  storage: [0, 1_000_000, 'Хранение — число не меньше 0'],
  other: [0, 1_000_000, 'Прочие расходы — число не меньше 0'],
  extra_pct: [0, 90, 'Доп. расходы — от 0 до 90 %'],
  returns: [0, 1_000_000, 'Возвраты — число не меньше 0'],
  price: [0.01, 100_000_000, 'Цена — число больше 0'],
}

/** Свои значения → тело запроса; ошибка ввода — текст. */
function overridesBody(ov: Overrides): { body: Record<string, string>; error: string | null } {
  const body: Record<string, string> = {}
  for (const key of Object.keys(ov) as (keyof Overrides)[]) {
    const n = fieldNumber(ov[key])
    if (n === null) continue
    const [lo, hi, text] = LIMITS[key]
    if (!Number.isFinite(n) || n < lo || n > hi) return { body, error: text }
    body[key] = String(n)
  }
  return { body, error: null }
}

const num = (v: string | null) => (v === null ? '—' : Number(v).toLocaleString('ru-RU', { maximumFractionDigits: 2 }))

function Field({
  label,
  value,
  placeholder,
  onChange,
  suffix,
  hint,
  testid,
  disabled = false,
}: {
  label: string
  value: string
  placeholder: string
  onChange: (v: string) => void
  suffix: string
  hint?: string
  testid: string
  disabled?: boolean
}) {
  return (
    <label className="block text-xs text-muted">
      {label}
      <span className="mt-1 flex items-center gap-1.5">
        <input
          inputMode="decimal"
          value={value}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className="w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-right text-sm text-fg outline-none placeholder:text-faint focus:border-accent disabled:opacity-60"
          data-testid={testid}
        />
        <span className="text-faint">{suffix}</span>
      </span>
      {hint && <span className="mt-1 block text-faint">{hint}</span>}
    </label>
  )
}

/** «Расчёт цены» в панели товара: минимальная и целевая цена, маржа сейчас и в акции, из чего складывается цена. */
export function PriceCalcBlock({ productId, refreshKey }: { productId: string; refreshKey: unknown }) {
  const { user } = useAuth()
  const settings = useSettings()
  const [ov, setOv] = useState<Overrides>(EMPTY)
  const [data, setData] = useState<CalculateResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [saved, setSaved] = useState<string | null>(null)
  const [savedNote, setSavedNote] = useState<string | null>(null)
  const [extrasVersion, setExtrasVersion] = useState(0)
  const seq = useRef(0)
  const { body, error: inputError } = overridesBody(ov)
  const key = JSON.stringify(body)

  useEffect(() => {
    setOv(EMPTY)
    setSaved(null)
    setSavedNote(null)
  }, [productId])

  useEffect(() => {
    if (inputError) return
    const id = ++seq.current
    setBusy(true)
    const t = setTimeout(() => {
      api
        .post<CalculateResponse>('/price/calculate', { product_id: productId, ...body })
        .then((r) => {
          if (id !== seq.current) return
          setData(r.data)
          setError(null)
        })
        .catch((err) => id === seq.current && setError(errorMessage(err)))
        .finally(() => id === seq.current && setBusy(false))
    }, 400)
    return () => clearTimeout(t)
    // body сравниваем по содержимому (key); refreshKey — данные товара перечитаны (например, себестоимость)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, key, inputError, refreshKey, extrasVersion, settings?.price_ad_pct, settings?.price_target_margin_pct, settings?.price_extra_pct])

  const set = (k: keyof Overrides) => (v: string) => {
    setSaved(null)
    setSavedNote(null)
    setOv((o) => ({ ...o, [k]: v }))
  }

  const changedSettings =
    settings !== null && body.target_margin_pct !== undefined && Number(body.target_margin_pct) !== Number(settings.price_target_margin_pct)

  // свои расходы товара: реклама, хранение, прочее, доп. % — сохраняются для всех расчётов (правка 06.11.2026, 4.2a)
  const savedExtras = data?.inputs.saved ?? null
  const extrasTyped = body.ad_pct !== undefined || body.storage !== undefined || body.other !== undefined || body.extra_pct !== undefined
  async function saveExtras(clear = false) {
    const keep = (typed: string | undefined, was: string | null | undefined) => (clear ? null : typed ?? was ?? null)
    try {
      await api.put(`/price/extras/${productId}`, {
        ad_pct: keep(body.ad_pct, savedExtras?.ad_pct),
        storage: keep(body.storage, savedExtras?.storage),
        other: keep(body.other, savedExtras?.other),
        extra_pct: keep(body.extra_pct, savedExtras?.extra_pct),
      })
      setOv((o) => ({ ...o, ad_pct: '', storage: '', other: '', extra_pct: '' }))
      setSavedNote(clear ? 'Свои значения убраны — товар считается по начислениям и общим настройкам' : 'Сохранено — так этот товар считается везде: цены, товары, акции')
      setExtrasVersion((v) => v + 1)
      notifyPriceInputsChanged()
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  async function saveMine() {
    if (!user || !settings) return
    try {
      await saveSettings(user.id, { price_target_margin_pct: body.target_margin_pct ?? settings.price_target_margin_pct })
      setOv((o) => ({ ...o, target_margin_pct: '' }))
      setSaved('Сохранено — так считаются цены всех товаров')
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  const i = data?.inputs
  const r = data?.result
  const shown = r?.current ?? null
  return (
    <section data-testid="panel-price-calc">
      <div className="mb-2 flex items-baseline gap-3">
        <h3 className="text-xs uppercase tracking-wider text-muted">Расчёт цены</h3>
        <Link href="/dashboard/products/prices" className="ml-auto text-xs text-accent hover:underline">
          Все цены →
        </Link>
      </div>
      <FormError message={error ?? inputError} />
      {!data ? (
        !error && <p className="text-sm text-muted">Считаем…</p>
      ) : (
        <div className={`space-y-3 transition-opacity ${busy ? 'opacity-60' : ''}`} aria-busy={busy}>
          {data.missing.length > 0 ? (
            <div className="rounded-lg bg-warn/10 px-3 py-2 text-sm" data-testid="calc-missing">
              <p className="font-medium">Посчитать пока нельзя:</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                {data.missing.map((m) => (
                  <li key={m}>{m}</li>
                ))}
              </ul>
            </div>
          ) : (
            r && (
              <>
                <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-line bg-line">
                  <Figure
                    label="Минимальная цена"
                    value={r.min_price ? rub(r.min_price) : '—'}
                    hint={r.min_error ?? 'ниже — каждая продажа в минус'}
                    tone={r.min_error ? 'text-bad' : ''}
                    testid="calc-min"
                  />
                  <Figure
                    label={`Целевая — маржа ${num(i!.target_margin_pct)} %`}
                    value={r.target_price ? rub(r.target_price) : '—'}
                    hint={r.target_error ?? 'по вашей целевой марже'}
                    tone={r.target_error ? 'text-bad' : 'text-accent'}
                    testid="calc-target"
                  />
                </div>
                {i!.price && (
                  <p className={`text-sm ${r.profitable === false ? 'text-bad' : ''}`} data-testid="calc-current">
                    {i!.sources.price === 'ваше значение' ? 'При цене' : 'Сейчас'} {rub(i!.price)}:{' '}
                    {r.profitable === false
                      ? `${r.min_price ? `ниже минимальной на ${rub(String(Number(r.min_price) - Number(i!.price)))} — ` : ''}в минус ${rub(String(-Number(shown!.profit)))} с каждой штуки`
                      : `остаётся ${rub(shown!.profit)} с штуки, маржа ${pctText(shown!.margin)}`}
                  </p>
                )}
                {r.floor_price && Number(r.min_profit ?? 0) > 0 && (
                  // минимальная допустимая прибыль («Настройки → Прибыльность», 10.26)
                  <p className={`text-sm ${r.floor_ok === false ? 'text-warn' : 'text-muted'}`} data-testid="calc-floor">
                    Чтобы оставалось не меньше {rub(r.min_profit!)} с продажи — цена от {rub(r.floor_price)}
                    {r.floor_ok === false ? ': текущая ниже' : ''}
                  </p>
                )}
                {r.promo && (
                  <p className={`text-sm ${Number(r.promo.profit) < 0 ? 'text-bad' : 'text-muted'}`} data-testid="calc-promo">
                    В акции{data.promo_title ? ` «${data.promo_title}»` : ''} за {rub(r.promo.price)}: маржа {pctText(r.promo.margin)} (
                    {rub(r.promo.profit)} с штуки)
                  </p>
                )}
              </>
            )
          )}

          <div className="grid grid-cols-2 gap-3">
            <Field
              label="Реклама, % от цены"
              value={ov.ad_pct}
              placeholder={num(i?.ad_pct ?? null)}
              onChange={set('ad_pct')}
              suffix="%"
              hint={i?.sources.ad_pct === 'сохранено для товара' ? 'своя у товара' : i?.sources.ad_pct}
              testid="calc-ad"
            />
            <Field
              label="Целевая маржа"
              value={ov.target_margin_pct}
              placeholder={num(i?.target_margin_pct ?? null)}
              onChange={set('target_margin_pct')}
              suffix="%"
              testid="calc-margin"
            />
          </div>
          <p className="text-xs text-faint">
            {AD_WHY} {MARGIN_WHY}
          </p>
          {changedSettings && (
            <button type="button" onClick={saveMine} className="rounded-lg border border-accent px-3 py-1.5 text-sm text-accent hover:bg-accent/10" data-testid="calc-save-settings">
              Сохранить целевую маржу — для всех товаров
            </button>
          )}
          {body.ad_pct !== undefined && !inputError && (
            <button type="button" onClick={() => saveExtras()} className="rounded-lg border border-accent px-3 py-1.5 text-sm text-accent hover:bg-accent/10" data-testid="calc-save-ad">
              Сохранить рекламу {num(body.ad_pct)} % для этого товара — вместо начислений
            </button>
          )}
          {saved && <p className="text-sm text-ok">{saved}</p>}

          <details className="rounded-lg border border-line px-3 py-2" open={!!savedExtras} data-testid="calc-extras">
            <summary className="cursor-pointer text-sm text-muted hover:text-fg">
              Свои расходы товара — сохраняются{savedExtras ? ' · есть свои' : ''}
            </summary>
            <p className="mt-2 text-xs text-faint">
              Свои значения вместо начислений Ozon и расходы, которых в них нет. Сохранённые входят в минимальную и целевую цену и маржу
              этого товара везде: на страницах цен, товаров и акций. Для всех товаров сразу — в «Настройках → Цены».
            </p>
            {savedExtras?.ad_pct != null && (
              <p className="mt-2 text-xs text-warn" data-testid="calc-saved-ad">
                Реклама — ваша: {num(savedExtras.ad_pct)} % вместо начислений Ozon.
              </p>
            )}
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field
                label="Хранение на 1 шт."
                value={ov.storage}
                placeholder={num(i?.storage ?? '0')}
                onChange={set('storage')}
                suffix="₽"
                hint={i?.sources.storage === 'сохранено для товара' ? 'ваше — вместо начислений' : i?.sources.storage}
                testid="calc-storage"
              />
              <Field label="Прочее на 1 шт." value={ov.other} placeholder={num(i?.other ?? '0')} onChange={set('other')} suffix="₽" hint="упаковка, маркировка, доставка до склада" testid="calc-other" />
              <Field
                label="Доп. расходы, % от цены"
                value={ov.extra_pct}
                placeholder={num(i?.extra_pct ?? '0')}
                onChange={set('extra_pct')}
                suffix="%"
                hint={i?.sources.extra_pct === 'сохранено для товара' ? 'свои у товара' : `общие: ${num(i?.shop_extra_pct ?? '0')} % — «Настройки → Цены»`}
                testid="calc-extra"
              />
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-2">
              {extrasTyped && !inputError && (
                <button type="button" onClick={() => saveExtras()} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90" data-testid="calc-save-extras">
                  Сохранить для этого товара
                </button>
              )}
              {savedExtras && (
                <button type="button" onClick={() => saveExtras(true)} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad" data-testid="calc-clear-extras">
                  Убрать свои расходы
                </button>
              )}
              {savedNote && <span className="text-sm text-ok" data-testid="calc-extras-saved">{savedNote}</span>}
            </div>
          </details>

          <details className="rounded-lg border border-line px-3 py-2" data-testid="calc-more">
            <summary className="cursor-pointer text-sm text-muted hover:text-fg">Проверить другую логистику, возвраты или цену — только для этого расчёта</summary>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <Field
                label="Логистика на 1 шт."
                value={ov.logistics}
                placeholder={num(i?.logistics ?? null)}
                onChange={set('logistics')}
                suffix="₽"
                hint={i?.logistics_text}
                testid="calc-logistics"
              />
              <Field
                label="Возвраты и невыкупы на 1 шт."
                value={ov.returns}
                placeholder={num(i?.returns ?? '0')}
                onChange={set('returns')}
                suffix="₽"
                hint={i?.sources.returns}
                testid="calc-returns"
              />
              <Field label="Проверить цену" value={ov.price} placeholder={num(i?.price ?? null)} onChange={set('price')} suffix="₽" hint="сколько останется при другой цене" testid="calc-price" />
            </div>
          </details>

          {r && i?.price && <WhatIf productId={productId} body={body} data={data} />}

          {shown && <BreakdownTable b={shown} data={data} />}

          <div className="text-xs text-faint" data-testid="calc-tax">
            {data.tax.known ? `Налог: ${data.tax.title}` : 'Налог не задан'}
            {!data.tax.known && (
              <>
                {' '}
                —{' '}
                <Link href="/dashboard/settings/taxes" className="text-accent hover:underline">
                  указать →
                </Link>
              </>
            )}
            {data.tax.notes.map((n) => (
              <p key={n} className="mt-0.5">
                {n}
              </p>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

function Figure({ label, value, hint, tone = '', testid }: { label: string; value: string; hint: string; tone?: string; testid: string }) {
  return (
    <div className="bg-card px-3 py-2" data-testid={testid}>
      <div className="text-[11px] uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-0.5 text-lg font-bold tabular-nums ${tone}`}>{value}</div>
      <div className="text-xs text-faint">{hint}</div>
    </div>
  )
}

/** Из чего складывается цена одной штуки — с источником каждой цифры. */
function BreakdownTable({ b, data }: { b: Breakdown; data: CalculateResponse }) {
  const i = data.inputs
  const line = (label: React.ReactNode, v: string, opts: { strong?: boolean; tone?: string; note?: string; hide?: boolean } = {}) =>
    opts.hide ? null : (
      <tr className={`border-b border-line last:border-b-0 ${opts.strong ? 'font-semibold' : ''}`}>
        <td className="py-1.5 pr-3">
          {label}
          {opts.note && <span className="block text-[11px] font-normal text-faint">{opts.note}</span>}
        </td>
        <td className={`whitespace-nowrap py-1.5 text-right tabular-nums ${opts.tone ?? ''}`}>{rub(v)}</td>
      </tr>
    )
  const zero = (v: string) => Number(v) === 0
  return (
    <div>
      <h4 className="mb-1 text-xs text-muted">Из чего складывается цена {rub(b.price)} — на 1 шт.</h4>
      <table className="w-full text-sm" data-testid="calc-breakdown">
        <tbody>
          {line('Цена', b.price)}
          {line(`− Комиссия Ozon ${num(i.commission_pct)} %`, b.commission, { note: i.sources.commission_pct })}
          {line(`− Эквайринг ${num(i.acquiring_pct)} %`, b.acquiring, { hide: zero(b.acquiring) })}
          {line(`− Реклама ${num(i.ad_pct)} %`, b.ad, { hide: zero(b.ad), note: adNote(data) })}
          {line(`− Доп. расходы ${num(i.extra_pct ?? '0')} %`, b.extra ?? '0', { hide: zero(b.extra ?? '0') })}
          {line('− Логистика', b.logistics, { note: i.logistics_text })}
          {line('− Возвраты и невыкупы', b.returns ?? '0', { hide: zero(b.returns ?? '0'), note: i.sources.returns })}
          {line('− Хранение', b.storage, { hide: zero(b.storage), note: i.sources.storage })}
          {line('− Прочие расходы', b.other, { hide: zero(b.other) })}
          {line('− Себестоимость с НДС поставщика', b.cost, { note: i.sources.cost })}
          {line('− НДС к уплате', b.vat, { hide: zero(b.vat), note: 'с продаж, за вычетом входящего — если есть вычет' })}
          {line('− Налог с дохода', b.income_tax, { hide: zero(b.income_tax) })}
          {line('= Остаётся до налога на прибыль', b.profit, { strong: true, tone: Number(b.profit) < 0 ? 'text-bad' : 'text-ok' })}
          {Number(b.profit_tax) > 0 && line(`Налог на прибыль ${num(data.tax.profit_tax_pct)} % — после него`, b.net, { note: `−${rub(b.profit_tax)}` })}
        </tbody>
      </table>
      <p className="mt-1 text-xs text-faint">Маржа {pctText(b.margin)} — остаток в % от цены.</p>
      <BuyoutNote data={data} />
    </div>
  )
}

/** Откуда реклама: своя у товара и без товара (кампании) — по начислениям; иначе — средняя или настройка. */
function adNote(data: CalculateResponse): string {
  const i = data.inputs
  const f = i.facts
  if (i.sources.ad_pct === 'сохранено для товара' || i.sources.ad_pct === 'ваше значение') return 'ваше значение — вместо начислений'
  if (f?.own && f.ad_own_pct !== null && f.ad_shared_pct !== null) {
    return `${i.sources.ad_pct}: товара ${num(f.ad_own_pct)} % + без товара (кампании) ${num(f.ad_shared_pct)} % — по доле в продажах`
  }
  return i.sources.ad_pct ?? ''
}

/** Выкуп (4.2a): сколько доехавших купили — невыкупы и возвраты уже в «Возвратах» на каждую проданную штуку. */
function BuyoutNote({ data }: { data: CalculateResponse }) {
  const f = data.inputs.facts
  if (!f?.own || f.buyout_pct === null) return null
  const low = Number(f.buyout_pct) < 80
  return (
    <p className={`mt-1 text-xs ${low ? 'text-warn' : 'text-faint'}`} data-testid="calc-buyout">
      Выкуп {num(f.buyout_pct)} % за {f.days} дн.: продано {f.qty} шт.
      {f.not_bought > 0 && `, не выкупили ${f.not_bought}`}
      {f.returned > 0 && `, вернули ${f.returned}`}. Обратная логистика и обработка невыкупов и возвратов — в «Возвратах» на каждую
      проданную штуку.
    </p>
  )
}

/** Реклама и целевая маржа — настройки калькулятора цены («Настройки» и страница цен). */
export function PriceSettingsForm({ onSaved }: { onSaved?: () => void }) {
  const { user } = useAuth()
  const settings = useSettings()
  const [ad, setAd] = useState('')
  const [margin, setMargin] = useState('')
  const [extra, setExtra] = useState('')
  const [dirty, setDirty] = useState(false) // начали править — пришедшие настройки не затирают ввод
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings || dirty) return
    setAd(num(settings.price_ad_pct))
    setMargin(num(settings.price_target_margin_pct))
    setExtra(num(settings.price_extra_pct ?? '0'))
  }, [settings, dirty])
  const edit = (setter: (v: string) => void) => (v: string) => {
    setDirty(true)
    setDone(false)
    setter(v)
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setError(null)
    setDone(false)
    const { body, error: bad } = overridesBody({ ...EMPTY, ad_pct: ad || '0', target_margin_pct: margin || '0', extra_pct: extra || '0' })
    if (bad) return setError(bad)
    try {
      await saveSettings(user.id, { price_ad_pct: body.ad_pct, price_target_margin_pct: body.target_margin_pct, price_extra_pct: body.extra_pct })
      setDirty(false)
      setDone(true)
      onSaved?.()
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  return (
    <form onSubmit={save} className="space-y-3" data-testid="price-settings">
      <FormError message={error} />
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <Field label="Реклама, % — если данных Ozon нет" value={ad} placeholder="0" onChange={edit(setAd)} suffix="%" testid="settings-ad" disabled={!settings} />
          <p className="mt-1 text-xs text-faint">{AD_SETTING_WHY}</p>
        </div>
        <div>
          <Field label="Целевая маржа" value={margin} placeholder="20" onChange={edit(setMargin)} suffix="%" testid="settings-margin" disabled={!settings} />
          <p className="mt-1 text-xs text-faint">{MARGIN_WHY}</p>
        </div>
        <div>
          <Field label="Доп. расходы, % от цены — для всех товаров" value={extra} placeholder="0" onChange={edit(setExtra)} suffix="%" testid="settings-extra" disabled={!settings} />
          <p className="mt-1 text-xs text-faint">{EXTRA_WHY} У отдельного товара свои расходы — в «Расчёте цены» товара.</p>
        </div>
      </div>
      {settings?.price_defaults && <p className="text-xs text-warn">Пока стоят значения по умолчанию — поставьте свои.</p>}
      <div className="flex items-center gap-3">
        <button type="submit" disabled={!settings} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="settings-price-save">
          Сохранить
        </button>
        {done && <span className="text-sm text-ok">Сохранено — цены пересчитаны</span>}
      </div>
    </form>
  )
}

/** Цены для «а если»: минимальная, текущая ±10 %, текущая, целевая и своя — по возрастанию, без повторов. */
function whatIfPrices(data: CalculateResponse, own: number | null): number[] {
  const r = data.result!
  const current = Number(data.inputs.price)
  const list = [r.min_price && Number(r.min_price), current * 0.9, current, current * 1.1, r.target_price && Number(r.target_price), own]
  const prices = list.filter((v): v is number => typeof v === 'number' && v > 0).map((v) => Math.round(v * 100) / 100)
  return Array.from(new Set(prices)).sort((a, b) => a - b)
}

/** «А если поставить …»: та же формула при нескольких ценах — сколько уходит на расходы, сколько остаётся, маржа. */
function WhatIf({ productId, body, data }: { productId: string; body: Record<string, string>; data: CalculateResponse }) {
  const [own, setOwn] = useState('')
  const [rows, setRows] = useState<Breakdown[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const ownValue = fieldNumber(own)
  const ownOk = ownValue !== null && Number.isFinite(ownValue) && ownValue > 0 && ownValue <= 100_000_000
  const prices = whatIfPrices(data, ownOk ? ownValue : null)
  const key = JSON.stringify([body, prices])
  useEffect(() => {
    let alive = true
    const t = setTimeout(() => {
      api
        .post<CalculateResponse>('/price/calculate', { product_id: productId, ...body, what_if: prices.map((p) => p.toFixed(2)) })
        .then(({ data: d }) => {
          if (!alive) return
          setRows(d.result?.what_if ?? null)
          setError(null)
        })
        .catch((err) => alive && setError(errorMessage(err)))
    }, 300)
    return () => {
      alive = false
      clearTimeout(t)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [productId, key])

  const r = data.result!
  const current = Number(data.inputs.price)
  const label = (price: number) => {
    const marks = []
    if (r.min_price && price === Number(r.min_price)) marks.push('минимальная')
    if (r.target_price && price === Number(r.target_price)) marks.push('целевая')
    if (price === Math.round(current * 100) / 100) marks.push('сейчас')
    if (ownOk && price === Math.round(ownValue! * 100) / 100) marks.push('ваша')
    if (!marks.length) marks.push(`${price > current ? '+' : '−'}10 %`)
    return marks.join(', ')
  }
  return (
    <div className="rounded-lg border border-line p-3" data-testid="calc-what-if">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="text-sm font-medium">А если поставить…</h4>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          своя цена
          <input
            inputMode="decimal"
            value={own}
            onChange={(e) => setOwn(e.target.value)}
            placeholder="например, 1500"
            aria-label="Своя цена"
            className="w-28 rounded-lg border border-line bg-bg px-2 py-1 text-right text-sm text-fg outline-none placeholder:text-faint focus:border-accent"
            data-testid="what-if-own"
          />
          ₽
        </label>
      </div>
      <FormError message={error ?? (own && !ownOk ? 'Своя цена — число больше 0' : null)} />
      {rows && (
        <div className="-mx-3 mt-2 overflow-x-auto px-3">
          <table className="w-full min-w-[22rem] text-sm">
            <thead className="text-left text-xs text-faint">
              <tr>
                <th className="py-1 pr-2 font-normal">Цена</th>
                <th className="py-1 pr-2 text-right font-normal" title="Комиссия, эквайринг, реклама, логистика, себестоимость, налоги — на 1 шт.">
                  Расходы
                </th>
                <th className="py-1 pr-2 text-right font-normal">Остаётся</th>
                <th className="py-1 text-right font-normal">Маржа</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((b) => {
                const price = Number(b.price)
                const loss = Number(b.profit) < 0
                return (
                  <tr key={b.price} className={`border-t border-line ${price === Math.round(current * 100) / 100 ? 'font-medium' : ''}`} data-testid="what-if-row">
                    <td className="py-1.5 pr-2 tabular-nums">
                      {rub(b.price)}
                      <span className="block text-[11px] font-normal text-faint">{label(price)}</span>
                    </td>
                    <td className="whitespace-nowrap py-1.5 pr-2 text-right tabular-nums text-muted">{rub(String(price - Number(b.profit)))}</td>
                    <td className={`whitespace-nowrap py-1.5 pr-2 text-right tabular-nums ${loss ? 'text-bad' : 'text-ok'}`}>{rub(b.profit)}</td>
                    <td className={`whitespace-nowrap py-1.5 text-right tabular-nums ${loss ? 'text-bad' : ''}`}>{pctText(b.margin)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-1 text-xs text-faint">До налога на прибыль, на 1 шт. Цена в Ozon не меняется — это только расчёт.</p>
    </div>
  )
}
