// Скачивание файлов с бэкенда (шаблоны, выгрузки) через тот же axios-клиент:
// при истёкшей сессии он сам обновит токен и повторит запрос.
import axios, { type AxiosResponse } from 'axios'

import { api } from '@/lib/api'

/** Имя файла из Content-Disposition: filename* (UTF-8, по-русски) важнее filename. */
function fileName(header: string | undefined, fallback: string): string {
  if (!header) return fallback
  const utf8 = /filename\*=UTF-8''([^;]+)/i.exec(header)
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1])
    } catch {
      /* битое имя — ниже возьмём простое */
    }
  }
  return /filename="?([^";]+)"?/i.exec(header)?.[1] ?? fallback
}

/** Сохраняет файл из ответа. Ошибка бэкенда при responseType blob тоже приходит Blob-ом —
 * разворачиваем её в JSON, чтобы errorMessage показал понятный текст. */
export async function saveFile(request: Promise<AxiosResponse<Blob>>, fallback: string): Promise<void> {
  let res: AxiosResponse<Blob>
  try {
    res = await request
  } catch (err) {
    if (axios.isAxiosError(err) && err.response?.data instanceof Blob) {
      try {
        err.response.data = JSON.parse(await err.response.data.text())
      } catch {
        /* не JSON — останется общий текст ошибки */
      }
    }
    throw err
  }
  const url = URL.createObjectURL(res.data)
  const a = document.createElement('a')
  a.href = url
  a.download = fileName(res.headers['content-disposition'], fallback)
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export type TemplateRows = 'all' | 'missing' | 'empty'
export type TemplateFormat = 'xlsx' | 'csv'

/** Шаблон себестоимости с товарами пользователя (шаг 2.4). */
export function downloadCostTemplate(rows: TemplateRows, format: TemplateFormat = 'xlsx'): Promise<void> {
  return saveFile(api.get('/costs/template', { params: { rows, format }, responseType: 'blob' }), `costs.${format}`)
}

/** Строки файла с ошибками — тем же шаблоном с колонкой «Ошибка». */
export function downloadCostErrors(file: File): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  return saveFile(api.post('/costs/import/errors', form, { responseType: 'blob' }), 'costs-errors.xlsx')
}

/** Шаблон цен с текущими ценами (шаг 6.3): поменять «Новая цена» и загрузить. */
export function downloadPriceTemplate(format: TemplateFormat = 'xlsx'): Promise<void> {
  return saveFile(api.get('/prices/bulk/template', { params: { format }, responseType: 'blob' }), `prices.${format}`)
}

/** Строки файла цен с ошибками и не прошедшие проверки — тем же шаблоном с колонкой «Ошибка». */
export function downloadPriceErrors(file: File): Promise<void> {
  const form = new FormData()
  form.append('file', file)
  return saveFile(api.post('/prices/bulk/upload/errors', form, { responseType: 'blob' }), 'prices-errors.xlsx')
}
