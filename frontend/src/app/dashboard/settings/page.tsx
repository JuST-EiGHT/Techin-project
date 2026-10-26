'use client'

import { Monitor, Moon, Sun } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { PriceSettingsForm } from '@/components/price-calc'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme } from '@/contexts/ThemeContext'
import { api, errorMessage } from '@/lib/api'
import { type SupplierPayment, saveSettings, useSettings } from '@/lib/settings'
import { SUPPLIER_OPTIONS, SUPPLIER_WHY } from '@/lib/supplier'
import { LEAD_WHY, SAFETY_WHY, TARGET_WHY } from '@/lib/supply'
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

/** Группа настроек по карте сервиса (шаг 10.26): заголовок, якорь для навигации, внутри — разделы. */
function Group({ id, title, hint, children }: { id: string; title: string; hint: string; children: React.ReactNode }) {
  return (
    <div id={id} className="scroll-mt-4 space-y-4" data-testid={`group-${id}`}>
      <div>
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="mt-0.5 text-sm text-muted">{hint}</p>
      </div>
      {children}
    </div>
  )
}

const GROUPS = [
  { id: 'interface', label: 'Интерфейс' },
  { id: 'stock', label: 'Запасы' },
  { id: 'supply', label: 'Поставки' },
  { id: 'profit', label: 'Прибыльность' },
  { id: 'prices', label: 'Цены' },
  { id: 'planning', label: 'Планирование' },
  { id: 'analytics', label: 'Аналитика' },
  { id: 'accounting', label: 'Налоги и учёт' },
  { id: 'anomalies', label: 'Пороги уведомлений' },
]

function GroupNav() {
  return (
    <nav aria-label="Группы настроек" className="flex flex-wrap gap-2" data-testid="settings-nav">
      {GROUPS.map((g) => (
        <a key={g.id} href={`#${g.id}`} className="rounded-full border border-line px-3 py-1 text-sm text-muted hover:border-accent hover:text-accent">
          {g.label}
        </a>
      ))}
    </nav>
  )
}

type NumberField = {
  key: NumberKey
  label: string
  unit: string
  why: string
  min: number
  max: number
  decimals?: boolean // дробное (₽, %); иначе — целое
}
type NumberKey = 'supply_target_days' | 'supply_lead_time_days' | 'supply_safety_days' | 'supply_demand_days' | 'low_margin_pct' | 'min_profit_rub' | 'recommendation_hide_days'

