'use client'

import { useState } from 'react'

import { Field, FormError } from '@/components/AuthForm'
import { type User, useAuth } from '@/contexts/AuthContext'
import { api, errorMessage, session } from '@/lib/api'

const MIN_PASSWORD = 8

function ProfileSection() {
  const { user, setUser } = useAuth()
  const [name, setName] = useState(user?.full_name ?? '')
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    setBusy(true)
    try {
      const { data } = await api.patch<User>('/auth/me', { full_name: name })
      setUser(data)
      setName(data.full_name ?? '')
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="rounded-xl border border-line bg-card p-5">
      <h2 className="mb-4 text-lg font-semibold">Профиль</h2>
      <FormError message={error} />
      <form onSubmit={save} className="max-w-sm" noValidate>
        <Field label="Email" id="email" value={user?.email ?? ''} readOnly disabled />
        <Field label="Имя" id="full_name" value={name} maxLength={255} onChange={(e) => setName(e.target.value)} />
        <div className="flex items-center gap-4">
          <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
            {busy ? 'Сохраняем…' : 'Сохранить'}
          </button>
          {saved && <span className="text-sm text-ok">Сохранено</span>}
        </div>
      </form>
    </section>
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
    <section className="rounded-xl border border-line bg-card p-5">
      <h2 className="text-lg font-semibold">Смена пароля</h2>
      <p className="mb-4 mt-1 text-sm text-muted">После смены пароля нужно будет войти заново на всех устройствах.</p>
      <FormError message={error} />
      <form onSubmit={change} className="max-w-sm" noValidate>
        <Field label="Текущий пароль" id="current_password" type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        <Field label="Новый пароль" id="new_password" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        <Field label="Новый пароль ещё раз" id="new_password2" type="password" autoComplete="new-password" value={repeat} onChange={(e) => setRepeat(e.target.value)} />
        <button type="submit" disabled={busy || !current || !next} className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
          {busy ? 'Меняем…' : 'Сменить пароль'}
        </button>
      </form>
    </section>
  )
}

export default function AccountPage() {
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <h1 className="text-2xl font-semibold">Аккаунт</h1>
      <ProfileSection />
      <PasswordSection />
    </div>
  )
}
