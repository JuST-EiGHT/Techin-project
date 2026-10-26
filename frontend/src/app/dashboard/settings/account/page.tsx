'use client'
// Меню аккаунта → «Профиль и безопасность» (шаги 0.3, 10.26): имя и фамилия раздельно, e-mail (сменить — после
// подключения почты, 8.3a), телефон (необязательный — с пояснением, зачем), смена пароля, активные сеансы (устройство,
// адрес, вход, активность; завершить один или все, кроме этого), удаление аккаунта — с паролем, предложением скачать
// данные и отсрочкой 30 дней, которую можно отменить.
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { Field, FormError } from '@/components/AuthForm'
import { type User, useAuth } from '@/contexts/AuthContext'
import { api, errorMessage, session } from '@/lib/api'
import { saveFile } from '@/lib/download'

const MIN_PASSWORD = 8
const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' })
const fmtDateTime = (iso: string) => new Date(iso).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow', dateStyle: 'short', timeStyle: 'short' })
const button = 'rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60'
const secondary = 'rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60'

function Section({ title, hint, children, testid, id }: { title: string; hint?: React.ReactNode; children: React.ReactNode; testid?: string; id?: string }) {
  return (
    <section id={id} className="scroll-mt-20 rounded-xl border border-line bg-card p-5" data-testid={testid}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

function ProfileSection() {
  const { user, setUser } = useAuth()
  const [first, setFirst] = useState(user?.first_name ?? '')
  const [last, setLast] = useState(user?.last_name ?? '')
  const [phone, setPhone] = useState(user?.phone ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setFirst(user?.first_name ?? '')
    setLast(user?.last_name ?? '')
    setPhone(user?.phone ?? '')
  }, [user?.first_name, user?.last_name, user?.phone])

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    setBusy(true)
    try {
      const { data } = await api.patch<User>('/auth/me', { first_name: first, last_name: last, phone })
      setUser(data)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Section title="Профиль" testid="profile">
      <FormError message={error} />
      <form onSubmit={save} className="grid max-w-xl gap-x-4 sm:grid-cols-2" noValidate>
        <Field label="Имя" id="first_name" value={first} maxLength={100} onChange={(e) => (setFirst(e.target.value), setSaved(false))} data-testid="profile-first" />
        <Field label="Фамилия" id="last_name" value={last} maxLength={100} onChange={(e) => (setLast(e.target.value), setSaved(false))} data-testid="profile-last" />
        <div className="sm:col-span-2">
          <Field label="E-mail" id="email" value={user?.email ?? ''} readOnly disabled />
          <p className="-mt-3 mb-4 text-xs text-faint">Сменить e-mail можно будет, когда подключим почту: новый адрес нужно будет подтвердить письмом.</p>
        </div>
        <div className="sm:col-span-2">
          <Field label="Телефон — необязательно" id="phone" type="tel" inputMode="tel" placeholder="+7 912 345-67-89" value={phone} maxLength={32} onChange={(e) => (setPhone(e.target.value), setSaved(false))} data-testid="profile-phone" />
          <p className="-mt-3 mb-4 text-xs text-faint" data-testid="phone-why">
            Зачем: связаться с вами, если с аккаунтом что-то случится и нет доступа к почте. Не показываем другим и не используем для рекламы; можно не
            указывать.
          </p>
        </div>
        <div className="flex items-center gap-4 sm:col-span-2">
          <button type="submit" disabled={busy} className={button} data-testid="profile-save">
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
          {saved && (
            <span className="text-sm text-ok" data-testid="profile-saved">
              Сохранено
            </span>
          )}
        </div>
      </form>
    </Section>
  )
}

function PasswordSection() {
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function change(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (next.length < MIN_PASSWORD) return setError(`Новый пароль: минимум ${MIN_PASSWORD} символов`)
    if (next !== repeat) return setError('Новые пароли не совпадают')
    setBusy(true)
    try {
      await api.post('/auth/change-password', { current_password: current, new_password: next })
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
      return
    }
    // все сессии завершены на сервере — чистим cookies и просим войти с новым паролем.
    // Полная перезагрузка, а не logout() из контекста: иначе кабинет сам уведёт на вход без сообщения
    await session.post('/logout').catch(() => undefined)
    window.location.replace('/login?changed=1')
  }

  return (
    <Section title="Смена пароля" hint="После смены пароля нужно будет войти заново на всех устройствах.">
      <FormError message={error} />
      <form onSubmit={change} className="max-w-sm" noValidate>
        <Field label="Текущий пароль" id="current_password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        <Field label="Новый пароль" id="new_password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <Field label="Новый пароль ещё раз" id="new_password2" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        <button type="submit" disabled={busy || !current || !next} className={button}>
          {busy ? 'Меняем…' : 'Сменить пароль'}
        </button>
      </form>
    </Section>
  )
}

type SessionRow = { id: string; device: string; ip: string | null; created_at: string; last_seen_at: string; current: boolean }

function SessionsSection() {
  const [rows, setRows] = useState<SessionRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)

  const load = useCallback(() => {
    api
      .get<SessionRow[]>('/auth/sessions')
      .then(({ data }) => setRows(data))
      .catch((err) => setError(errorMessage(err)))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  async function end(row: SessionRow) {
    setBusy(row.id)
    setError(null)
    try {
      await api.delete(`/auth/sessions/${row.id}`)
      setNotice(`Сеанс «${row.device}» завершён — на том устройстве нужно войти снова.`)
      load()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  async function endOthers() {
    setBusy('others')
    setError(null)
    try {
      const { data } = await api.post<{ ended: number }>('/auth/sessions/end-others')
      setNotice(data.ended ? `Завершено сеансов: ${data.ended}. Остался только этот.` : 'Других сеансов нет.')
      load()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(null)
    }
  }

  const others = (rows ?? []).filter((r) => !r.current).length
  return (
    <Section title="Активные сеансы" hint="Где сейчас выполнен вход. Не узнаёте устройство — завершите сеанс и смените пароль." testid="sessions">
      <FormError message={error} />
      {notice && (
        <p className="mb-3 text-sm text-ok" data-testid="sessions-notice">
          {notice}
        </p>
      )}
      {!rows && !error && <p className="text-sm text-muted">Загружаем…</p>}
      {rows && (
        <ul className="divide-y divide-line rounded-lg border border-line">
          {rows.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 text-sm" data-testid="session-row" data-current={r.current}>
              <div className="min-w-0 flex-1">
                <div className="font-medium">
                  {r.device}
                  {r.current && <span className="ml-2 rounded bg-ok/15 px-2 py-0.5 text-xs text-ok">это устройство</span>}
                </div>
                <div className="text-xs text-muted">
                  {r.ip ? `${r.ip} · ` : ''}вход {fmtDateTime(r.created_at)} · активность {fmtDateTime(r.last_seen_at)}
                </div>
              </div>
              {!r.current && (
                <button type="button" disabled={busy !== null} onClick={() => end(r)} className={secondary} data-testid="session-end">
                  Завершить
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {others > 0 && (
        <button type="button" disabled={busy !== null} onClick={endOthers} className={`mt-3 ${secondary}`} data-testid="sessions-end-others">
          Завершить все, кроме этого
        </button>
      )}
    </Section>
  )
}

function DeleteSection() {
  const { user, setUser } = useAuth()
  const [password, setPassword] = useState('')
  const [sure, setSure] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const due = user?.deletion_due_at ?? null

  async function run(action: () => Promise<{ data: User }>) {
    setBusy(true)
    setError(null)
    try {
      const { data } = await action()
      setUser(data)
      setPassword('')
      setSure(false)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  const download = (path: string, name: string) => saveFile(api.get(path, { responseType: 'blob' }), name).catch((err) => setError(errorMessage(err)))

  if (due)
    return (
      <Section title="Удаление аккаунта" id="delete" testid="delete-account">
        <div className="rounded-lg border border-bad/40 bg-bad/10 p-4 text-sm" data-testid="deletion-pending">
          <b className="text-bad">Аккаунт будет удалён {fmtDate(due)}</b> — вместе с данными всех ваших магазинов. До этого всё работает как обычно,
          данные можно скачать, а удаление — отменить.
          <div className="mt-3">
            <button type="button" disabled={busy} onClick={() => run(() => api.post<User>('/auth/account/cancel-deletion'))} className={button} data-testid="deletion-cancel">
              Отменить удаление
            </button>
          </div>
        </div>
        <FormError message={error} />
      </Section>
    )

  return (
    <Section
      title="Удаление аккаунта"
      id="delete"
      testid="delete-account"
      hint="Удалятся аккаунт и данные всех ваших магазинов: товары, история цен и остатков, начисления, отчёты, операции, настройки. Удаление — через 30 дней: до этого его можно отменить."
    >
      <div className="mb-4 rounded-lg border border-line p-3 text-sm">
        <div className="font-medium">Сначала скачайте, что может пригодиться</div>
        <div className="mt-2 flex flex-wrap gap-2">
          <button type="button" onClick={() => download('/products/export', 'products.csv')} className={secondary} data-testid="export-products">
            Товары (CSV)
          </button>
          <button type="button" onClick={() => download('/costs/template?rows=all&format=xlsx', 'costs.xlsx')} className={secondary} data-testid="export-costs">
            Себестоимость (Excel)
          </button>
          <Link href="/dashboard/operations" className={secondary}>
            Операции → Excel
          </Link>
          <Link href="/dashboard/finance/documents" className={secondary}>
            Отчёты Ozon →
          </Link>
        </div>
        <p className="mt-2 text-xs text-faint">Выгрузки — текущего магазина; если магазинов несколько, переключите магазин и скачайте снова.</p>
      </div>
      <FormError message={error} />
      <form
        onSubmit={(e) => {
          e.preventDefault()
          run(() => api.post<User>('/auth/account/delete', { password }))
        }}
        className="max-w-sm"
        noValidate
      >
        <Field label="Пароль — подтвердите, что это вы" id="delete_password" type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} data-testid="delete-password" />
        <label className="mb-4 flex cursor-pointer items-start gap-2 text-sm">
          <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} className="mt-1 accent-accent" data-testid="delete-sure" />
          Понимаю: через 30 дней аккаунт и данные всех магазинов удалятся без возможности восстановления.
        </label>
        <button type="submit" disabled={busy || !password || !sure} className="rounded-lg bg-bad px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="delete-submit">
          Удалить аккаунт через 30 дней
        </button>
      </form>
    </Section>
  )
}

export default function AccountPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Профиль и безопасность</h1>
      <ProfileSection />
      <PasswordSection />
      <SessionsSection />
      <DeleteSection />
    </div>
  )
}
