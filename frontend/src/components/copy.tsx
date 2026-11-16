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
export function CopyButton({ text, label, testid, className = '' }: { text: string; label: string; testid?: string; className?: string }) {
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
      className={`inline-flex shrink-0 items-center rounded p-0.5 align-middle ${state === 'ok' ? 'text-ok' : state === 'fail' ? 'text-bad' : 'text-faint hover:text-accent'} ${className}`}
      data-testid={testid ?? 'copy'}
      data-copied={state === 'ok' || undefined}
      data-state={state}
    >
      {state === 'ok' ? <Check className="h-3.5 w-3.5" aria-hidden /> : <Copy className="h-3.5 w-3.5" aria-hidden />}
    </button>
  )
}

// Значок копирования виден при наведении на значение (правка владельца 16.11.2026, как в кабинете Ozon), с клавиатуры —
// при фокусе, только что скопировано — пока горит галочка. На телефоне наведения нет — значок виден всегда, после текста.
const REVEAL =
  'opacity-0 transition-opacity group-hover/copy:opacity-100 focus-visible:opacity-100 data-[state=ok]:opacity-100 data-[state=fail]:opacity-100 [@media(hover:none)]:opacity-100'
// не помещается — конец текста под значком растворяется
const FADE = 'linear-gradient(to right, #000 calc(100% - 2.5rem), transparent calc(100% - 0.75rem))'
const ICON_ROOM = 20 // px: значок с отступом — помещается ли после текста

/**
 * Значение, которое можно скопировать: артикул, название, SKU, штрихкод. Текст обрезается по ширине ячейки «…»; при
 * наведении появляется значок копирования — сразу после текста, а если текст не поместился — поверх его конца, конец
 * растворяется. `children` — как показать (по умолчанию само значение), копируется всегда `value`.
 */
export function CopyText({ value, label, children, className = '', inline, testid, buttonTestid }: {
  value: string | number
  label: string
  children?: React.ReactNode
  className?: string
  /** в строке текста («SKU 123»), а не отдельным блоком ячейки */
  inline?: boolean
  testid?: string
  buttonTestid?: string
}) {
  const text = String(value)
  const outer = useRef<HTMLSpanElement>(null)
  const box = useRef<HTMLSpanElement>(null)
  const [over, setOver] = useState(false) // текст не поместился — или значку негде встать после него
  const [hover, setHover] = useState(false)
  const measure = () => {
    const el = box.current
    const room = (outer.current?.clientWidth ?? 0) - (el?.offsetWidth ?? 0)
    // в строке текста значок стоит после значения всегда (место под него оставлено), в ячейке — поверх, если тесно
    setOver(Boolean(el && (el.scrollWidth > el.clientWidth + 1 || (!inline && room < ICON_ROOM))))
    setHover(true)
  }
  const fade = over && hover ? { maskImage: FADE, WebkitMaskImage: FADE } : undefined
  return (
    <span ref={outer} className={`${inline ? 'inline-flex max-w-full align-bottom' : 'flex'} min-w-0 ${className}`} data-testid={testid}>
      <span className="group/copy relative flex min-w-0 max-w-full items-center" onMouseEnter={measure} onMouseLeave={() => setHover(false)} data-over={over || undefined}>
        <span ref={box} className="min-w-0 truncate" style={fade}>
          {children ?? text}
        </span>
        <CopyButton
          text={text}
          label={label}
          testid={buttonTestid ?? (testid ? `${testid}-copy` : 'copy')}
          className={`${REVEAL} ml-0.5 ${
            inline && !over
              ? ''
              : `[@media(hover:hover)]:absolute [@media(hover:hover)]:top-1/2 [@media(hover:hover)]:-translate-y-1/2 ${over ? '[@media(hover:hover)]:right-0' : '[@media(hover:hover)]:left-full'}`
          }`}
        />
      </span>
    </span>
  )
}

/** Значение с кнопкой копирования: «SKU 123 ⧉» — значок при наведении. */
export function Copyable({ prefix, value, label, testid }: { prefix?: string; value: string | number; label: string; testid?: string }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 whitespace-nowrap">
      {prefix && <span>{prefix}</span>}
      <CopyText value={value} label={label} inline className="tabular-nums" buttonTestid={testid}>
        {String(value)}
      </CopyText>
    </span>
  )
}
