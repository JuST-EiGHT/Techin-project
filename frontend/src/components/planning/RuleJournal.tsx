'use client'
// «Правила разнесения → Журнал применения» (шаг 10.20): когда и почему правила пересчитали статьи, сколько операций
// проверено и изменено, какие (было → стало), ошибки и предупреждения по правилам.
import { useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import type { RuleRun, RunDetail } from '@/components/planning/types'
import { Badge, fmtDateTime, fmtDay, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

function who(d: RunDetail): string {
  if (d.by === 'auto') return 'встроенное правило'
  if (d.by === 'none') return d.rule ? `правило №${d.rule.number ?? '—'} больше не подходит` : 'правило больше не подходит'
  return d.rule ? `правило №${d.rule.number ?? '—'}` : 'правило'
}

export function RuleJournal() {
  const [runs, setRuns] = useState<RuleRun[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<RuleRun[]>('/planning/rules/runs', { params: { limit: 50 } })
      .then((r) => setRuns(r.data))
      .catch((err) => setError(errorMessage(err)))
  }, [])

  if (error) return <FormError message={error} />
  if (!runs) return <p className="text-sm text-muted">Загрузка…</p>
  return (
    <section className="space-y-3" data-testid="rule-journal">
      <p className="text-sm text-muted">
        Каждый раз, когда правила пересчитывают статьи — добавили или изменили правило, поменяли порядок, загрузили выписку, — здесь запись: что проверили и
        что поменяли. Ошибки — правила, которые не работают, и что с ними сделать.
      </p>
      {runs.length === 0 ? (
        <p className="rounded-xl border border-line bg-card p-4 text-sm text-muted">Пока пусто: правила ещё не применялись.</p>
      ) : (
        <ul className="space-y-2">
          {runs.map((run) => (
            <li key={run.id} className="rounded-xl border border-line bg-card p-4 text-sm" data-testid="rule-run">
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="font-medium">{run.title}</span>
                <span className="text-xs text-faint">{fmtDateTime(run.created_at)}</span>
                <span className="ml-auto flex gap-1">
                  <Badge tone={run.changed ? 'accent' : 'muted'}>изменено {run.changed}</Badge>
                  <Badge tone="muted">проверено {run.checked}</Badge>
                  {run.problems.length > 0 && <Badge tone="warn">предупреждений {run.problems.length}</Badge>}
                </span>
              </div>
              {run.rule && (
                <p className="mt-1 text-xs text-muted">
                  {run.rule.number ? `№${run.rule.number}: ` : ''}
                  {run.rule.text}
                </p>
              )}
              {run.problems.length > 0 && (
                <ul className="mt-2 space-y-1 text-xs text-warn" data-testid="rule-run-problems">
                  {run.problems.map((p, i) => (
                    <li key={i}>
                      {p.rule?.number ? `Правило №${p.rule.number}: ` : ''}
                      {p.message}
                    </li>
                  ))}
                </ul>
              )}
              {run.details.length > 0 && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs text-accent hover:underline">
                    Какие операции ({run.changed}
                    {run.changed > run.details.length ? `, показаны первые ${run.details.length}` : ''})
                  </summary>
                  <ul className="mt-2 divide-y divide-line text-xs" data-testid="rule-run-details">
                    {run.details.map((d) => (
                      <li key={d.op_id} className="grid gap-x-3 gap-y-0.5 py-1.5 sm:grid-cols-[5rem_7rem_minmax(0,1fr)_minmax(0,1.3fr)]">
                        <span className="tabular-nums text-muted">{fmtDay(d.day)}</span>
                        <span className="tabular-nums">{rub(d.amount)}</span>
                        <span className="truncate text-muted">{d.counterparty ?? '—'}</span>
                        <span>
                          {d.before ?? 'без статьи'} → <b>{d.after ?? 'без статьи'}</b> <span className="text-faint">· {who(d)}</span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
