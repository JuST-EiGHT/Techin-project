// Настройки (меню аккаунта) для страниц: загружаются один раз на пользователя, обновляются при сохранении.
import { useEffect, useRef, useState } from 'react'

import { useAuth } from '@/contexts/AuthContext'
import { api } from '@/lib/api'
import type { ThemePreference } from '@/lib/theme'

/** «Откуда товар»: при закупке, после продажи, с отсрочкой, своё производство, по-разному (варианты — lib/supplier.ts). */
export type SupplierPayment = 'upfront' | 'after_sale' | 'deferred' | 'own_production' | 'mixed'

export type UserSettings = {
  theme: ThemePreference
  supplier_payment: SupplierPayment
  supplier_deferral_days: number | null
  cost_label: string
  price_ad_pct: string // калькулятор цены (шаг 4.2): реклама, % от цены
  price_target_margin_pct: string // целевая маржа, %
  price_defaults: boolean // реклама и маржа ещё не заданы — значения по умолчанию
  // пороги статуса остатка, дней запаса (шаг 5.1): меньше — «Критично», меньше — «Мало», больше — «Избыток»
  stock_critical_days: number
  stock_low_days: number
  stock_excess_days: number
  // планирование поставок (шаг 5.2): запас после поставки, дней, и сколько дней поставка в пути
  supply_target_days: number
  supply_lead_time_days: number
  // порог «низкой маржи», % (шаг 7.1): акции, товары, прибыль по товарам, цены, аномалия «маржа ниже порога»
  low_margin_pct: string
  // пороги аномалий (шаг 7.1, BUSINESS_LOGIC §7); дни запаса — пороги остатков выше
  anomaly_sales_drop_pct: number
  anomaly_sales_drop_red_pct: number
  anomaly_sales_growth_pct: number
  anomaly_price_pct: number
  anomaly_price_red_pct: number
  anomaly_commission_pp: string
  anomaly_payout_diff_pct: number
  anomaly_cash_gap_days: number
  anomaly_uncategorized_days: number
  anomaly_sync_stale_hours: number // данные не обновлялись дольше, ч (10.5)
  anomaly_data_gap_days: number // дней без начислений между загруженными (10.5)
  recommendation_hide_days: number // «Скрыть» рекомендацию — на сколько дней (10.6)
  anomaly_defaults: boolean // пороги аномалий и маржи — по умолчанию
}

/** Порог «низкой маржи» по умолчанию, % (BUSINESS_LOGIC §7). */
export const DEFAULT_LOW_MARGIN = 10

/** Как называется себестоимость с НДС в P&L (подпись приходит с бэкенда — это запасной вариант). */
export const DEFAULT_COST_LABEL = 'Себестоимость с НДС'

let cache: { userId: string; settings: Promise<UserSettings> } | null = null
const listeners = new Set<(s: UserSettings) => void>()

export function loadSettings(userId: string): Promise<UserSettings> {
  if (cache?.userId !== userId) {
    const settings = api.get<UserSettings>('/settings').then((r) => r.data)
    settings.catch(() => {
      if (cache?.settings === settings) cache = null
    })
    cache = { userId, settings }
  }
  return cache.settings
}

export async function saveSettings(
  userId: string,
  changes: Partial<Omit<UserSettings, 'cost_label' | 'price_defaults' | 'anomaly_defaults'>>,
): Promise<UserSettings> {
  const { data } = await api.patch<UserSettings>('/settings', changes)
  cache = { userId, settings: Promise.resolve(data) }
  listeners.forEach((l) => l(data))
  return data
}

/** Настройки текущего пользователя; null — ещё загружаются (или не загрузились). */
export function useSettings(): UserSettings | null {
  const { user } = useAuth()
  const [settings, setSettings] = useState<UserSettings | null>(null)
  useEffect(() => {
    if (!user) return
    let alive = true
    loadSettings(user.id)
      .then((s) => alive && setSettings(s))
      .catch(() => undefined)
    listeners.add(setSettings)
    return () => {
      alive = false
      listeners.delete(setSettings)
    }
  }, [user])
  return settings
}

/** «Себестоимость с НДС», «Долг поставщику» или «Себестоимость производства» — по «Откуда товар». */
export function useCostLabel(): string {
  return useSettings()?.cost_label ?? DEFAULT_COST_LABEL
}

/** Порог «низкой маржи» пользователя, % («Настройки → Аномалии и порог маржи»): ниже — жёлтым, ниже нуля — красным. */
export function useLowMargin(): number {
  const value = useSettings()?.low_margin_pct
  return value === undefined ? DEFAULT_LOW_MARGIN : Number(value)
}

/** Реклама или целевая маржа поменялись (например, сохранили в панели товара) — onChange: таблица пересчитает цены. */
export function useOnPriceSettingsChange(onChange: () => void) {
  const settings = useSettings()
  const key = settings ? `${settings.price_ad_pct}/${settings.price_target_margin_pct}` : null
  const last = useRef<string | null>(null)
  const callback = useRef(onChange)
  callback.current = onChange
  useEffect(() => {
    if (key === null) return
    if (last.current !== null && last.current !== key) callback.current()
    last.current = key
  }, [key])
}
