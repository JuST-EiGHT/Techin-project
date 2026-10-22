'use client'
// Меню аккаунта → «Интеграции» → «Банки» (шаг 3.12): банк по API — операции и остатки сами попадают в «Планирование» раз в час.
// Токен проверяется в банке, хранится зашифрованным и никогда не показывается снова.

import { Building2, RefreshCw } from 'lucide-react'
import Link from 'next/link'
import { useCallback, useEffect, useRef, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, fmtDateTime } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'

type Connection = {
  id: string
  provider: 'tbank' | 'tochka'
  title: string
  status: 'active' | 'error' | 'auth'
  status_message: string | null
  expires_at: string | null
  accounts: { number: string; name: string | null }[]
  last_sync_at: string | null
  created_at: string
}
type Run = { id: string; status: 'queued' | 'running' | 'success' | 'failed'; error: string | null; stats: Record<string, unknown> | null }
type SyncStatus = { bank: { last_run: Run | null } | null }

const STATUS: Record<Connection['status'], { label: string; tone: 'ok' | 'warn' | 'bad' }> = {
  active: { label: 'Работает', tone: 'ok' },
  error: { label: 'Не обновилось', tone: 'warn' },
  auth: { label: 'Нужен новый токен', tone: 'bad' },
}

const BANKS: { code: Connection['provider']; title: string; steps: React.ReactNode[]; note: string }[] = [
  {
    code: 'tbank',
    title: 'Т-Банк',
    steps: [
      <>В Т-Бизнесе откройте «Интеграции» → «T-API» и нажмите «Выпустить токен».</>,
      <>Проверку IP-адресов отключите: «Отключить проверку адресов» (или впишите адрес сервиса, когда он появится).</>,
      <>
        В блоке «К чему будет доступ по API» отметьте только «Счета и выписки»: информацию о счетах и об операциях компании.
        Платежи и другие доступы не нужны — сервис только читает.
      </>,
      <>Нажмите «Выпустить токен», скопируйте его — банк показывает токен один раз — и вставьте ниже.</>,
    ],
    note: 'Токен Т-Банка действует 90 дней с последнего использования. Сервис обращается к банку каждый час, поэтому он не истечёт, пока банк подключён.',
  },
  {
    code: 'tochka',
    title: 'Точка',
    steps: [
      <>В интернет-банке Точки откройте «Сервисы» → «Интеграции и API» и нажмите «Создать JWT-ключ».</>,
      <>Укажите название (например, «Ozon Analytics») и срок доступа. Когда срок выйдет, Точка предложит перевыпустить ключ — новый вставьте здесь же, история сохранится.</>,
      <>
        Разрешения: по умолчанию отмечены все — оставьте только чтение: информацию о счетах, балансы и выписки. Платежи, СБП,
        эквайринг и счета на оплату снимите.
      </>,
      <>Нажмите «Создать ключ», введите код из смс, скопируйте ключ и вставьте ниже.</>,
    ],
    note: 'Точка формирует выписку по запросу — первое обновление с историей за год может занять пару минут.',
  },
]

