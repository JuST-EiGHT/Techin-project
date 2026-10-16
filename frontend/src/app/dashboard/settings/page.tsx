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
import type { ThemePreference } from '@/lib/theme'

const THEMES: { value: ThemePreference; label: string; hint: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { value: 'dark', label: 'Тёмная', hint: 'по умолчанию', Icon: Moon },
  { value: 'light', label: 'Светлая', hint: 'для яркого света', Icon: Sun },
  { value: 'system', label: 'Как в системе', hint: 'следует настройке устройства', Icon: Monitor },
]

function Section({ title, children, hint }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-card p-5">
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
    <Section title="Налоги и НДС" hint="Кто вы, система налогообложения, ставка и НДС — по ним считается налог в P&L.">
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
      <h1 className="text-2xl font-semibold">Основные настройки</h1>
      <ThemeSection />
      <TaxesSection />
      <SupplierSection />
      <PriceSection />
      <StockSection />
      <PlanningSection />
    </div>
  )
}