/** Поля-числа одной формой: проверка диапазона по каждому, одно «Сохранить». */
function NumberForm({ fields, testid, saved, extra }: { fields: NumberField[]; testid: string; saved: string; extra?: React.ReactNode }) {
  const { user } = useAuth()
  const settings = useSettings()
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!settings) return
    setValues(Object.fromEntries(fields.map((f) => [f.key, String(Number(settings[f.key] ?? 0)).replace('.', ',')])))
    // fields — постоянный список раздела
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settings])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setError(null)
    setDone(false)
    const body: Record<string, number | string> = {}
    for (const f of fields) {
      const text = (values[f.key] ?? '').replace(/\s/g, '').replace(',', '.')
      const n = Number(text)
      const ok = text !== '' && Number.isFinite(n) && (f.decimals ? /^\d+(\.\d{1,2})?$/.test(text) : /^\d+$/.test(text))
      if (!ok || n < f.min || n > f.max)
        return setError(`${f.label} — ${f.decimals ? 'число' : 'целое число'} от ${f.min} до ${f.max.toLocaleString('ru-RU')}${f.unit === '%' ? ' %' : f.unit === '₽' ? ' ₽' : ''}`)
      body[f.key] = f.decimals ? n.toFixed(2) : n
    }
    try {
      await saveSettings(user.id, body)
      setDone(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  return (
    <form onSubmit={save} className="space-y-3" data-testid={testid}>
      <FormError message={error} />
      <div className="grid gap-4 sm:grid-cols-2">
        {fields.map((f) => (
          <div key={f.key}>
            <label htmlFor={f.key} className="block text-sm text-muted">
              {f.label}
            </label>
            <div className="mt-1 flex items-center gap-2">
              <input
                id={f.key}
                inputMode={f.decimals ? 'decimal' : 'numeric'}
                disabled={!settings}
                value={values[f.key] ?? ''}
                onChange={(e) => {
                  setDone(false)
                  setValues((v) => ({ ...v, [f.key]: f.decimals ? e.target.value.replace(/[^\d.,\s]/g, '') : e.target.value.replace(/\D/g, '') }))
                }}
                className="w-24 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent disabled:opacity-60"
                data-testid={`settings-${f.key}`}
              />
              <span className="text-sm text-muted">{f.unit}</span>
            </div>
            <p className="mt-1 text-xs text-faint">{f.why}</p>
          </div>
        ))}
      </div>
      {extra}
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={!settings}
          className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
          data-testid={`${testid}-save`}
        >
          Сохранить
        </button>
        {done && <span className="text-sm text-ok">{saved}</span>}
      </div>
    </form>
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
    <Section title="Тема" hint="Тема сохраняется в аккаунте — одинаково на всех устройствах.">
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
    <>
      <Section title="Расчёт цены" hint="По ним считаются минимальная и целевая цена и маржа в акциях — на страницах «Цены», «Товары» и «Акции».">
        <PriceSettingsForm />
        <p className="mt-3 text-xs text-faint">
          Минимальная цена — та, при которой с продажи остаётся «Минимальная допустимая прибыль» (группа «Прибыльность»); при 0 — цена без убытка.
        </p>
      </Section>
      <Section title="Ограничения автоизменения цен" hint="Насколько сервис сможет сам менять цены по правилам: шаг, частота, нижняя граница.">
        <p className="text-sm text-muted" data-testid="auto-price-soon">
          Появятся вместе с автоматизацией цен (шаг 9.1). Сейчас цена меняется только вами: предпросмотр → подтверждение, не больше 20 % за раз и
          не ниже минимальной без явного согласия.
        </p>
      </Section>
    </>
  )
}

const STOCK_FIELDS = [
  {
    key: 'stock_critical_days',
    label: '«Критично» — меньше',
    why: 'Товар вот-вот кончится: красным на складах, в «Требует внимания» на главной и в фильтре «Критичный остаток».',
  },
  {
    key: 'stock_low_days',
    label: '«Мало» — меньше',
    why: 'Пора планировать поставку: жёлтым на складах, в «Требует внимания» и в фильтре «Мало остатков» на странице товаров.',
  },
  {
    key: 'stock_excess_days',
    label: '«Избыток» — больше',
    why: 'Лежит дольше, чем продастся: такие товары попадают в «На вывоз» — за хранение платить дольше.',
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
      title="Статусы остатка"
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

const TARGET_FIELD: NumberField = {
  key: 'supply_target_days',
  label: 'Целевой запас — после поставки',
  unit: 'дн.',
  why: TARGET_WHY,
  min: 1,
  max: 365,
}

const SUPPLY_FIELDS: NumberField[] = [
  { key: 'supply_lead_time_days', label: 'Срок поставки — в пути', unit: 'дн.', why: LEAD_WHY, min: 0, max: 180 },
  { key: 'supply_safety_days', label: 'Страховой запас', unit: 'дн.', why: SAFETY_WHY, min: 0, max: 180 },
  {
    key: 'supply_demand_days',
    label: 'Период планирования',
    unit: 'дн.',
    why: 'За сколько последних дней брать заказы, чтобы оценить продажи в день. Короче — быстрее заметит рост и спад, длиннее — спокойнее к случайным всплескам. Обычно 30.',
    min: 7,
    max: 90,
  },
]

function SupplySection() {
  return (
    <Section title="Параметры поставки" hint="По ним «Поставки» считают, сколько везти в каждый кластер Ozon и что закупить на свой склад.">
      <NumberForm fields={SUPPLY_FIELDS} testid="supply-settings" saved="Сохранено — поставки пересчитаны" />
    </Section>
  )
}

const PROFIT_FIELDS: NumberField[] = [
  {
    key: 'low_margin_pct',
    label: 'Порог низкой маржи',
    unit: '%',
    why: 'Маржа ниже — жёлтым в товарах, ценах, акциях и «Прибыли по товарам», и уведомление «маржа ниже порога». Тот же порог — в «Уведомлениях».',
    min: 0,
    max: 90,
    decimals: true,
  },
  {
    key: 'min_profit_rub',
    label: 'Минимальная допустимая прибыль',
    unit: '₽ с продажи',
    why: 'Сколько должно оставаться с каждой проданной штуки после всех расходов и налога. Калькулятор цены покажет цену, при которой столько остаётся, и предупредит, если текущая ниже. 0 — достаточно не уходить в минус.',
    min: 0,
    max: 1_000_000,
    decimals: true,
  },
]

function ProfitSection() {
  return (
    <Section title="Маржа и прибыль с продажи" hint="Что считать хорошей и плохой продажей — по этим границам товары подсвечиваются и проверяется цена.">
      <NumberForm fields={PROFIT_FIELDS} testid="profit-settings" saved="Сохранено — маржа и цены пересчитаны" />
    </Section>
  )
}

/** Пороги аномалий и «низкой маржи» (шаг 7.1) с шага 10.25 — в «Аккаунт → Уведомления», у своего раздела; значения те
 * же. Здесь — ссылка туда (якорь #anomalies — для старых ссылок). */
function AnomalySection() {
  return (
    <Section id="anomalies" title="Пороги предупреждений и маржи">
      <p className="text-sm text-muted" data-testid="anomaly-moved">
        Когда считать проблемой падение продаж, изменение цены, низкую маржу, расхождение выплаты или долгую синхронизацию — теперь в «Уведомлениях», рядом с
        тем, куда и как часто о них сообщать. Значения те же, что были здесь.
      </p>
      <Link href="/dashboard/settings/notifications#thresholds" className="mt-2 inline-block text-sm text-accent hover:underline" data-testid="anomaly-moved-link">
        Пороги в «Уведомлениях» →
      </Link>
    </Section>
  )
}

type PlanningState = { enabled: boolean; setup_done: boolean }

function RecommendationSection() {
  return (
    <Section
      title="Рекомендации"
      hint="«Рекомендуемые действия» на главной: что сделать с ценой, акцией, поставкой, себестоимостью и лишним запасом."
    >
      <NumberForm
        fields={[
          {
            key: 'recommendation_hide_days',
            label: '«Скрыть» — на',
            unit: 'дн.',
            why: 'Скрытый совет не показывается столько дней. Если за это время ничего не изменилось — он вернётся; сделали — исчезнет сам.',
            min: 1,
            max: 90,
          },
        ]}
        testid="recommendation-settings"
        saved="Сохранено"
      />
    </Section>
  )
}

function AbcSection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [a, setA] = useState('')
  const [b, setB] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (settings) {
      setA(String(settings.abc_a_pct))
      setB(String(settings.abc_b_pct))
    }
  }, [settings])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!user) return
    setError(null)
    setDone(false)
    const na = Number(a)
    const nb = Number(b)
    if (!/^\d+$/.test(a) || na < 10 || na > 98) return setError('Граница A — от 10 до 98 %')
    if (!/^\d+$/.test(b) || nb < 11 || nb > 99) return setError('Граница B — от 11 до 99 %')
    if (na >= nb) return setError('Граница A должна быть меньше границы B: например, 80 и 95 %')
    try {
      await saveSettings(user.id, { abc_a_pct: na, abc_b_pct: nb })
      setDone(true)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  const field = (id: string, label: string, value: string, set: (v: string) => void, why: string) => (
    <div>
      <label htmlFor={id} className="block text-sm text-muted">
        {label}
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={id}
          inputMode="numeric"
          disabled={!settings}
          value={value}
          onChange={(e) => {
            setDone(false)
            set(e.target.value.replace(/\D/g, ''))
          }}
          className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent disabled:opacity-60"
          data-testid={`settings-${id}`}
        />
        <span className="text-sm text-muted">%</span>
      </div>
      <p className="mt-1 text-xs text-faint">{why}</p>
    </div>
  )

  return (
    <Section
      id="abc"
      title="ABC-анализ"
      hint="Границы групп в «Аналитике → ABC-анализ»: товары по убыванию выручки, прибыли или продаж, пока их накопленная доля не дойдёт до границы."
    >
      <form onSubmit={save} className="space-y-3" data-testid="abc-settings">
        <FormError message={error} />
        <div className="grid gap-4 sm:grid-cols-2">
          {field('abc_a_pct', 'Группа A — первые', a, setA, 'Главные товары: дают эту долю выручки (прибыли, продаж). Обычно 80 %.')}
          {field('abc_b_pct', 'Группа B — до', b, setB, 'Средние — от границы A до этой. Остальные — C. Обычно 95 %.')}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            disabled={!settings}
            className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            data-testid="settings-abc-save"
          >
            Сохранить
          </button>
          {done && <span className="text-sm text-ok">Сохранено</span>}
        </div>
      </form>
    </Section>
  )
}

