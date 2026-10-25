'use client'
// «Операции → Правила разнесения» (шаги 3.12, 10.20): условия → статья. Встроенные правила — всегда первыми,
// ваши — по порядку сверху вниз, срабатывает первое подошедшее. Статью, поставленную вручную, правила не меняют.
// 10.20: условия «счёт» и «источник», операции правила, предупреждения, «Применить ко всем» с проверкой.
import { ArrowDown, ArrowUp, Pencil, Trash2 } from 'lucide-react'
import Link from 'next/link'
import { useSearchParams } from 'next/navigation'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { CategoryOptions } from '@/components/planning/Categories'
import { type Account, type Category, type Rule, type RuleDirection, type RuleSource, type RulesDryRun, type RulesPage, parseAmount } from '@/components/planning/types'
import { Modal } from '@/components/planning/ui'
import { fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

const input = 'w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent'
const button = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60'
const KIND: Record<RuleDirection, 'income' | 'expense'> = { in: 'income', out: 'expense' }

type Form = { direction: RuleDirection; category_id: string; counterparty_inn: string; counterparty: string; purpose: string; amount_min: string; amount_max: string; account_id: string; source: '' | RuleSource }
type Preview = { matched: number; examples: { op_id: string; day: string; amount: string; counterparty: string | null; comment: string | null }[] }

const EMPTY: Form = { direction: 'out', category_id: '', counterparty_inn: '', counterparty: '', purpose: '', amount_min: '', amount_max: '', account_id: '', source: '' }
export const SOURCE_TEXT: Record<RuleSource, string> = { bank: 'из банка', import: 'из файла операций', manual: 'внесена вручную' }

/** Условия правила одной строкой: «Платёж · ИНН … · «аренд» в назначении · от 1 000 ₽ · счёт «Карта»». */
export function describe(r: Pick<Rule, 'direction' | 'counterparty_inn' | 'counterparty' | 'purpose' | 'amount_min' | 'amount_max' | 'account' | 'source'>): string {
  const parts = [r.direction === 'in' ? 'Поступление' : 'Платёж']
  if (r.counterparty_inn) parts.push(`ИНН ${r.counterparty_inn}`)
  if (r.counterparty) parts.push(`контрагент «${r.counterparty}»`)
  if (r.purpose) parts.push(`в назначении «${r.purpose}»`)
  if (r.amount_min && r.amount_max) parts.push(`${rub(r.amount_min)} — ${rub(r.amount_max)}`)
  else if (r.amount_min) parts.push(`от ${rub(r.amount_min)}`)
  else if (r.amount_max) parts.push(`до ${rub(r.amount_max)}`)
  if (r.account) parts.push(`счёт «${r.account.name}»`)
  if (r.source) parts.push(SOURCE_TEXT[r.source])
  return parts.join(' · ')
}

function body(f: Form) {
  return {
    direction: f.direction,
    category_id: f.category_id,
    counterparty_inn: f.counterparty_inn.trim() || null,
    counterparty: f.counterparty.trim() || null,
    purpose: f.purpose.trim() || null,
    amount_min: f.amount_min.trim() ? parseAmount(f.amount_min) : null,
    amount_max: f.amount_max.trim() ? parseAmount(f.amount_max) : null,
    account_id: f.account_id || null,
    source: f.source || null,
  }
}

function RuleForm({
  categories,
  accounts,
  initial,
  prefill,
  onSaved,
  onCancel,
}: {
  categories: Category[]
  accounts: Account[]
  initial?: Rule
  prefill?: Partial<Form> // из «Нераспознанных»: направление, ИНН или контрагент
  onSaved: (applied: number) => void
  onCancel?: () => void
}) {
  const [form, setForm] = useState<Form>(() =>
    initial
      ? {
          direction: initial.direction,
          category_id: initial.category?.id ?? '',
          counterparty_inn: initial.counterparty_inn ?? '',
          counterparty: initial.counterparty ?? '',
          purpose: initial.purpose ?? '',
          amount_min: initial.amount_min ? initial.amount_min.replace('.', ',') : '',
          amount_max: initial.amount_max ? initial.amount_max.replace('.', ',') : '',
          account_id: initial.account?.id ?? '',
          source: initial.source ?? '',
        }
      : { ...EMPTY, ...prefill },
  )
  const [preview, setPreview] = useState<Preview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const set = (k: keyof Form) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setForm((f) => ({ ...f, [k]: e.target.value }))
  const hasCondition = Boolean(
    form.counterparty_inn.trim() || form.counterparty.trim() || form.purpose.trim() || form.amount_min.trim() || form.amount_max.trim() || form.account_id || form.source,
  )

  // сколько операций подойдёт — пока заполняют форму
  useEffect(() => {
    if (!form.category_id || !hasCondition) {
      setPreview(null)
      return
    }
    const t = setTimeout(async () => {
      try {
        const { data } = await api.post<Preview>('/planning/rules/preview', body(form))
        setPreview(data)
      } catch {
        setPreview(null)
      }
    }, 400)
    return () => clearTimeout(t)
  }, [form, hasCondition])

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!form.category_id) return setError('Выберите статью')
    if (!hasCondition) return setError('Укажите хотя бы одно условие: ИНН, контрагента, слова в назначении, сумму, счёт или источник')
    setSaving(true)
    try {
      const { data } = initial
        ? await api.patch<{ applied: number }>(`/planning/rules/${initial.id}`, body(form))
        : await api.post<{ applied: number }>('/planning/rules', body(form))
      if (!initial) setForm({ ...EMPTY, direction: form.direction })
      onSaved(data.applied)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  const hint = 'mt-1 block text-xs text-muted'
  return (
    <form onSubmit={submit} className="space-y-3" data-testid="rule-form">
      <FormError message={error} />
      <div className="flex gap-2" role="radiogroup" aria-label="Для каких операций">
        {(['out', 'in'] as const).map((d) => (
          <button
            key={d}
            type="button"
            role="radio"
            aria-checked={form.direction === d}
            onClick={() => setForm((f) => ({ ...f, direction: d, category_id: '' }))}
            className={`rounded-lg border px-3 py-1.5 text-sm ${form.direction === d ? 'border-accent bg-accent/10 text-accent' : 'border-line hover:border-muted'}`}
          >
            {d === 'out' ? 'Платежи' : 'Поступления'}
          </button>
        ))}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm">
          ИНН контрагента
          <input value={form.counterparty_inn} onChange={set('counterparty_inn')} inputMode="numeric" className={input} data-testid="rule-inn" />
          <span className={hint}>Точнее всего: ИНН есть в операциях из банка</span>
        </label>
        <label className="text-sm">
          Контрагент
          <input value={form.counterparty} onChange={set('counterparty')} placeholder="например, Петров" className={input} data-testid="rule-counterparty" />
          <span className={hint}>Все слова должны быть в названии — порядок не важен</span>
        </label>
        <label className="text-sm">
          Слова в назначении платежа
          <input value={form.purpose} onChange={set('purpose')} placeholder="например: аренд, склад" className={input} data-testid="rule-purpose" />
          <span className={hint}>Через запятую — достаточно любого; «аренд» найдёт «Аренда склада»</span>
        </label>
        <div className="text-sm">
          Сумма, ₽
          <div className="flex items-center gap-2">
            <input value={form.amount_min} onChange={set('amount_min')} placeholder="от" inputMode="decimal" className={input} aria-label="Сумма от" />
            <span className="text-muted">—</span>
            <input value={form.amount_max} onChange={set('amount_max')} placeholder="до" inputMode="decimal" className={input} aria-label="Сумма до" />
          </div>
          <span className={hint}>Необязательно — например, чтобы мелкие платежи шли в другую статью</span>
        </div>
        <label className="text-sm">
          Счёт
          <select value={form.account_id} onChange={set('account_id')} className={input} data-testid="rule-account">
            <option value="">Любой</option>
            {accounts
              .filter((a) => !a.archived || a.id === form.account_id)
              .map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
          </select>
          <span className={hint}>Например, все платежи с карты сотрудника — в «Хозяйственные расходы»</span>
        </label>
        <label className="text-sm">
          Источник операции
          <select value={form.source} onChange={set('source')} className={input} data-testid="rule-source">
            <option value="">Любой</option>
            <option value="bank">Из банка — выписка или API</option>
            <option value="import">Из файла операций</option>
            <option value="manual">Внесена вручную без статьи</option>
          </select>
          <span className={hint}>Чтобы правило не трогало операции из других источников</span>
        </label>
      </div>
      <label className="block text-sm">
        Статья
        <select value={form.category_id} onChange={set('category_id')} className={input} data-testid="rule-category">
          <option value="">— выберите —</option>
          <CategoryOptions categories={categories} kind={KIND[form.direction]} />
        </select>
      </label>
      {preview && (
        <div className="rounded-lg border border-line bg-bg p-3 text-sm" data-testid="rule-preview">
          {preview.matched === 0 ? (
            <span className="text-muted">Сейчас подходящих операций без ручной разметки нет — правило сработает на новых.</span>
          ) : (
            <>
              <span>Подойдёт операций без ручной разметки: <b>{preview.matched}</b></span>
              <ul className="mt-1 space-y-0.5 text-xs text-muted">
                {preview.examples.map((x) => (
                  <li key={x.op_id}>
                    {fmtDay(x.day)} · {rub(x.amount)} · {x.counterparty ?? x.comment ?? '—'}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={saving} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="rule-save">
          {saving ? 'Сохраняю…' : initial ? 'Сохранить' : 'Добавить правило'}
        </button>
        {onCancel && (
          <button type="button" onClick={onCancel} className={button}>
            Отмена
          </button>
        )}
      </div>
    </form>
  )
}

/** Проверка перед массовым применением: что поменяют правила у всех операций без ручной разметки → «Применить». */
function ApplyAll({ onDone }: { onDone: (text: string) => void }) {
  const [check, setCheck] = useState<RulesDryRun | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setError(null)
    try {
      setCheck((await api.post<RulesDryRun>('/planning/rules/check')).data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function apply() {
    setBusy(true)
    try {
      const { data } = await api.post<{ changed: number }>('/planning/rules/apply')
      setCheck(null)
      onDone(`Правила применены: статья поменялась у операций — ${data.changed}. Подробности — в «Журнале применения».`)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <button type="button" onClick={run} disabled={busy} className={button} data-testid="rules-check">
        {busy && !check ? 'Проверяю…' : 'Применить ко всем операциям…'}
      </button>
      <FormError message={error} />
      {check && (
        <Modal title="Применить правила ко всем операциям" onClose={() => setCheck(null)}>
          <div className="space-y-3 text-sm" data-testid="rules-check-result">
            <p>
              Проверено операций без ручной разметки: <b>{check.checked}</b>. Статья поменяется у <b>{check.changed}</b>. Операции, где статью ставили вручную,
              не изменятся.
            </p>
            {check.examples.length > 0 && (
              <ul className="space-y-1 rounded-lg border border-line p-3 text-xs">
                {check.examples.map((x) => (
                  <li key={x.op_id} className="flex flex-wrap gap-x-2">
                    <span className="tabular-nums text-muted">{fmtDay(x.day)}</span>
                    <span className="tabular-nums">{rub(x.amount)}</span>
                    <span className="text-muted">{x.counterparty ?? '—'}</span>
                    <span>
                      {x.before ?? 'без статьи'} → <b>{x.after ?? 'без статьи'}</b>
                    </span>
                  </li>
                ))}
                {check.changed > check.examples.length && <li className="text-faint">и ещё {check.changed - check.examples.length}</li>}
              </ul>
            )}
            {check.problems.length > 0 && (
              <ul className="space-y-1 text-xs text-warn">
                {check.problems.map((p, i) => (
                  <li key={i}>
                    {p.rule?.number ? `Правило №${p.rule.number}: ` : ''}
                    {p.message}
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <button type="button" onClick={apply} disabled={busy || check.changed === 0} className="rounded-lg bg-accent px-4 py-2 font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="rules-apply">
                {check.changed === 0 ? 'Менять нечего' : `Применить (${check.changed})`}
              </button>
              <button type="button" onClick={() => setCheck(null)} className={button}>
                Отмена
              </button>
            </div>
          </div>
        </Modal>
      )}
    </>
  )
}

export function RulesSection({ categories, accounts, onChanged }: { categories: Category[]; accounts: Account[]; onChanged: () => void }) {
  const params = useSearchParams()
  const [page, setPage] = useState<RulesPage | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // «Создать правило» из нераспознанных операций: ?new=1&direction=out&inn=…&counterparty=…
  const [prefill] = useState<Partial<Form> | null>(() =>
    params.get('new')
      ? {
          direction: params.get('direction') === 'in' ? 'in' : 'out',
          counterparty_inn: params.get('inn') ?? '',
          counterparty: params.get('counterparty') ?? '',
          account_id: params.get('account') ?? '',
        }
      : null,
  )

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<RulesPage>('/planning/rules')
      setPage(data)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const done = (applied: number, what: string) => {
    setNotice(applied ? `${what} Статья поменялась у операций: ${applied}.` : `${what} Операции без ручной разметки не изменились.`)
    setEditing(null)
    load()
    onChanged()
  }

  async function run(request: Promise<{ data: { applied?: number } | RulesPage }>, what: string) {
    setError(null)
    try {
      const { data } = await request
      done('applied' in data ? (data.applied ?? 0) : 0, what)
    } catch (err) {
      setError(errorMessage(err))
    }
  }

  const move = (index: number, delta: number) => {
    if (!page) return
    const ids = page.rules.map((r) => r.id)
    const [id] = ids.splice(index, 1)
    ids.splice(index + delta, 0, id)
    run(api.post<RulesPage>('/planning/rules/reorder', { ids }), 'Порядок сохранён.')
  }

  return (
    <section className="rounded-xl border border-line bg-card p-4" data-testid="rules">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">Правила разнесения</h2>
          <p className="mt-1 text-sm text-muted">
            Операции из банка и из файла без статьи разносятся сами — не нужно размечать каждую. Правила проверяются сверху вниз,
            срабатывает первое подходящее. Статью, которую вы поставили вручную, правила не меняют.
          </p>
        </div>
        {page && page.rules.length > 0 && (
          <ApplyAll
            onDone={(text) => {
              setNotice(text)
              load()
              onChanged()
            }}
          />
        )}
      </div>
      <FormError message={error} />
      {notice && <p className="mt-2 text-sm text-ok" data-testid="rules-notice">{notice}</p>}
      {page && (
        <>
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-muted hover:text-fg">Встроенные — всегда первыми ({page.builtin.length})</summary>
            <ul className="mt-2 divide-y divide-line">
              {page.builtin.map((b) => (
                <li key={b.code} className="py-2">
                  <span className="font-medium">{b.title}</span> → {b.category}
                  <span className="block text-xs text-muted">{b.when}. {b.why}.</span>
                </li>
              ))}
            </ul>
          </details>
          {page.rules.length === 0 ? (
            <p className="mt-3 text-sm text-muted">Своих правил пока нет. Добавьте ниже — или поставьте статью операции вручную: сервис предложит правило для этого контрагента.</p>
          ) : (
            <ol className="mt-3 divide-y divide-line" data-testid="rules-list">
              {page.rules.map((r, i) =>
                editing === r.id ? (
                  <li key={r.id} className="py-3">
                    <RuleForm categories={categories} accounts={accounts} initial={r} onSaved={(n) => done(n, 'Правило сохранено.')} onCancel={() => setEditing(null)} />
                  </li>
                ) : (
                  <li key={r.id} className={`flex flex-wrap items-center gap-2 py-2 text-sm ${r.enabled ? '' : 'opacity-60'}`} data-testid="rule-row">
                    <span className="w-5 text-right tabular-nums text-faint">{i + 1}</span>
                    <span className="min-w-0 flex-1">
                      {describe(r)} → <b>{r.category?.name ?? '—'}</b>
                      <span className="block text-xs text-muted">
                        {r.enabled ? (
                          <>
                            <Link href={`/dashboard/operations?rule=${r.id}`} className="text-accent hover:underline" data-testid="rule-ops">
                              разнесено операций: {r.operations} →
                            </Link>
                            {r.matches !== undefined && r.matches > r.operations && <span> · подходит под условия: {r.matches}</span>}
                          </>
                        ) : (
                          'выключено — его операции остались без статьи'
                        )}
                      </span>
                      {r.problem && (
                        <span className="mt-0.5 block text-xs text-warn" data-testid="rule-problem">
                          {r.problem}
                        </span>
                      )}
                    </span>
                    <span className="flex items-center gap-1 text-muted">
                      <button type="button" disabled={i === 0} onClick={() => move(i, -1)} className="rounded p-1 hover:text-fg disabled:opacity-30" aria-label="Выше" data-testid="rule-up">
                        <ArrowUp className="h-4 w-4" />
                      </button>
                      <button type="button" disabled={i === page.rules.length - 1} onClick={() => move(i, 1)} className="rounded p-1 hover:text-fg disabled:opacity-30" aria-label="Ниже">
                        <ArrowDown className="h-4 w-4" />
                      </button>
                      <button type="button" onClick={() => run(api.patch(`/planning/rules/${r.id}`, { enabled: !r.enabled }), r.enabled ? 'Правило выключено.' : 'Правило включено.')} className="rounded px-1.5 py-1 text-xs hover:text-fg">
                        {r.enabled ? 'Выключить' : 'Включить'}
                      </button>
                      <button type="button" onClick={() => setEditing(r.id)} className="rounded p-1 hover:text-fg" aria-label="Изменить">
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => window.confirm('Удалить правило? Ручная разметка не изменится.') && run(api.delete(`/planning/rules/${r.id}`), 'Правило удалено.')}
                        className="rounded p-1 hover:text-bad"
                        aria-label="Удалить"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </span>
                  </li>
                ),
              )}
            </ol>
          )}
          <details className="mt-3 rounded-lg border border-line p-3" open={page.rules.length === 0 || prefill !== null} data-testid="rule-add">
            <summary className="cursor-pointer text-sm font-medium">Новое правило</summary>
            <div className="mt-3">
              <RuleForm categories={categories} accounts={accounts} prefill={prefill ?? undefined} onSaved={(n) => done(n, 'Правило добавлено.')} />
            </div>
          </details>
        </>
      )}
    </section>
  )
}
