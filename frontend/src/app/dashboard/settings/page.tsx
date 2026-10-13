'use client'

import { Monitor, Moon, Sun } from 'lucide-react'
import Link from 'next/link'
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { useAuth } from '@/contexts/AuthContext'
import { useTheme } from '@/contexts/ThemeContext'
import { api, errorMessage } from '@/lib/api'
import { type SupplierPayment, saveSettings, useSettings } from '@/lib/settings'
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

const SUPPLIER: { value: SupplierPayment; label: string; hint: string }[] = [
  { value: 'upfront', label: 'При закупке', hint: 'в P&L — «Себестоимость с НДС»' },
  {
    value: 'after_sale',
    label: 'После продажи товара',
    hint: 'поставщик ждёт, пока товар продастся, — в P&L «Долг поставщику»',
  },
]

function SupplierSection() {
  const { user } = useAuth()
  const settings = useSettings()
  const [error, setError] = useState<string | null>(null)

  async function choose(value: SupplierPayment) {
    if (!user) return
    setError(null)
    try {
      await saveSettings(user.id, { supplier_payment: value })
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    }
  }

  return (
    <Section
      title="Когда вы платите поставщику"
      hint="От этого зависит, как называется себестоимость с НДС в P&L и отчётах. Расчёт не меняется."
    >
      <FormError message={error} />
      <div role="radiogroup" aria-label="Когда вы платите поставщику" className="grid gap-3 sm:grid-cols-2">
        {SUPPLIER.map(({ value, label, hint }) => {
          const active = settings?.supplier_payment === value
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
              <span className="block text-xs text-faint">{hint}</span>
            </button>
          )
        })}
      </div>
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
    </div>
  )
}