type PlanningParams = PlanningState & { horizon_days: number; threshold: string | null }

function PlanningSection() {
  const [state, setState] = useState<PlanningParams | null>(null)
  const [horizon, setHorizon] = useState('')
  const [threshold, setThreshold] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  function apply(data: PlanningParams) {
    setState(data)
    setHorizon(String(data.horizon_days))
    setThreshold(data.threshold === null ? '' : String(Number(data.threshold)))
  }

  useEffect(() => {
    api
      .get<PlanningParams>('/planning/settings')
      .then(({ data }) => apply(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  async function put(body: Record<string, unknown>) {
    setError(null)
    setDone(false)
    try {
      const { data } = await api.put<PlanningParams>('/planning/settings', body)
      apply(data)
      return true
    } catch (err) {
      setError(errorMessage(err))
      return false
    }
  }

  async function saveParams(e: React.FormEvent) {
    e.preventDefault()
    const h = Number(horizon)
    if (!/^\d+$/.test(horizon) || h < 14 || h > 180) return setError('Горизонт прогноза — от 14 до 180 дней')
    const t = threshold.replace(/\s/g, '').replace(',', '.')
    if (t && !/^-?\d+(\.\d{1,2})?$/.test(t)) return setError('Порог остатка — числом, например 50 000; пусто — без порога')
    if (await put({ horizon_days: h, threshold: t || null })) setDone(true)
  }

  return (
    <Section
      title="Планирование денег"
      hint="Необязательный раздел: свои счета, расходы и доходы, прогноз денег. «Финансы Ozon» работают и без него."
    >
      <FormError message={error} />
      <div className="flex flex-wrap items-center gap-3 text-sm" data-testid="planning-setting">
        <span>{state === null ? 'Загрузка…' : state.enabled ? 'Включено' : state.setup_done ? 'Выключено — данные сохранены' : 'Не включалось'}</span>
        {state?.enabled && (
          <button type="button" onClick={() => put({ enabled: false })} className="ml-auto rounded-lg border border-line px-3 py-1.5 hover:border-bad hover:text-bad" data-testid="planning-off">
            Выключить
          </button>
        )}
        {state && !state.enabled && state.setup_done && (
          <button type="button" onClick={() => put({ enabled: true })} className="ml-auto rounded-lg border border-line px-3 py-1.5 hover:border-accent hover:text-accent" data-testid="planning-on">
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
        <>
          <p className="mt-2 text-xs text-faint">Выключите — раздел перестанет показываться, счета и операции останутся; включить можно в любой момент.</p>
          <form onSubmit={saveParams} className="mt-4 space-y-3 border-t border-line pt-4" data-testid="planning-params">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <label htmlFor="horizon_days" className="block text-sm text-muted">
                  Горизонт прогноза
                </label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    id="horizon_days"
                    inputMode="numeric"
                    value={horizon}
                    onChange={(e) => {
                      setDone(false)
                      setHorizon(e.target.value.replace(/\D/g, ''))
                    }}
                    className="w-24 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
                    data-testid="settings-horizon_days"
                  />
                  <span className="text-sm text-muted">дн.</span>
                </div>
                <p className="mt-1 text-xs text-faint">На сколько дней вперёд календарь, обзор и ДДС считают прогноз остатка и ищут кассовый разрыв.</p>
              </div>
              <div>
                <label htmlFor="threshold" className="block text-sm text-muted">
                  Порог остатка денег
                </label>
                <div className="mt-1 flex items-center gap-2">
                  <input
                    id="threshold"
                    inputMode="decimal"
                    value={threshold}
                    placeholder="без порога"
                    onChange={(e) => {
                      setDone(false)
                      setThreshold(e.target.value.replace(/[^\d.,\s-]/g, ''))
                    }}
                    className="w-32 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
                    data-testid="settings-threshold"
                  />
                  <span className="text-sm text-muted">₽</span>
                </div>
                <p className="mt-1 text-xs text-faint">
                  Ниже этой суммы прогноз подсвечивает дни и предупреждает заранее — «подушка», которую не хочется тратить. Пусто — только разрыв ниже нуля.
                </p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90" data-testid="planning-params-save">
                Сохранить
              </button>
              {done && <span className="text-sm text-ok">Сохранено — прогноз пересчитан</span>}
            </div>
          </form>
          <ul className="mt-4 space-y-1 border-t border-line pt-4 text-sm" data-testid="planning-links">
            <li>
              Начальные остатки — у каждого счёта:{' '}
              <Link href="/dashboard/operations/accounts" className="text-accent hover:underline">
                Счета и выписки →
              </Link>
            </li>
            <li>
              Что попадает в ДДС, P&L и бюджеты — у статей:{' '}
              <Link href="/dashboard/operations/categories" className="text-accent hover:underline">
                Статьи →
              </Link>
            </li>
            <li>
              Плановые платежи от выплат Ozon, поставщика и налогов — в календаре:{' '}
              <Link href="/dashboard/planning/calendar" className="text-accent hover:underline">
                Платёжный календарь →
              </Link>
            </li>
            <li>
              Сценарии прогноза (оптимистичный, пессимистичный):{' '}
              <Link href="/dashboard/operations/budgets" className="text-accent hover:underline">
                Бюджеты и сценарии →
              </Link>
            </li>
          </ul>
        </>
      )}
    </Section>
  )
}

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-8">
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold">Настройки</h1>
        <GroupNav />
      </div>
      <Group id="interface" title="Интерфейс" hint="Как выглядит сервис.">
        <ThemeSection />
      </Group>
      <Group id="stock" title="Запасы" hint="Сколько дней продаж должно хватать товара — от этого статусы остатков, «На вывоз» и объём поставок.">
        <StockSection />
        <Section title="Целевой запас" hint="До какого запаса пополнять товар поставкой.">
          <NumberForm fields={[TARGET_FIELD]} testid="target-settings" saved="Сохранено — поставки пересчитаны" />
        </Section>
      </Group>
      <Group id="supply" title="Поставки" hint="Срок, страховой запас и за какой период смотреть продажи.">
        <SupplySection />
      </Group>
      <Group id="profit" title="Прибыльность" hint="Порог низкой маржи и минимальная прибыль, ниже которой продавать не стоит.">
        <ProfitSection />
      </Group>
      <Group id="prices" title="Цены" hint="Параметры калькулятора минимальной и целевой цены.">
        <PriceSection />
      </Group>
      <Group id="planning" title="Планирование" hint="Прогноз денег, кассовый разрыв, начальные остатки и ДДС.">
        <PlanningSection />
      </Group>
      <Group id="analytics" title="Аналитика" hint="Советы на главной и группы ABC-анализа.">
        <RecommendationSection />
        <AbcSection />
      </Group>
      <Group id="accounting" title="Налоги и учёт" hint="Налог в P&L и как учитывать закупку товара.">
        <TaxesSection />
        <SupplierSection />
      </Group>
      <AnomalySection />
    </div>
  )
}
