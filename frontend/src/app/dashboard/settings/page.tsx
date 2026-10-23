'use client'

import { Monitor, Moon, Sun } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PriceSettingsForm } from '@/components/price-calc'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme } from '@/contexts/ThemeContext'
import { api, errorMessage } from '@/lib/api'
import { type SupplierPayment, type UserSettings, saveSettings, useSettings } from '@/lib/settings'
import { SUPPLIER_OPTIONS, SUPPLIER_WHY } from '@/lib/supplier'
import { LEAD_WHY, TARGET_WHY } from '@/lib/supply'
import type { ThemePreference } from '@/lib/theme'

const THEMES: { value: ThemePreference; label: string; hint: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { value: 'dark', label: 'Тёмная', hint: 'по умолчанию', Icon: Moon },
  { value: 'light', label: 'Светлая', hint: 'для яркого света', Icon: Sun },
  { value: 'system', label: 'Как в системе', hint: 'следует настройке устройства', Icon: Monitor },
]

function Section({ title, children, hint, id }: { title: string; hint?: string; id?: string; children: React.ReactNode }) {
  return (
    <section id={id} className="scroll-mt-4 rounded-xl border border-line bg-card p-5">
      <h2 className="text-lg font-semibold">{title}</h2>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

function ThemeSection() {
  const { preference, setPreference } = useTheme()
  const [error, setError] = useState<string | null>(null)

  async function choose(value: ThemePreference) {
    setError(null)
    try {
      await setPreference(value)
    } catch (err) {
      setError(errorMessage(err, 'Тема включена, но не сохранилась в аккаунте — попробуйте ещё раз'))
    }
  }

  return (
    <Section title="Внешний вид" hint="Тема сохраняется в аккаунте — одинаково на всех устройствах.">
      <FormError message={error} />
      <div role="radiogroup" aria-label="Тема" className="grid gap-3 sm:grid-cols-3">
        {THEMES.map(({ value, label, hint, Icon }) => {
          const active = preference === value
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={active}
              onClick={() => choose(value)}
              data-testid={`theme-${value}`}
              className={`flex items-center gap-3 rounded-xl border p-4 text-left ${
                active ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'
              }`}
            >
              <Icon className={`h-5 w-5 ${active ? 'text-accent' : 'text-muted'}`} />
              <span>
                <span className="block font-medium">{label}</span>
                <span className="block text-xs text-faint">{hint}</span>
              </span>
            </button>
          )
        })}
      </div>
    </Section>
  )
}

function TaxesSection() {
  const [title, setTitle] = useState<string | null | undefined>(undefined)
  useEffect(() => {
    api
      .get<{ current: { title: string } | null }>('/tax-profile')
      .then(({ data }) => setTitle(data.current?.title ?? null))
      .catch(() => setTitle(null))
  }, [])
  return (
    <Section title="Налоги и учёт" hint="Кто вы, система налогообложения, ставка и НДС — по ним считается налог в P&L.">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className={title === null ? 'text-warn' : ''} data-testid="taxes-summary">
          {title === undefined ? 'Загрузка…' : (title ?? 'Не заданы — налог не считается')}
        </span>
        <Link href="/dashboard/settings/taxes" className="ml-auto text-accent hover:underline">
          {title ? 'Изменить →' : 'Указать →'}
        </Link>
      </div>
    </Section>
  )
}

function SupplierSection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [error, setError] = useState<string | null>(null)
  const [deferred, setDeferred] = useState(false) // выбрали отсрочку — сначала спросить, сколько дней
  const [days, setDays] = useState('')

  useEffect(() => {
    if (settings?.supplier_deferral_days) setDays(String(settings.supplier_deferral_days))
  }, [settings?.supplier_deferral_days])

  async function save(value: SupplierPayment, deferralDays?: number) {
    if (!user) return
    setError(null)
    try {
      await saveSettings(user.id, { supplier_payment: value, ...(deferralDays ? { supplier_deferral_days: deferralDays } : {}) })
      setDeferred(false)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  function choose(value: SupplierPayment) {
    if (value === 'deferred' && !settings?.supplier_deferral_days) {
      setDeferred(true) // без числа дней не сохраняем
      return
    }
    setDeferred(false)
    save(value)
  }

  function saveDays() {
    const n = Number(days)
    if (!Number.isInteger(n) || n < 1 || n > 365) return setError('Отсрочка — целое число дней от 1 до 365')
    save('deferred', n)
  }

  const showDays = deferred || settings?.supplier_payment === 'deferred'
  return (
    <Section title="Откуда товар" hint={SUPPLIER_WHY}>
      <FormError message={error} />
      <div role="radiogroup" aria-label="Откуда товар" className="grid gap-3 sm:grid-cols-2">
        {SUPPLIER_OPTIONS.map(({ value, label, effect }) => {
          const active = deferred ? value === 'deferred' : settings?.supplier_payment === value
          return (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={active}
              disabled={!settings}
              onClick={() => choose(value)}
              data-testid={`supplier-${value}`}
              className={`rounded-xl border p-4 text-left disabled:opacity-60 ${
                active ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'
              }`}
            >
              <span className="block font-medium">{label}</span>
              <span className="mt-0.5 block text-xs text-faint">{effect}</span>
            </button>
          )
        })}
      </div>
      {showDays && (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm" data-testid="deferral">
          <label htmlFor="deferral-days" className="text-muted">
            Отсрочка оплаты, дней
          </label>
          <input
            id="deferral-days"
            inputMode="numeric"
            value={days}
            onChange={(e) => setDays(e.target.value.replace(/\D/g, ''))}
            onKeyDown={(e) => e.key === 'Enter' && saveDays()}
            className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
            data-testid="deferral-days"
          />
          <button type="button" onClick={saveDays} className="rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent" data-testid="deferral-save">
            Сохранить
          </button>
          <span className="text-xs text-faint">через сколько дней после закупки платите — по нему встанут платежи в календаре</span>
        </div>
      )}
    </Section>
  )
}

function PriceSection() {
  return (
    <Section title="Расчёт цены" hint="По ним считаются минимальная и целевая цена и маржа в акциях — на страницах «Цены», «Товары» и «Акции».">
      <PriceSettingsForm />
    </Section>
  )
}

const STOCK_FIELDS = [
  {
    key: 'stock_critical_days',
    label: '«Критично» — меньше',
    why: 'Товар вот-вот кончится: красным на складах, в «Заканчиваются» на главной и в фильтре «Критичный остаток».',
  },
  {
    key: 'stock_low_days',
    label: '«Мало» — меньше',
    why: 'Пора планировать поставку: жёлтым на складах, в «Заканчиваются» и в фильтре «Мало остатков» на странице товаров.',
  },
  {
    key: 'stock_excess_days',
    label: '«Избыток» — больше',
    why: 'Лежит дольше, чем продастся: такие товары попадают в «Кандидаты на вывоз» — за хранение платить дольше.',
  },
] as const

type StockKey = (typeof STOCK_FIELDS)[number]['key']

function StockSection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [values, setValues] = useState<Record<StockKey, string>>({ stock_critical_days: '', stock_low_days: '', stock_excess_days: '' })
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings) return
    setValues({
      stock_critical_days: String(settings.stock_critical_days),
      stock_low_days: String(settings.stock_low_days),
      stock_excess_days: String(settings.stock_excess_days),
    })
  }, [settings])

  async function save(next: Record<StockKey, string>) {
    if (!user) return
    setError(null)
    setDone(false)
    const days = Object.fromEntries(STOCK_FIELDS.map(({ key }) => [key, Number(next[key])])) as Record<StockKey, number>
    if (Object.values(days).some((d) => !Number.isInteger(d) || d < 1 || d > 365)) return setError('Пороги — целые числа дней от 1 до 365')
    if (!(days.stock_critical_days < days.stock_low_days && days.stock_low_days < days.stock_excess_days))
      return setError('Пороги идут по возрастанию: «Критично» меньше «Мало», «Мало» меньше «Избыток»')
    try {
      await saveSettings(user.id, days)
      setDone(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  const isDefault = values.stock_critical_days === '7' && values.stock_low_days === '14' && values.stock_excess_days === '60'
  return (
    <Section
      title="Остатки"
      hint="На сколько дней продаж хватит остатка — по этим порогам товар получает статус на складах, на главной и на странице товаров."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save(values)
        }}
        className="space-y-3"
        data-testid="stock-settings"
      >
        <FormError message={error} />
        <div className="grid gap-3 sm:grid-cols-3">
          {STOCK_FIELDS.map(({ key, label, why }) => (
            <div key={key}>
              <label htmlFor={key} className="block text-sm text-muted">
                {label}
              </label>
              <div className="mt-1 flex items-center gap-2">
                <input
                  id={key}
                  inputMode="numeric"
                  disabled={!settings}
                  value={values[key]}
                  onChange={(e) => {
                    setDone(false)
                    setValues((v) => ({ ...v, [key]: e.target.value.replace(/\D/g, '') }))
                  }}
                  className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent disabled:opacity-60"
                  data-testid={`settings-${key}`}
                />
                <span className="text-sm text-muted">дн. запаса</span>
              </div>
              <p className="mt-1 text-xs text-faint">{why}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={!settings}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="settings-stock-save"
          >
            Сохранить
          </button>
          {!isDefault && (
            <button
              type="button"
              onClick={() => {
                const next = { stock_critical_days: '7', stock_low_days: '14', stock_excess_days: '60' }
                setValues(next)
                save(next)
              }}
              className="text-sm text-accent hover:underline"
              data-testid="settings-stock-reset"
            >
              Вернуть 7 / 14 / 60
            </button>
          )}
          {done && <span className="text-sm text-ok">Сохранено — статусы пересчитаны</span>}
        </div>
        <p className="text-xs text-faint">
          Дни запаса — остаток товара на всех складах, делённый на продажи в день за последние 30 дней.
        </p>
      </form>
    </Section>
  )
}

function SupplySection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [target, setTarget] = useState('')
  const [lead, setLead] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings) return
    setTarget(String(settings.supply_target_days))
    setLead(String(settings.supply_lead_time_days))
  }, [settings])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setError(null)
    setDone(false)
    const t = Number(target)
    const l = Number(lead)
    if (!/^\d+$/.test(target) || t < 1 || t > 365) return setError('Запас после поставки — от 1 до 365 дней')
    if (!/^\d+$/.test(lead) || l > 180) return setError('Дней в пути — от 0 до 180')
    try {
      await saveSettings(user.id, { supply_target_days: t, supply_lead_time_days: l })
      setDone(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  const fields = [
    { id: 'supply_target_days', label: 'Запас после поставки', value: target, set: setTarget, why: TARGET_WHY },
    { id: 'supply_lead_time_days', label: 'Поставка в пути', value: lead, set: setLead, why: LEAD_WHY },
  ]
  return (
    <Section title="Поставки" hint="По ним «Поставки» считают, сколько везти в каждый кластер Ozon и что закупить на свой склад.">
      <form onSubmit={save} className="space-y-3" data-testid="supply-settings">
        <FormError message={error} />
        <div className="grid gap-3 sm:grid-cols-2">
          {fields.map((f) => (
            <div key={f.id}>
              <label htmlFor={f.id} className="block text-sm text-muted">
                {f.label}
              </label>
              <div className="mt-1 flex items-center gap-2">
                <input
                  id={f.id}
                  inputMode="numeric"
                  disabled={!settings}
                  value={f.value}
                  onChange={(e) => {
                    setDone(false)
                    f.set(e.target.value.replace(/\D/g, ''))
                  }}
                  className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent disabled:opacity-60"
                  data-testid={`settings-${f.id}`}
                />
                <span className="text-sm text-muted">дн.</span>
              </div>
              <p className="mt-1 text-xs text-faint">{f.why}</p>
            </div>
          ))}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={!settings}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="settings-supply-save"
          >
            Сохранить
          </button>
          {done && <span className="text-sm text-ok">Сохранено — поставки пересчитаны</span>}
        </div>
      </form>
    </Section>
  )
}

type AnomalyKey =
  | 'low_margin_pct'
  | 'anomaly_sales_drop_pct'
  | 'anomaly_sales_drop_red_pct'
  | 'anomaly_sales_growth_pct'
  | 'anomaly_price_pct'
  | 'anomaly_price_red_pct'
  | 'anomaly_commission_pp'
  | 'anomaly_payout_diff_pct'
  | 'anomaly_cash_gap_days'
  | 'anomaly_uncategorized_days'
  | 'anomaly_sync_stale_hours'
  | 'anomaly_data_gap_days'

type AnomalyField = { key: AnomalyKey; label: string; suffix: string; why: string; min: number; max: number; places?: number }

// Пороги аномалий (шаг 7.1, BUSINESS_LOGIC §7): у каждого — на что влияет (правило 15)
const ANOMALY_GROUPS: { title: string; why: string; fields: AnomalyField[] }[] = [
  {
    title: 'Маржа',
    why: 'Одна настройка для всего сервиса: «Акции», «Товары», «Цены» и «Прибыль по товарам» подсвечивают маржу ниже порога жёлтым.',
    fields: [
      {
        key: 'low_margin_pct',
        label: 'Низкая маржа — меньше',
        suffix: '%',
        why: 'У товара, который продаётся (есть остаток или были продажи за 30 дней), маржа ниже — красная аномалия.',
        min: 0,
        max: 90,
        places: 2,
      },
    ],
  },
  {
    title: 'Продажи',
    why: 'Последние 3 дня против 7 дней до них — по магазину и по каждому товару, с поправкой на выходные; дни без данных не считаются. У товаров с редкими продажами случайные колебания не считаются.',
    fields: [
      { key: 'anomaly_sales_drop_pct', label: 'Упали больше чем на', suffix: '%', why: 'Жёлтая — проверьте цену, остатки, акции и карточку товара.', min: 1, max: 99 },
      { key: 'anomaly_sales_drop_red_pct', label: 'Упали — красная, больше', suffix: '%', why: 'Красная — продажи почти встали.', min: 1, max: 99 },
      { key: 'anomaly_sales_growth_pct', label: 'Выросли больше чем на', suffix: '%', why: 'Резкий рост — повод проверить, не слишком ли низкая цена.', min: 10, max: 1000 },
    ],
  },
  {
    title: 'Цена и комиссия Ozon',
    why: 'Сравниваем с тем, что было 7 дней назад: изменения в кабинете Ozon, самим Ozon и через сервис («Цены»).',
    fields: [
      { key: 'anomaly_price_pct', label: 'Цена изменилась больше чем на', suffix: '%', why: 'Жёлтая — посмотрите, кто и зачем поменял цену.', min: 1, max: 99 },
      { key: 'anomaly_price_red_pct', label: 'Цена — красная, больше', suffix: '%', why: 'Красная — резкое изменение, похоже на ошибку.', min: 1, max: 99 },
      { key: 'anomaly_commission_pp', label: 'Комиссия изменилась больше чем на', suffix: 'п. п.', why: 'Вместе с комиссией меняются маржа и минимальная цена.', min: 0.1, max: 50, places: 1 },
    ],
  },
  {
    title: 'Деньги',
    why: 'Выплата Ozon, которая не пришла по графику, — всегда красная, банк для этого не нужен. Сверка с банком, кассовый разрыв и операции без статьи — только когда включено «Планирование».',
    fields: [
      { key: 'anomaly_payout_diff_pct', label: 'Выплата Ozon разошлась с прогнозом больше чем на', suffix: '%', why: 'Прогноз — начисления недели продаж по графику выплат: разница — повод посмотреть удержания.', min: 1, max: 100 },
      { key: 'anomaly_cash_gap_days', label: 'Кассовый разрыв — смотреть вперёд на', suffix: 'дн.', why: 'Прогноз остатка ниже нуля — красная, ниже вашего порога остатка — жёлтая.', min: 1, max: 90 },
      { key: 'anomaly_uncategorized_days', label: 'Операция без статьи дольше', suffix: 'дн.', why: 'Без статьи ДДС по статьям и прогноз неточны — пора разметить.', min: 1, max: 90 },
    ],
  },
  {
    title: 'Данные',
    why: 'Проблемы с данными попадают в «Требует внимания» на главной: без свежих и полных данных цифры в разделах неверны. Ключ или токен банка не принят — сразу, без порогов.',
    fields: [
      { key: 'anomaly_sync_stale_hours', label: 'Данные не обновлялись дольше', suffix: 'ч', why: 'Ozon или банк не отвечает, загрузка не идёт: разовый сбой между плановыми загрузками не тревожит, а долгий — да.', min: 1, max: 720 },
      { key: 'anomaly_data_gap_days', label: 'Дней без начислений между загруженными — от', suffix: 'дн.', why: 'Пропуск в начислениях за последние 30 дней: выручка, прибыль и P&L за эти дни неполные. Хвост после последнего загруженного дня не считаем.', min: 1, max: 30 },
    ],
  },
]
const ANOMALY_FIELDS = ANOMALY_GROUPS.flatMap((g) => g.fields)
const ANOMALY_DEFAULTS: Record<AnomalyKey, string> = {
  low_margin_pct: '10',
  anomaly_sales_drop_pct: '40',
  anomaly_sales_drop_red_pct: '70',
  anomaly_sales_growth_pct: '200',
  anomaly_price_pct: '10',
  anomaly_price_red_pct: '30',
  anomaly_commission_pp: '2',
  anomaly_payout_diff_pct: '10',
  anomaly_cash_gap_days: '14',
  anomaly_uncategorized_days: '7',
  anomaly_sync_stale_hours: '24',
  anomaly_data_gap_days: '1',
}
const decimalText = (v: string | number) => String(Number(v)).replace('.', ',')

function AnomalySection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [values, setValues] = useState<Record<AnomalyKey, string>>(ANOMALY_DEFAULTS)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings) return
    setValues(Object.fromEntries(ANOMALY_FIELDS.map(({ key }) => [key, decimalText(settings[key])])) as Record<AnomalyKey, string>)
  }, [settings])

  async function save(next: Record<AnomalyKey, string>) {
    if (!user) return
    setError(null)
    setDone(false)
    const parsed: Partial<Record<AnomalyKey, number | string>> = {}
    for (const f of ANOMALY_FIELDS) {
      const raw = next[f.key].trim().replace(',', '.')
      const n = Number(raw)
      const places = f.places ?? 0
      const ok = raw !== '' && Number.isFinite(n) && n >= f.min && n <= f.max && (raw.split('.')[1]?.length ?? 0) <= places
      if (!ok) return setError(`«${f.label}» — ${places ? 'число' : 'целое число'} от ${decimalText(f.min)} до ${decimalText(f.max)}`)
      parsed[f.key] = places ? raw : n
    }
    if (Number(parsed.anomaly_sales_drop_pct) >= Number(parsed.anomaly_sales_drop_red_pct))
      return setError('Падение продаж: жёлтый порог меньше красного')
    if (Number(parsed.anomaly_price_pct) >= Number(parsed.anomaly_price_red_pct)) return setError('Изменение цены: жёлтый порог меньше красного')
    try {
      await saveSettings(user.id, parsed as Partial<UserSettings>)
      setDone(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  return (
    <Section
      id="anomalies"
      title="Аномалии и порог маржи"
      hint="Раз в час сервис проверяет продажи, цены, остатки, акции и выплаты и сообщает, если что-то пошло не так. Пороги — когда считать это проблемой."
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          save(values)
        }}
        className="space-y-5"
        data-testid="anomaly-settings"
      >
        <FormError message={error} />
        {ANOMALY_GROUPS.map((g) => (
          <fieldset key={g.title} className="space-y-2">
            <legend className="font-medium">{g.title}</legend>
            <p className="text-xs text-faint">{g.why}</p>
            <div className="grid gap-3 sm:grid-cols-3">
              {g.fields.map((f) => (
                <div key={f.key}>
                  <label htmlFor={f.key} className="block text-sm text-muted">
                    {f.label}
                  </label>
                  <div className="mt-1 flex items-center gap-2">
                    <input
                      id={f.key}
                      inputMode={f.places ? 'decimal' : 'numeric'}
                      disabled={!settings}
                      value={values[f.key]}
                      onChange={(e) => {
                        setDone(false)
                        const v = e.target.value.replace(f.places ? /[^\d.,]/g : /\D/g, '')
                        setValues((old) => ({ ...old, [f.key]: v }))
                      }}
                      className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent disabled:opacity-60"
                      data-testid={`settings-${f.key}`}
                    />
                    <span className="text-sm text-muted">{f.suffix}</span>
                  </div>
                  <p className="mt-1 text-xs text-faint">{f.why}</p>
                </div>
              ))}
            </div>
          </fieldset>
        ))}
        <p className="text-xs text-faint">
          Дни запаса — пороги «Остатков» выше: меньше «Мало» — жёлтая, меньше «Критично» — красная. Товар, который Ozon сам добавил в акцию, —
          сразу жёлтая; маржа в акции ниже нуля — красная.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={!settings}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="settings-anomaly-save"
          >
            Сохранить
          </button>
          {settings && !settings.anomaly_defaults && (
            <button
              type="button"
              onClick={() => {
                setValues(ANOMALY_DEFAULTS)
                save(ANOMALY_DEFAULTS)
              }}
              className="text-sm text-accent hover:underline"
              data-testid="settings-anomaly-reset"
            >
              Вернуть по умолчанию
            </button>
          )}
          {done && <span className="text-sm text-ok">Сохранено — маржа на страницах сразу, аномалии — при следующей проверке</span>}
        </div>
      </form>
    </Section>
  )
}