function ConnectionRow({ conn, onChanged, onRemoved }: { conn: Connection; onChanged: () => void; onRemoved: (id: string) => void }) {
  const [busy, setBusy] = useState<'sync' | 'verify' | 'delete' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current)
  }, [])
  const status = STATUS[conn.status]

  async function watch(runId: string, attempt = 0) {
    try {
      const { data } = await api.get<SyncStatus>('/sync/status')
      const run = data.bank?.last_run
      if (run && run.id === runId && (run.status === 'success' || run.status === 'failed')) {
        const fresh = Number(run.stats?.new ?? 0)
        setResult(run.status === 'success'
          ? { ok: true, text: fresh ? `Готово: новых операций — ${fresh}. Они уже в «Планировании».` : 'Готово: новых операций нет.' }
          : { ok: false, text: run.error ?? 'Не удалось обновить' })
        setBusy(null)
        onChanged()
        return
      }
    } catch {
      // статус недоступен — попробуем ещё раз
    }
    if (attempt > 60) {
      setResult({ ok: true, text: 'Обновление идёт дольше обычного — результат появится здесь позже.' })
      setBusy(null)
      return
    }
    timer.current = setTimeout(() => watch(runId, attempt + 1), 3000)
  }

  async function sync() {
    setBusy('sync')
    setResult(null)
    try {
      const { data } = await api.post<Run>(`/bank/connections/${conn.id}/sync`)
      setResult({ ok: true, text: 'Загружаем операции из банка…' })
      watch(data.id)
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
      setBusy(null)
    }
  }

  async function verify() {
    setBusy('verify')
    setResult(null)
    try {
      const { data } = await api.post<{ ok: boolean; message: string }>(`/bank/connections/${conn.id}/verify`)
      setResult({ ok: data.ok, text: data.message })
      onChanged()
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    setBusy('delete')
    try {
      await api.delete(`/bank/connections/${conn.id}`)
      onRemoved(conn.id)
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
      setBusy(null)
      setConfirming(false)
    }
  }

  const btn = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60'
  return (
    <li className="border-t border-line py-4 first:border-t-0" data-testid="bank-connection">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{conn.title}</span>
            <Badge tone={status.tone}>{status.label}</Badge>
          </div>
          <div className="mt-1 text-sm text-muted">
            Счета: {conn.accounts.map((a) => (a.name ? `${a.name} ${a.number}` : a.number)).join(', ') || '—'}
          </div>
          <div className="mt-1 text-sm text-muted">
            {conn.last_sync_at ? `Обновлено ${fmtDateTime(conn.last_sync_at)} · дальше — раз в час` : 'Ещё не обновлялось — загрузка истории в очереди'}
          </div>
          {conn.expires_at && <div className="mt-1 text-xs text-faint">Токен действует до {fmtDateTime(conn.expires_at)}</div>}
          {conn.status_message && conn.status !== 'active' && (
            <div className={`mt-2 text-sm ${conn.status === 'auth' ? 'text-bad' : 'text-warn'}`}>
              {conn.status_message}
              {conn.status === 'auth' && ' Вставьте новый токен в форму ниже — подключение обновится, история сохранится.'}
            </div>
          )}
          {result && <div className={`mt-2 text-sm ${result.ok ? 'text-ok' : 'text-bad'}`} data-testid="bank-result">{result.text}</div>}
        </div>
        <div className="flex flex-wrap gap-2">
          {confirming ? (
            <>
              <span className="self-center text-sm text-muted">Отключить? Операции останутся.</span>
              <button onClick={remove} disabled={busy !== null} className="rounded-lg bg-bad px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
                {busy === 'delete' ? 'Отключаю…' : 'Да, отключить'}
              </button>
              <button onClick={() => setConfirming(false)} disabled={busy !== null} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-muted">
                Отмена
              </button>
            </>
          ) : (
            <>
              <button onClick={sync} disabled={busy !== null} className={btn} data-testid="bank-sync">
                <RefreshCw className={`mr-1 inline h-3.5 w-3.5 ${busy === 'sync' ? 'animate-spin' : ''}`} />
                {busy === 'sync' ? 'Обновляю…' : 'Обновить'}
              </button>
              <button onClick={verify} disabled={busy !== null} className={btn}>
                {busy === 'verify' ? 'Проверяю…' : 'Проверить доступ'}
              </button>
              <button onClick={() => setConfirming(true)} disabled={busy !== null} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad disabled:opacity-60">
                Отключить
              </button>
            </>
          )}
        </div>
      </div>
    </li>
  )
}

