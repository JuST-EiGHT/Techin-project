// Себестоимость: общие подписи для справочника (шаг 2.3) и правки в карточке товара (шаг 4.1).
import { fmtDay } from '@/components/reports'

export const BEGINNING = '2000-01-01' // первая цена без даты — «с начала учёта»

// ставка НДС поставщика → коэффициент (как в шаблоне); пусто — как в налоговом профиле
export const VAT_OPTIONS = [
  { value: '1.220', label: '22 %' },
  { value: '1.200', label: '20 %' },
  { value: '1.100', label: '10 %' },
  { value: '1.070', label: '7 %' },
  { value: '1.050', label: '5 %' },
  { value: '1.000', label: 'без НДС' },
]

export const since = (d: string | null) => (d === null ? '—' : d === BEGINNING ? 'с начала учёта' : `с ${fmtDay(d)}`)

export const toNumber = (text: string) => {
  const n = Number(text.replace(/\s/g, '').replace(',', '.'))
  return text.trim() && Number.isFinite(n) ? n : null
}

export function vatText(v: string | null): string {
  if (v === null) return 'по профилю'
  const pct = Math.round((Number(v) - 1) * 1000) / 10
  return pct === 0 ? 'без НДС' : `${pct.toLocaleString('ru-RU')} %`
}

/** Адрес себестоимости артикула: артикул со «/» допустим — кодируем по частям. */
export const costPath = (offerId: string) => `/costs/${offerId.split('/').map(encodeURIComponent).join('/')}`
