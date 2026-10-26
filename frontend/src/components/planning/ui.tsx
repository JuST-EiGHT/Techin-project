'use client'
// Общие элементы страниц «Операций» и «Планирования»: поле ввода и всплывающее окно.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export const input = 'w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'

const openModals: object[] = [] // окна поверх окон (новая статья из формы операции): Escape закрывает только верхнее

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false)
  const close = useRef(onClose)
  close.current = onClose
  useEffect(() => {
    const me = {}
    openModals.push(me)
    setMounted(true)
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && openModals[openModals.length - 1] === me && close.current()
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('keydown', onKey)
      openModals.splice(openModals.indexOf(me), 1)
    }
  }, [])
  if (!mounted) return null
  // в body: окно из формы не попадает внутрь её <form>; щелчки и отправка — только в своём окне
  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-bg/70 sm:items-center sm:p-4"
      onClick={(e) => {
        e.stopPropagation()
        onClose()
      }}
      onSubmit={(e) => e.stopPropagation()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-xl border border-line bg-card p-5 shadow-xl sm:rounded-xl"
      >
        <div className="mb-3 flex items-start gap-3">
          <h2 className="text-lg font-semibold">{title}</h2>
          <button type="button" onClick={onClose} className="ml-auto rounded-md px-2 py-1 text-muted hover:text-fg" aria-label="Закрыть">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}

