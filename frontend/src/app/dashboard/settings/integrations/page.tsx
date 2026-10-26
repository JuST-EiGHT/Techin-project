'use client'
// Меню аккаунта → «Интеграции» (шаг 10.1): одна страница вместо «Магазинов», «API-ключей Ozon» и «Банков»; Telegram —
// подключение бота (что присылать — в «Уведомлениях»). Вкладка — в адресе (?tab=banks), старые адреса переадресуются
// (lib/redirects.ts). Шаг 10.26: сводка — состояние, последний успешный обмен и ошибки по Ozon, банкам и Telegram;
// у ключа Ozon и банка — «Проверить соединение», «Обновить доступ» (новый ключ или токен), «Отключить».
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense } from 'react'

import { BanksPanel } from '@/components/settings/Banks'
import { IntegrationsSummary } from '@/components/settings/IntegrationsSummary'
import { OzonKeysPanel } from '@/components/settings/OzonKeys'
import { StoresPanel } from '@/components/settings/OzonStores'
import { TelegramPanel } from '@/components/settings/Telegram'

type Tab = 'ozon' | 'banks' | 'telegram'
const TABS: { value: Tab; label: string; hint: string }[] = [
  { value: 'ozon', label: 'Ozon', hint: 'магазины и ключи Seller API' },
  { value: 'banks', label: 'Банки', hint: 'операции и остатки по API' },
  { value: 'telegram', label: 'Telegram', hint: 'уведомления в чат' },
]

export default function IntegrationsPage() {
  return (
    <Suspense>
      <Integrations />
    </Suspense>
  )
}

function Integrations() {
  const params = useSearchParams()
  const router = useRouter()
  const raw = params.get('tab')
  const tab: Tab = TABS.some((t) => t.value === raw) ? (raw as Tab) : 'ozon'
  const open = (t: Tab) => router.replace(t === 'ozon' ? '/dashboard/settings/integrations' : `/dashboard/settings/integrations?tab=${t}`, { scroll: false })

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Интеграции</h1>
        <p className="mt-1 text-sm text-muted">
          Откуда сервис берёт данные и куда присылает уведомления. Ключи и токены хранятся только зашифрованными и после
          сохранения не показываются.
        </p>
      </div>
      <IntegrationsSummary onOpen={open} />
      <div className="flex gap-1 overflow-x-auto border-b border-line" role="tablist" aria-label="Интеграции">
        {TABS.map((t) => (
          <button
            key={t.value}
            type="button"
            role="tab"
            aria-selected={tab === t.value}
            title={t.hint}
            onClick={() => open(t.value)}
            className={`-mb-px whitespace-nowrap border-b-2 px-4 py-2 text-sm ${tab === t.value ? 'border-accent font-medium text-fg' : 'border-transparent text-muted hover:text-fg'}`}
            data-testid={`tab-${t.value}`}
          >
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'ozon' && (
        <StoresPanel>
          <OzonKeysPanel />
        </StoresPanel>
      )}
      {tab === 'banks' && <BanksPanel />}
      {tab === 'telegram' && <TelegramPanel />}
    </div>
  )
}