type PlanningState = { enabled: boolean; setup_done: boolean }

function PlanningSection() {
  const [state, setState] = useState<PlanningState | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api
      .get<PlanningState>('/planning/settings')
      .then(({ data }) => setState(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  async function toggle(enabled: boolean) {
    setError(null)
    try {
      const { data } = await api.put<PlanningState>('/planning/settings', { enabled })
      setState(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  return (
    <Section
      title="Планирование"
      hint="Необязательный раздел: свои счета, расходы и доходы, прогноз денег. «Финансы Ozon» работают и без него."
    >
      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="planning-setting">
        <span>{state === null ? 'Загрузка…' : state.enabled ? 'Включено' : state.setup_done ? 'Выключено — данные сохранены' : 'Не включалось'}</span>
        {state?.enabled && (
          <button type="button" onClick={() => toggle(false)} className="ml-auto rounded-lg border border-line px-3 py-1.5 hover:border-bad hover:text-bad" data-testid="planning-off">
            Выключить
          </button>
        )}
        {state && !state.enabled && state.setup_done && (
          <button type="button" onClick={() => toggle(true)} className="ml-auto rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent" data-testid="planning-on">
            Включить снова
          </button>
        )}
        {state && !state.enabled && !state.setup_done && (
          <Link href="/dashboard/planning" className="ml-auto text-accent hover:underline">
            Начать →
          </Link>
        )}
      </div>
      {state?.enabled && (
        <p className="mt-2 text-xs text-faint">Выключите — раздел перестанет показываться, счета и операции останутся; включить можно в любой момент.</p>
      )}
    </Section>
  )
}

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Настройки</h1>
      <ThemeSection />
      <TaxesSection />
      <SupplierSection />
      <PriceSection />
      <StockSection />
      <SupplySection />
      <AnomalySection />
      <PlanningSection />
    </div>
  )
}
