'use client'

import { Download } from 'lucide-react'
import { useState } from 'react'

import { errorMessage } from '@/lib/api'
import { downloadCostTemplate, type TemplateFormat, type TemplateRows } from '@/lib/download'

/** Кнопка «скачать шаблон себестоимости»: со своими товарами, только без цены или пустой. */
export function CostTemplateButton({
  rows,
  format = 'xlsx',
  children,
  className = 'inline-flex items-center gap-1.5 text-sm text-accent hover:underline disabled:opacity-60',
}: {
  rows: TemplateRows
  format?: TemplateFormat
  children: React.ReactNode
  className?: string
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function download() {
    setBusy(true)
    setError(null)
    try {
      await downloadCostTemplate(rows, format)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" onClick={download} disabled={busy} className={className} data-testid={`template-${rows}`}>
        <Download className="h-4 w-4 shrink-0" />
        {busy ? 'Готовим файл…' : children}
      </button>
      {error && <span className="text-xs text-bad">{error}</span>}
    </span>
  )
}
