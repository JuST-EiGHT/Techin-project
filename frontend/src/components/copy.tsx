'use client'

// Копирование в буфер одним нажатием — артикул, SKU, штрихкод, как в кабинете Ozon (правка владельца 06.11.2026).
import { Check, Copy } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

async function writeText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // ниже — запасной способ
  }
  // http без TLS или запрет браузера: через скрытое поле
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(area)
  return ok
}

/** Кнопка «скопировать»: значок рядом со значением, после нажатия — галочка на полторы секунды. */
export function CopyButton({ text, label, testid }: { text: string; label: string; testid?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  const timer = useRef<number>()
  useEffect(() => () => window.clearTimeout(timer.current), [])

  async function copy(e: React.MouseEvent) {
    e.preventDefault()
    e.stopPropagation() // строка таблицы по нажатию открывает товар — копирование её не трогает
    const ok = await writeText(text)
    setState(ok ? 'ok' : 'fail')
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setState('idle'), 1500)
  }

  const title = state === 'ok' ? 'Скопировано' : state === 'fail' ? 'Не получилось — выделите и скопируйте вручную' : `Скопировать ${label}`
  return (
    <button
      type="button"
      onClick={copy}
      title={title}
      aria-label={title}
      className={`inline-flex shrink-0 items-center rounded p-0.5 align-middle ${state === 'ok' ? 'text-ok' : state === 'fail' ? 'text-bad' : 'text-faint hover:text-accent'}`}
      data-testid={testid ?? 'copy'}
      data-copied={state === 'ok' || undefined}
    >
      {state === 'ok' ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
    </button>
  )
}

/** Значение с кнопкой копирования: «SKU 123 ⧉». */
export function Copyable({ prefix, value, label, testid }: { prefix?: string; value: string | number; label: string; testid?: string }) {
  const text = String(value)
  return (
    <span className="inline-flex items-center gap-0.5 whitespace-nowrap">
      {prefix && <span>{prefix}</span>}
      <span className="tabular-nums">{text}</span>
      <CopyButton text={text} label={label} testid={testid} />
    </span>
  )
}