function ConnectForm({ onConnected }: { onConnected: (c: Connection, replaced: boolean) => void }) {
  const [bank, setBank] = useState<Connection['provider']>('tbank')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const chosen = BANKS.find((b) => b.code === bank)!

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!token.trim()) return setError('Вставьте токен из интернет-банка')
    setBusy(true)
    try {
      const { data } = await api.post<{ connection: Connection; replaced: boolean }>('/bank/connections', { provider: bank, token: token.trim() })
      setToken('') // токен не держим в памяти страницы дольше нужного
      onConnected(data.connection, data.replaced)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} noValidate className="rounded-xl border border-line bg-card p-6" data-testid="bank-connect">
      <h2 className="mb-1 text-lg font-semibold">Подключить банк</h2>
      <p className="mb-4 text-sm text-muted">
        Выберите банк — покажем, где взять токен. Перед сохранением токен проверяется запросом в банк; храним его только в
        зашифрованном виде и больше нигде не показываем.
      </p>
      <div role="radiogroup" aria-label="Банк" className="mb-4 grid gap-3 sm:grid-cols-2">
        {BANKS.map((b) => (
          <button
            key={b.code}
            type="button"
            role="radio"
            aria-checked={bank === b.code}
            onClick={() => setBank(b.code)}
            className={`flex items-center gap-3 rounded-xl border p-4 text-left ${bank === b.code ? 'border-accent bg-accent/10' : 'border-line hover:border-muted'}`}
            data-testid={`bank-${b.code}`}
          >
            <Building2 className="h-5 w-5 shrink-0 text-muted" />
            <span className="font-medium">{b.title}</span>
          </button>
        ))}
      </div>
      <ol className="mb-3 list-decimal space-y-1.5 pl-5 text-sm" data-testid="bank-steps">
        {chosen.steps.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ol>
      <p className="mb-4 text-xs text-muted">{chosen.note}</p>
      <FormError message={error} />
      <label htmlFor="bank-token" className="mb-1 block text-sm">
        Токен {chosen.title}
      </label>
      <input
        id="bank-token"
        type="password"
        autoComplete="off"
        value={token}
        onChange={(e) => setToken(e.target.value)}
        className="mb-4 w-full rounded-lg border border-line bg-bg px-3 py-2 text-sm outline-none focus:border-accent"
        data-testid="bank-token"
      />
      <button type="submit" disabled={busy} className="w-full rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60 sm:w-64" data-testid="bank-submit">
        {busy ? 'Проверяю в банке…' : 'Проверить и подключить'}
      </button>
    </form>
  )
}

export function BanksPanel() {
  const [items, setItems] = useState<Connection[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Connection[]>('/bank/connections')
      setItems(data)
      setError(null)
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  return (
    <div className="space-y-6" data-testid="banks">
      <div>
        <p className="text-sm text-muted">
          Необязательно. Подключённый банк сам раз в час присылает операции и остатки в «Планирование»: не нужно вносить расходы
          руками, поступления от Ozon сверяются с выплатами, а налоги по ЕНП закрывают плановые платежи. Доступ — только на
          чтение: отправить платёж сервис не может.
        </p>
      </div>

      <section className="rounded-xl border border-line bg-card p-6">
        <h2 className="mb-2 text-lg font-semibold">Подключённые банки</h2>
        <FormError message={error} />
        {notice && <p className="mb-2 text-sm text-ok" data-testid="bank-notice">{notice}</p>}
        {items === null && !error && <p className="text-sm text-muted">Загрузка…</p>}
        {items?.length === 0 && <p className="text-sm text-muted">Пока ни одного — подключите банк ниже.</p>}
        {items && items.length > 0 && (
          <ul>
            {items.map((c) => (
              <ConnectionRow key={c.id} conn={c} onChanged={load} onRemoved={(id) => setItems((xs) => xs?.filter((x) => x.id !== id) ?? null)} />
            ))}
          </ul>
        )}
      </section>

      <ConnectForm
        onConnected={(c, replaced) => {
          setNotice(replaced
            ? `${c.title}: новый токен сохранён — обновление продолжится с того же места.`
            : `${c.title} подключён. Загружаем историю операций — первые появятся в «Планировании» через минуту-другую.`)
          load()
        }}
      />

      <p className="text-sm text-muted">
        Другого банка нет в списке? Загрузите выписку файлом в формате 1С — её умеет выгружать любой интернет-банк:{' '}
        <Link href="/dashboard/operations/accounts" className="text-accent hover:underline">
          «Операции» → «Счета и выписки»
        </Link>
        . Сбер, Альфа и ВТБ по API — позже.
      </p>
    </div>
  )
}
