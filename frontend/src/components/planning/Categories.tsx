'use client'
// Статьи (шаги 3.8, 10.19): доходы и расходы раздельно, подстатьи (одна вложенность), «где учитывается» — ДДС всегда,
// P&L (прочие расходы бизнеса, шаг 10.22), бюджеты (10.21); архив и возврат. Выбор статьи с «+» — для форм операций:
// новая статья создаётся во всплывающем окне, форма под ним не теряется, новая статья сразу выбрана.
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Modal, input } from '@/components/planning/ui'
import type { Category, TemplateCategory } from '@/components/planning/types'
import { Badge } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type Kind = 'income' | 'expense'
const KIND_TITLE: Record<Kind, string> = { expense: 'Расходы', income: 'Поступления' }
const button = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-50'

export type Branch = { cat: Category; children: Category[] }

/** Действующие статьи вида kind деревом: статья — и её подстатьи. keepId — показать, даже если в архиве (статья
 * исправляемой операции). Подстатья, чья статья не попала в список, — на верхнем уровне. */
export function categoryTree(categories: Category[], kind?: Category['kind'] | null, keepId?: string | null): Branch[] {
  const shown = categories.filter((c) => (!kind || c.kind === kind) && (!c.archived || c.id === keepId))
  const ids = new Set(shown.map((c) => c.id))
  const tops = shown.filter((c) => !c.parent_id || !ids.has(c.parent_id))
  return tops.map((cat) => ({ cat, children: shown.filter((c) => c.parent_id === cat.id && c !== cat) }))
}

/** Варианты для <select>: статья, под ней подстатьи с отступом. */
export function CategoryOptions({ categories, kind, keepId }: { categories: Category[]; kind?: Category['kind'] | null; keepId?: string | null }) {
  return (
    <>
      {categoryTree(categories, kind, keepId).map(({ cat, children }) => [
        <option key={cat.id} value={cat.id}>
          {cat.name}
        </option>,
        ...children.map((c) => (
          <option key={c.id} value={c.id}>
            {` · ${c.name}`}
          </option>
        )),
      ])}
    </>
  )
}

