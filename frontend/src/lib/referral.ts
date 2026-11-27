// «Продвижение → Реферальная программа» (шаг 11.7): тем, кто оставил отзыв, — личная ссылка с кодом участника в
// utm_content; продавец отправляет её сам и отмечает «Отправлено». Покупки друзей — 11.8, промокоды — 11.9.
import { api } from '@/lib/api'
import { saveFile } from '@/lib/download'

export type SentChannel = 'ozon_chat' | 'outside' | 'review' | 'other'
export type Target = 'product' | 'store'

export type Program = { campaign_name: string; utm_source: string; utm_medium: string; store_url: string | null; defaults: boolean }

export type ReferralSettings = {
  program: Program
  utm_prefix: string | null
  campaign: string | null
  channels: Record<SentChannel, string>
}

export type Participant = {
  id: string
  code: string
  name: string
  product: { id: string; offer_id: string; name: string | null; sku: number | null } | null
  review_date: string | null
  note: string | null
  target: Target
  link_id: string
  url: string
  status: 'new' | 'sent'
  sent: { channel: SentChannel; channel_title: string; sent_on: string; note: string | null; marked_at: string } | null
  created_at: string
  deleted: boolean
}

export type ParticipantTab = 'all' | 'new' | 'sent' | 'deleted'

export type ParticipantList = { items: Participant[]; total: number; counts: Record<ParticipantTab, number> }

export type ParticipantFilters = {
  tab: ParticipantTab
  search?: string
  date_from?: string
  date_to?: string
  channel?: string
  product_id?: string
}

export type ParticipantEvent = { kind: string; title: string; channel: string | null; sent_on: string | null; note: string | null; created_at: string }

const clean = (f: ParticipantFilters) => Object.fromEntries(Object.entries(f).filter(([, v]) => v !== undefined && v !== ''))

export const getReferralSettings = () => api.get<ReferralSettings>('/referral/settings').then((r) => r.data)

export const saveReferralSettings = (body: { campaign_name: string; utm_source: string; utm_medium: string; store_url: string | null }) =>
  api.put<ReferralSettings>('/referral/settings', body).then((r) => r.data)

export const listParticipants = (f: ParticipantFilters) =>
  api.get<ParticipantList>('/referral/participants', { params: clean(f) }).then((r) => r.data)

export const exportParticipants = (f: ParticipantFilters) =>
  saveFile(api.get('/referral/participants/export', { params: clean(f), responseType: 'blob' }), 'referral.xlsx')

export const createParticipant = (body: { name: string; product_id: string | null; review_date: string | null; note: string | null; target: Target }) =>
  api.post<Participant>('/referral/participants', body).then((r) => r.data)

export const updateParticipant = (id: string, body: Partial<{ name: string; review_date: string | null; note: string }>) =>
  api.patch<Participant>(`/referral/participants/${id}`, body).then((r) => r.data)

export const markSent = (id: string, body: { channel: SentChannel; sent_on: string | null; note: string | null }) =>
  api.post<Participant>(`/referral/participants/${id}/sent`, body).then((r) => r.data)

export const unmarkSent = (id: string) => api.post<Participant>(`/referral/participants/${id}/unsent`).then((r) => r.data)

export const removeParticipant = (id: string) => api.delete(`/referral/participants/${id}`)

export const restoreParticipant = (id: string) => api.post<Participant>(`/referral/participants/${id}/restore`).then((r) => r.data)

export const participantEvents = (id: string) =>
  api.get<ParticipantEvent[]>(`/referral/participants/${id}/events`).then((r) => r.data)

/** Зачем каждая настройка (правило 15). */
export const PROGRAM_WHY = {
  campaign: 'Идёт в метку utm_campaign после префикса магазина: по ней покупки друзей видны в отчётах Ozon отдельно от остальной рекламы.',
  labels: 'utm_source и utm_medium — общие у всех ссылок программы; по ним программа отделяется от другой рекламы в отчётах.',
  store: 'Нужна, если хотите вести ссылку не на товар из отзыва, а на весь магазин. Скопируйте адрес страницы магазина в Ozon.',
}
