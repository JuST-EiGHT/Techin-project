'use client'

import { Monitor, Moon, Sun } from 'lucide-react'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { useTheme } from '@/contexts/ThemeContext'
import { api, errorMessage } from '@/lib/api'
import type { ThemePreference } from '@/lib/theme'

type Settings = {
  theme: ThemePreference
  vat_multiplier: string
  tax_base: 'profit' | 'revenue'
  tax_rate: string // доля: 0.2000
}

const THEMES: { value: ThemePreference; label: string; hint: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { value: 'dark', label: 'Тёмная', hint: 'по умолчанию', Icon: Moon },
  { value: 'light', label: 'Светлая', hint: 'для яркого света', Icon: Sun },
  { value: 'system', label: 'Как в системе', hint: 'следует настройке устройства', Icon: Monitor },
]

/** «1,22» → «1.22»; пусто или не число → null. */
function toNumber(text: string): number | null {
  const n = Number(text.replace(/\s/g, '').replace(',', '.'))
  return text.trim() && Number.isFinite(n) ? n : null
}

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

function CalculationSection() {
  const [loaded, setLoaded] = useState<Settings | null>(null)
  const [vat, setVat] = useState('')
  const [taxBase, setTaxBase] = useState<Settings['tax_base']>('profit')
  const [taxPct, setTaxPct] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .get<Settings>('/settings')
      .then(({ data }) => {
        setLoaded(data)
        setVat(String(Number(data.vat_multiplier)))
        setTaxBase(data.tax_base)
        setTaxPct(String(Math.round(Number(data.tax_rate) * 10000) / 100))
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    const vatValue = toNumber(vat)
    const pct = toNumber(taxPct)
    if (vatValue === null || vatValue < 1 || vatValue > 2) return setError('Коэффициент НДС: число от 1 до 2, например 1,22')
    if (pct === null || pct < 0 || pct >= 100) return setError('Ставка налога: от 0 до 100 %, например 20')
    setBusy(true)
    try {
      const { data } = await api.patch<Settings>('/settings', {
        vat_multiplier: vatValue.toFixed(3),
        tax_base: taxBase,
        tax_rate: (pct / 100).toFixed(4),
      })
      setLoaded(data)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section
      title="Расчёты по умолчанию"
      hint="С этими значениями считаются новые отчёты. У каждого загруженного отчёта параметры свои — их можно поменять в самом отчёте."
    >
      <FormError message={error} />
      {!loaded && !error && <p className="text-sm text-muted">Загрузка…</p>}
      {loaded && (
        <form onSubmit={save} className="space-y-5" noValidate>
          <label className="block max-w-xs">
            <span className="mb-1 block text-sm text-muted">Коэффициент НДС к себестоимости</span>
            <input
              id="vat_multiplier"
              inputMode="decimal"
              value={vat}
              onChange={(e) => setVat(e.target.value)}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent"
            />
            <span className="mt-1 block text-xs text-faint">Себестоимость без НДС умножается на него: 1,22 — НДС 22 %, 1 — без НДС.</span>
          </label>

          <fieldset>
            <legend className="mb-2 text-sm text-muted">Налог считается</legend>
            <div className="flex flex-wrap gap-3">
              {[
                { value: 'profit' as const, label: 'От прибыли', hint: '(сумма от Ozon − себестоимость) × ставка' },
                { value: 'revenue' as const, label: 'От дохода', hint: 'выручка × ставка' },
              ].map((o) => (
                <label
                  key={o.value}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg border px-3 py-2 ${
                    taxBase === o.value ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'
                  }`}
                >
                  <input
                    type="radio"
                    name="tax_base"
                    value={o.value}
                    checked={taxBase === o.value}
                    onChange={() => setTaxBase(o.value)}
                    className="mt-1 accent-[rgb(var(--accent))]"
                  />
                  <span>
                    <span className="block text-sm">{o.label}</span>
                    <span className="block text-xs text-faint">{o.hint}</span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <label className="block max-w-xs">
            <span className="mb-1 block text-sm text-muted">Ставка налога, %</span>
            <input
              id="tax_rate"
              inputMode="decimal"
              value={taxPct}
              onChange={(e) => setTaxPct(e.target.value)}
              className="w-full rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-accent"
            />
          </label>

          <div className="flex items-center gap-4">
            <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
              {busy ? 'Сохраняем…' : 'Сохранить'}
            </button>
            {saved && <span className="text-sm text-ok">Сохранено</span>}
          </div>
        </form>
      )}
    </Section>
  )
}

export default function SettingsPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Основные настройки</h1>
      <ThemeSection />
      <CalculationSection />
    </div>
  )
}