/** Окно «Новая статья»: название и (необязательно) статья, в которую она входит. */
export function NewCategoryModal({
  kind,
  categories,
  initialName = '',
  parentId = '',
  submitLabel = 'Добавить и выбрать',
  onClose,
  onCreated,
}: {
  kind: Kind
  categories: Category[]
  initialName?: string
  parentId?: string
  submitLabel?: string
  onClose: () => void
  onCreated: (c: Category) => void
}) {
  const [name, setName] = useState(initialName)
  const [parent, setParent] = useState(parentId)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const tops = categories.filter((c) => c.kind === kind && !c.archived && !c.parent_id)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.post<Category>('/planning/categories', { name, kind, parent_id: parent || null })
      onCreated(data)
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <Modal title={kind === 'income' ? 'Новая статья поступлений' : 'Новая статья расходов'} onClose={onClose}>
      <form onSubmit={save} className="space-y-3" data-testid="new-cat-form">
        <label className="block text-sm">
          <span className="text-muted">Название</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} autoFocus className={`${input} mt-1`} data-testid="new-cat-name" />
        </label>
        <label className="block text-sm">
          <span className="text-muted">Входит в статью — необязательно</span>
          <select value={parent} onChange={(e) => setParent(e.target.value)} className={`${input} mt-1`} data-testid="new-cat-parent">
            <option value="">— самостоятельная статья —</option>
            {tops.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-faint">
            Подстатья — для подробностей: например, «Реклама Яндекс» в «Маркетинге». В ДДС и отчётах её сумма входит в статью.
          </span>
        </label>
        <FormError message={error} />
        <div className="flex gap-2">
          <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="new-cat-save">
            {busy ? 'Сохраняем…' : submitLabel}
          </button>
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-sm text-muted hover:text-fg">
            Отмена
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** Выбор статьи с «+» рядом: новая статья — во всплывающем окне, сразу выбрана; остальное в форме не меняется. */
export function CategoryPicker({
  categories,
  kind,
  value,
  onChange,
  onCreated,
  keepId,
  noneLabel = 'Без статьи',
  testid = 'op-category',
  className = '',
}: {
  categories: Category[]
  kind: Kind
  value: string
  onChange: (id: string) => void
  onCreated?: (c: Category) => void // обновить список статей страницы
  keepId?: string | null
  noneLabel?: string
  testid?: string
  className?: string
}) {
  const [adding, setAdding] = useState(false)
  const [created, setCreated] = useState<Category[]>([]) // пока список страницы не обновился
  const all = useMemo(() => [...categories, ...created.filter((c) => !categories.some((x) => x.id === c.id))], [categories, created])
  return (
    <div className={`flex gap-2 ${className}`}>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label="Статья" className={`${input} min-w-0 flex-1`} data-testid={testid}>
        <option value="">{noneLabel}</option>
        <CategoryOptions categories={all} kind={kind} keepId={keepId ?? value} />
      </select>
      <button
        type="button"
        onClick={() => setAdding(true)}
        title="Новая статья"
        aria-label="Новая статья"
        className="shrink-0 rounded-lg border border-line px-3 text-lg leading-none text-muted hover:border-accent hover:text-accent"
        data-testid={`${testid}-add`}
      >
        +
      </button>
      {adding && (
        <NewCategoryModal
          kind={kind}
          categories={all}
          onClose={() => setAdding(false)}
          onCreated={(c) => {
            setAdding(false)
            setCreated((prev) => [...prev, c])
            onChange(c.id)
            onCreated?.(c)
          }}
        />
      )}
    </div>
  )
}

// ---------- страница «Статьи» ----------

function Where({ c }: { c: Category }) {
  return (
    <span className="inline-flex flex-wrap gap-1" data-testid="cat-where">
      <Badge tone="muted">ДДС</Badge>
      {c.in_pnl && <Badge tone="accent">P&amp;L</Badge>}
      {c.in_budget && <Badge tone="muted">бюджеты</Badge>}
    </span>
  )
}

function EditCategory({ cat, categories, onClose, onSaved }: { cat: Category; categories: Category[]; onClose: () => void; onSaved: () => void }) {
  const [name, setName] = useState(cat.name)
  const [parent, setParent] = useState(cat.parent_id ?? '')
  const [inPnl, setInPnl] = useState(cat.in_pnl)
  const [inBudget, setInBudget] = useState(cat.in_budget)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const tops = categories.filter((c) => c.kind === cat.kind && !c.archived && !c.parent_id && c.id !== cat.id)
  const canMove = !cat.required && !cat.has_children

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    const body: Record<string, unknown> = {}
    if (name.trim() !== cat.name) body.name = name
    if ((parent || null) !== cat.parent_id) body.parent_id = parent || null
    if (inPnl !== cat.in_pnl) body.in_pnl = inPnl
    if (inBudget !== cat.in_budget) body.in_budget = inBudget
    try {
      if (Object.keys(body).length) await api.patch(`/planning/categories/${cat.id}`, body)
      onSaved()
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  const check = 'mt-0.5 h-4 w-4 shrink-0 accent-accent'
  return (
    <Modal title={`Статья «${cat.name}»`} onClose={onClose}>
      <form onSubmit={save} className="space-y-4 text-sm" data-testid="cat-edit-form">
        <label className="block">
          <span className="text-muted">Название</span>
          <input value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} className={`${input} mt-1`} data-testid="cat-edit-name" />
        </label>
        <label className="block">
          <span className="text-muted">Входит в статью</span>
          <select value={parent} onChange={(e) => setParent(e.target.value)} disabled={!canMove} className={`${input} mt-1`} data-testid="cat-edit-parent">
            <option value="">— самостоятельная статья —</option>
            {tops.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-xs text-faint">
            {cat.required
              ? 'Эта статья нужна сервису — она остаётся самостоятельной.'
              : cat.has_children
                ? 'У статьи есть подстатьи — вложенность одна, поэтому сама она подстатьёй стать не может.'
                : 'Подстатья входит в статью: в ДДС и отчётах её сумма — внутри статьи, операции остаются при подстатье.'}
          </span>
        </label>
        <fieldset className="space-y-2">
          <legend className="text-muted">Где учитывается</legend>
          <label className="flex gap-2">
            <input type="checkbox" checked disabled className={check} />
            <span>
              ДДС — всегда
              <span className="block text-xs text-faint">Движение денег по статьям: сколько пришло и ушло.</span>
            </span>
          </label>
          {cat.kind === 'expense' && (
            <label className="flex gap-2">
              <input type="checkbox" checked={inPnl} onChange={(e) => setInPnl(e.target.checked)} className={check} data-testid="cat-edit-pnl" />
              <span>
                P&amp;L — прочие расходы бизнеса
                <span className="block text-xs text-faint">
                  Расход вычтется из прибыли магазина (аренда, зарплата, реклама вне Ozon). Не отмечайте закупку товара и затраты
                  производства, уже входящие в себестоимость, налоги, кредиты и вывод денег себе: себестоимость и налог P&amp;L считает сам —
                  иначе они вычтутся дважды.
                </span>
              </span>
            </label>
          )}
          <label className="flex gap-2">
            <input type="checkbox" checked={inBudget} onChange={(e) => setInBudget(e.target.checked)} className={check} data-testid="cat-edit-budget" />
            <span>
              Бюджеты
              <span className="block text-xs text-faint">По статье можно задать лимит на месяц и сравнивать с фактом.</span>
            </span>
          </label>
        </fieldset>
        <FormError message={error} />
        <div className="flex gap-2">
          <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="cat-edit-save">
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
          <button type="button" onClick={onClose} className="rounded-lg border border-line px-4 py-2 text-muted hover:text-fg">
            Отмена
          </button>
        </div>
      </form>
    </Modal>
  )
}

export function CategoriesPage({ categories, templates, onChanged }: { categories: Category[]; templates: TemplateCategory[]; onChanged: () => void }) {
  const [kind, setKind] = useState<Kind>('expense')
  const [editing, setEditing] = useState<Category | null>(null)
  const [adding, setAdding] = useState<{ parent: string } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const tree = categoryTree(categories, kind)
  const archived = categories.filter((c) => c.kind === kind && c.archived)
  const transfer = categories.find((c) => c.kind === 'transfer' && !c.archived)
  const have = new Set(categories.filter((c) => !c.archived && c.code).map((c) => c.code))
  const missing = templates.filter((t) => !have.has(t.code) && t.kind === kind)
  useEffect(() => setNotice(null), [kind])

  async function run(request: Promise<unknown>, text?: string) {
    setError(null)
    setNotice(null)
    try {
      await request
      if (text) setNotice(text)
      onChanged()
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const row = (c: Category, child: boolean) => (
    <li key={c.id} className={`flex flex-wrap items-start gap-x-3 gap-y-1 py-2.5 text-sm ${child ? 'pl-6' : ''}`} data-testid={child ? 'cat-child' : 'cat-row'}>
      <span className="min-w-0 flex-1">
        <span className={child ? '' : 'font-medium'}>
          {child && <span className="text-faint">· </span>}
          {c.name}
        </span>
        {c.required && <span className="ml-1 text-xs text-faint">· нужна сервису</span>}
        {c.why && <span className="block text-xs text-muted">{c.why}</span>}
        <span className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-faint">
          <Where c={c} />
          <span>операций: {c.operations}</span>
        </span>
      </span>
      <span className="flex basis-full flex-wrap gap-x-3 gap-y-1 text-xs sm:basis-auto">
        {!child && (
          <button type="button" onClick={() => setAdding({ parent: c.id })} className="text-accent hover:underline" data-testid="cat-add-child">
            + Подстатья
          </button>
        )}
        <button type="button" onClick={() => setEditing(c)} className="text-muted hover:text-fg" data-testid="cat-edit">
          Изменить
        </button>
        {!c.required && (
          <button
            type="button"
            disabled={c.has_children}
            title={c.has_children ? 'Сначала перенесите подстатьи или отправьте их в архив' : undefined}
            onClick={() => run(api.patch(`/planning/categories/${c.id}`, { archived: true }), `«${c.name}» — в архиве. Операции с ней остаются.`)}
            className="text-muted hover:text-bad disabled:opacity-40 disabled:hover:text-muted"
            data-testid="cat-archive"
          >
            В архив
          </button>
        )}
      </span>
    </li>
  )

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        По статьям видно, на что уходят деньги. Подстатьи — для подробностей: их сумма входит в статью в ДДС и отчётах. Статья в архиве
        остаётся у прежних операций. Чтобы операции из банка сами получали статью —{' '}
        <Link href="/dashboard/operations/rules" className="text-accent hover:underline">
          правила разнесения
        </Link>
        .
      </p>
      <div className="flex gap-1 rounded-lg border border-line bg-card p-1 sm:w-fit" role="tablist" aria-label="Вид статей">
        {(['expense', 'income'] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            onClick={() => setKind(k)}
            className={`flex-1 rounded-md px-4 py-1.5 text-sm sm:flex-none ${kind === k ? 'bg-accent text-white' : 'text-muted hover:text-fg'}`}
            data-testid={`cats-tab-${k}`}
          >
            {KIND_TITLE[k]} ({categories.filter((c) => c.kind === k && !c.archived).length})
          </button>
        ))}
      </div>
      <FormError message={error} />
      {notice && <p className="text-sm text-ok" data-testid="cats-notice">{notice}</p>}
      <section className="rounded-xl border border-line bg-card p-4">
        <div className="mb-1 flex flex-wrap items-center gap-2">
          <h2 className="text-sm font-semibold">{KIND_TITLE[kind]}</h2>
          <button type="button" onClick={() => setAdding({ parent: '' })} className={`${button} ml-auto`} data-testid="cat-add">
            + Новая статья
          </button>
        </div>
        {tree.length === 0 ? (
          <p className="py-2 text-sm text-muted">Статей пока нет — добавьте свою или из шаблона ниже.</p>
        ) : (
          <ul className="divide-y divide-line" data-testid={`cats-${kind}`}>
            {tree.map(({ cat, children }) => [row(cat, false), ...children.map((c) => row(c, true))])}
          </ul>
        )}
      </section>
      <section className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="cats-where-help">
        <h2 className="mb-2 font-semibold">Где учитывается статья</h2>
        <ul className="space-y-1 text-muted">
          <li>
            <b className="text-fg">ДДС</b> — всегда: сколько денег пришло и ушло по статье за период («Планирование → ДДС»).
          </li>
          <li>
            <b className="text-fg">P&amp;L</b> — расходы бизнеса вне Ozon (аренда, зарплата, своя реклама) войдут в прибыль магазина как прочие
            расходы. Закупка товара и затраты производства из себестоимости, налоги, кредиты и вывод денег себе — нет: себестоимость и
            налог P&amp;L считает сам.
          </li>
          <li>
            <b className="text-fg">Бюджеты</b> — лимит по статье на месяц и сравнение с фактом.
          </li>
        </ul>
        {transfer && <p className="mt-2 text-xs text-faint">«{transfer.name}» — служебная статья: не поступление и не расход, в ДДС и итоги не входит.</p>}
      </section>
      {archived.length > 0 && (
        <details className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="cats-archive">
          <summary className="cursor-pointer font-semibold">Архив ({archived.length})</summary>
          <ul className="mt-2 divide-y divide-line">
            {archived.map((c) => (
              <li key={c.id} className="flex items-center gap-3 py-2">
                <span className="min-w-0 flex-1">
                  {c.name}
                  <span className="block text-xs text-faint">операций: {c.operations}</span>
                </span>
                <button type="button" onClick={() => run(api.patch(`/planning/categories/${c.id}`, { archived: false }), `«${c.name}» снова в списке.`)} className="text-accent hover:underline" data-testid="cat-restore">
                  Вернуть
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {missing.length > 0 && (
        <details className="rounded-xl border border-line bg-card p-4 text-sm">
          <summary className="cursor-pointer font-semibold">Ещё из шаблона ({missing.length})</summary>
          <ul className="mt-2 divide-y divide-line">
            {missing.map((t) => (
              <li key={t.code} className="flex items-center gap-3 py-2">
                <span className="min-w-0 flex-1">
                  {t.name}
                  <span className="block text-xs text-muted">{t.why}</span>
                </span>
                <button type="button" onClick={() => run(api.post('/planning/categories/from-template', { codes: [t.code] }))} className="text-accent hover:underline">
                  Добавить
                </button>
              </li>
            ))}
          </ul>
        </details>
      )}
      {adding && (
        <NewCategoryModal
          kind={kind}
          categories={categories}
          parentId={adding.parent}
          submitLabel="Добавить"
          onClose={() => setAdding(null)}
          onCreated={(c) => {
            setAdding(null)
            setNotice(`Статья «${c.name}» добавлена.`)
            onChanged()
          }}
        />
      )}
      {editing && (
        <EditCategory
          cat={editing}
          categories={categories}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            onChanged()
          }}
        />
      )}
    </div>
  )
}
