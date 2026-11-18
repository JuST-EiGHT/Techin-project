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

export const exportLinks = (body: BuildRequest, format: 'xlsx' | 'csv') =>
  saveFile(api.post('/traffic/links/export', body, { params: { format }, responseType: 'blob' }), `utm-links.${format}`)

/** QR-код ссылки: картинка — через тот же клиент (сессия в cookies), ссылка на объект — для <img>. */
export async function qrObjectUrl(url: string): Promise<string> {
  const r = await api.get<Blob>('/traffic/qr', { params: { url, format: 'svg' }, responseType: 'blob' })
  return URL.createObjectURL(r.data)
}

export const downloadQr = (url: string, format: 'png' | 'svg') =>
  saveFile(api.get('/traffic/qr', { params: { url, format, download: true }, responseType: 'blob' }), `qr.${format}`)
