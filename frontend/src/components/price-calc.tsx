'use client'
// Калькулятор цены (шаг 4.2): блок «Расчёт цены» в панели товара и форма настроек «реклама % и целевая маржа %».
// «А если поставить …» (10.15): прибыль, расходы и маржа сразу при нескольких ценах — тем же расчётом.
import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { pctText, rub } from '@/components/reports'
import { useAuth } from '@/contexts/AuthContext'
import { api, errorMessage } from '@/lib/api'
import { AD_WHY, MARGIN_WHY, fieldNumber, type Breakdown, type CalculateResponse, type Overrides } from '@/lib/pricing'
import { saveSettings, useSettings } from '@/lib/settings'

const EMPTY: Overrides = { ad_pct: '', target_margin_pct: '', logistics: '', storage: '', other: '', price: '' }
const LIMITS: Record<keyof Overrides, [number, number, string]> = {
  ad_pct: [0, 90, 'Реклама — от 0 до 90 %'],
  target_margin_pct: [0, 90, 'Целевая маржа — от 0 до 90 %'],
  logistics: [0, 1_000_000, 'Логистика — число не меньше 0'],
  storage: [0, 1_000_000, 'Хранение — число не меньше 0'],
  other: [0, 1_000_000, 'Прочие расходы — число не меньше 0'],
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
}: {
  label: string
  value: string
  placeholder: string
  onChange: (v: string) => void
  suffix: string
  hint?: string
  testid: string
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
          className="w-full rounded-lg border border-line bg-bg px-2 py-1.5 text-right text-sm text-fg outline-none placeholder:text-faint focus:border-accent"
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
  const seq = useRef(0)
  const { body, error: inputError } = overridesBody(ov)
  const key = JSON.stringify(body)

  useEffect(() => {
    setOv(EMPTY)
    setSaved(null)
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
  }, [productId, key, inputError, refreshKey, settings?.price_ad_pct, settings?.price_target_margin_pct])

  const set = (k: keyof Overrides) => (v: string) => {
    setSaved(null)
    setOv((o) => ({ ...o, [k]: v }))
  }

  const changedSettings =
    settings !== null &&
    ((body.ad_pct !== undefined && Number(body.ad_pct) !== Number(settings.price_ad_pct)) ||
      (body.target_margin_pct !== undefined && Number(body.target_margin_pct) !== Number(settings.price_target_margin_pct)))

  async function saveMine() {
    if (!user || !settings) return
    try {
      await saveSettings(user.id, {
        price_ad_pct: body.ad_pct ?? settings.price_ad_pct,
        price_target_margin_pct: body.target_margin_pct ?? settings.price_target_margin_pct,
      })
      setOv((o) => ({ ...o, ad_pct: '', target_margin_pct: '' }))
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
              Сохранить как мои настройки — для всех товаров
            </button>
          )}
          {saved && <p className="text-sm text-ok">{saved}</p>}

          <details className="rounded-lg border border-line px-3 py-2" data-testid="calc-more">
            <summary className="cursor-pointer text-sm text-muted hover:text-fg">Уточнить расчёт — только для этого расчёта</summary>
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
              <Field label="Хранение на 1 шт." value={ov.storage} placeholder="0" onChange={set('storage')} suffix="₽" hint="Ozon не делит хранение по товарам" testid="calc-storage" />
              <Field label="Прочее на 1 шт." value={ov.other} placeholder="0" onChange={set('other')} suffix="₽" hint="упаковка, маркировка, доставка до склада" testid="calc-other" />
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
          {line(`− Реклама ${num(i.ad_pct)} %`, b.ad, { hide: zero(b.ad) })}
          {line('− Логистика', b.logistics, { note: i.logistics_text })}
          {line('− Хранение', b.storage, { hide: zero(b.storage) })}
          {line('− Прочие расходы', b.other, { hide: zero(b.other) })}
          {line('− Себестоимость с НДС поставщика', b.cost, { note: i.sources.cost })}
          {line('− НДС к уплате', b.vat, { hide: zero(b.vat), note: 'с продаж, за вычетом входящего — если есть вычет' })}
          {line('− Налог с дохода', b.income_tax, { hide: zero(b.income_tax) })}
          {line('= Остаётся до налога на прибыль', b.profit, { strong: true, tone: Number(b.profit) < 0 ? 'text-bad' : 'text-ok' })}
          {Number(b.profit_tax) > 0 && line(`Налог на прибыль ${num(data.tax.profit_tax_pct)} % — после него`, b.net, { note: `−${rub(b.profit_tax)}` })}
        </tbody>
      </table>
      <p className="mt-1 text-xs text-faint">Маржа {pctText(b.margin)} — остаток в % от цены.</p>
    </div>
  )
}

/** Реклама и целевая маржа — настройки калькулятора цены («Настройки» и страница цен). */
export function PriceSettingsForm({ onSaved }: { onSaved?: () => void }) {
  const { user } = useAuth()
  const settings = useSettings()
  const [ad, setAd] = useState('')
  const [margin, setMargin] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings) return
    setAd(num(settings.price_ad_pct))
    setMargin(num(settings.price_target_margin_pct))
  }, [settings])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setError(null)
    setDone(false)
    const { body, error: bad } = overridesBody({ ...EMPTY, ad_pct: ad || '0', target_margin_pct: margin || '0' })
    if (bad) return setError(bad)
    try {
      await saveSettings(user.id, { price_ad_pct: body.ad_pct, price_target_margin_pct: body.target_margin_pct })
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
          <Field label="Реклама, % от цены" value={ad} placeholder="0" onChange={setAd} suffix="%" testid="settings-ad" />
          <p className="mt-1 text-xs text-faint">{AD_WHY}</p>
        </div>
        <div>
          <Field label="Целевая маржа" value={margin} placeholder="20" onChange={setMargin} suffix="%" testid="settings-margin" />
          <p className="mt-1 text-xs text-faint">{MARGIN_WHY}</p>
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
