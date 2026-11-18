// «Продвижение → Внешний трафик» (фаза 11). Шаг 11.1: префикс UTM_CAMPAIGN и конструктор ссылок с UTM-метками.
// Правила Ozon и сборка ссылок — на бэкенде (engine/traffic_links), здесь — типы и запросы.
import { api } from '@/lib/api'
import { saveFile } from '@/lib/download'

export type Macro = { code: string; label: string }
export type Channel = {
  key: string
  label: string
  source: string
  medium: string
  fixed_source: boolean
  hint: string
  macros: Macro[]
  /** подсказки в выпадающем списке, как у Ozon; можно вписать своё */
  source_options: string[]
  medium_options: string[]
}

export type TrafficSettings = {
  utm_prefix: string | null
  cabinet_url: string
  channels: Channel[]
  sections: Record<string, string>
  max_links: number
}

export type ProductOption = { id: string; offer_id: string; name: string | null; sku: number | null; archived: boolean }

export type BuildRequest = {
  product_ids: string[]
  urls: string
  channel: string
  campaign: string
  source?: string | null
  medium?: string | null
  term?: string | null
  content?: string | null
}

export type BuildRow = {
  kind: 'product' | 'url'
  line: number | null
  input: string
  url: string | null
  section: string | null
  error: string | null
  product: { id: string; offer_id: string; name: string | null; sku: number | null } | null
  /** сохранённая ссылка (шаг 11.2); у строки с ошибкой — нет */
  link_id: string | null
}

export type BuildResult = {
  campaign: string
  source: string
  medium: string
  term: string
  content: string
  rows: BuildRow[]
  ok: number
  errors: number
  warnings: string[]
}

/** Зачем префикс (правило 15) — одной строкой у поля и в пустом состоянии конструктора. */
export const PREFIX_WHY =
  'Ozon узнаёт ваши кампании только по префиксу в метке utm_campaign. Без него переходы по ссылкам не попадут в статистику магазина.'

export const PREFIX_RE = /^vendor_org_\d{1,20}$/

export const getTrafficSettings = () => api.get<TrafficSettings>('/traffic/settings').then((r) => r.data)

export const saveTrafficPrefix = (utm_prefix: string | null) =>
  api.put<TrafficSettings>('/traffic/settings', { utm_prefix }).then((r) => r.data)

export const searchTrafficProducts = (search: string) =>
  api.get<ProductOption[]>('/traffic/products', { params: { search: search || undefined, limit: 30 } }).then((r) => r.data)

export const buildLinks = (body: BuildRequest) => api.post<BuildResult>('/traffic/links/build', body).then((r) => r.data)

/** Собрать и сохранить в «Созданные ссылки» (шаг 11.2). */
export const createLinks = (body: BuildRequest) => api.post<BuildResult>('/traffic/links', body).then((r) => r.data)

export const exportLinks = (body: BuildRequest, format: 'xlsx' | 'csv') =>
  saveFile(api.post('/traffic/links/export', body, { params: { format }, responseType: 'blob' }), `utm-links.${format}`)

/** QR-код ссылки: картинка — через тот же клиент (сессия в cookies), ссылка на объект — для <img>. */
export async function qrObjectUrl(url: string): Promise<string> {
  const r = await api.get<Blob>('/traffic/qr', { params: { url, format: 'svg' }, responseType: 'blob' })
  return URL.createObjectURL(r.data)
}

export const downloadQr = (url: string, format: 'png' | 'svg') =>
  saveFile(api.get('/traffic/qr', { params: { url, format, download: true }, responseType: 'blob' }), `qr.${format}`)

// ---------- история ссылок (шаг 11.2) ----------

export type LinkTab = 'all' | 'deeplink' | 'expiring' | 'substitute' | 'deleted'

export type DeeplinkStatus = { state: 'ok' | 'soon' | 'expired'; expires_on: string; days_left: number; last_activity: string }

export type LinkRow = {
  link_id: string
  version: number
  versions: number
  created_at: string
  updated_at: string
  product: { id: string; offer_id: string; name: string | null; sku: number | null } | null
  source_url: string
  section: string
  channel: string
  campaign: string
  campaign_name: string
  utm_source: string
  utm_medium: string
  utm_term: string
  utm_content: string
  url: string
  deeplink: string | null
  deeplink_status: DeeplinkStatus | null
  substitute_code: string | null
  deleted: boolean
}

export type LinkList = {
  items: LinkRow[]
  total: number
  counts: Record<LinkTab, number>
  facets: { campaigns: string[]; sources: string[]; mediums: string[] }
}

export type LinkFilters = {
  tab: LinkTab
  search?: string
  date_from?: string
  date_to?: string
  campaign?: string
  source?: string
  medium?: string
  channel?: string
}

export type LinkUpdate = Partial<{
  campaign: string
  source: string
  medium: string
  term: string
  content: string
  deeplink: string
  substitute_code: string
}>

export type LinkVersion = {
  version: number
  created_at: string
  campaign: string
  utm_source: string
  utm_medium: string
  utm_term: string
  utm_content: string
  url: string
  deeplink: string | null
  substitute_code: string | null
  deleted_at: string | null
  superseded_at: string | null
}

const clean = (f: LinkFilters) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== ''))

export const listLinks = (f: LinkFilters) => api.get<LinkList>('/traffic/links', { params: clean(f) }).then((r) => r.data)

export const exportLinkHistory = (f: LinkFilters) =>
  saveFile(api.get('/traffic/links/export', { params: clean(f), responseType: 'blob' }), 'utm-links-history.xlsx')

export const updateLink = (id: string, body: LinkUpdate) =>
  api.patch<{ link: LinkRow; warnings: string[] }>(`/traffic/links/${id}`, body).then((r) => r.data)

export const deleteLink = (id: string) => api.delete(`/traffic/links/${id}`)

export const restoreLink = (id: string) => api.post<LinkRow>(`/traffic/links/${id}/restore`).then((r) => r.data)

export const markOpened = (id: string) => api.post<LinkRow>(`/traffic/links/${id}/opened`).then((r) => r.data)

export const linkVersions = (id: string) => api.get<LinkVersion[]>(`/traffic/links/${id}/versions`).then((r) => r.data)

/** Зачем поля из кабинета (правило 15). */
export const DEEPLINK_WHY =
  'Диплинк откроет страницу в приложении Ozon. Его создаёт только кабинет Ozon — скопируйте ссылку https://ozon.onelink.me/… из списка созданных ссылок. Без переходов 30 дней он перестаёт работать — сервис напомнит заранее.'
export const SUBSTITUTE_WHY =
  'Подменный артикул (O-4374100) блогеры и соцсети показывают вместо обычного — по нему Ozon тоже считает переходы. Он есть не у всех продавцов, берётся в кабинете Ozon.'
