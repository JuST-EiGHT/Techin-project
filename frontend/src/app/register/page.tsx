'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'

import { AuthCard, Field, FormError, SubmitButton } from '@/components/AuthForm'
import { useAuth } from '@/contexts/AuthContext'
import { errorMessage } from '@/lib/api'
import { useRegistrationMode } from '@/lib/registration'

const MIN_PASSWORD = 8 // как на бэкенде (schemas/user.py)

function RegisterForm({ invite }: { invite: boolean }) {
  const { register } = useAuth()
  const router = useRouter()
  const params = useSearchParams()
  const [form, setForm] = useState({
    email: '',
    full_name: '',
    password: '',
    password2: '',
    invite_code: params.get('invite') ?? '',
  })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  function validate(): string | null {
    if (invite && !form.invite_code.trim()) return 'Код приглашения: обязательное поле'
    if (!form.email.trim()) return 'Email: обязательное поле'
    if (form.password.length < MIN_PASSWORD) return `Пароль: минимум ${MIN_PASSWORD} символов`
    if (form.password !== form.password2) return 'Пароли не совпадают'
    return null
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const problem = validate()
    setError(problem)
    if (problem) return
    setBusy(true)
    try {
      await register({
        email: form.email.trim(),
        password: form.password,
        full_name: form.full_name.trim() || undefined,
        invite_code: invite ? form.invite_code.trim() : undefined,
      })
      router.replace('/dashboard')
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <FormError message={error} />
      {invite && (
        <>
          <Field label="Код приглашения" id="invite_code" type="text" autoComplete="off" required value={form.invite_code} onChange={set('invite_code')} />
          <p className="-mt-3 mb-4 text-xs text-muted">
            Регистрация — по приглашениям. Код есть в ссылке, которую прислал администратор сервиса; если вы открыли эту
            ссылку, он уже вписан.
          </p>
        </>
      )}
      <Field label="Email" id="email" type="email" autoComplete="email" required value={form.email} onChange={set('email')} />
      <Field label="Имя" id="full_name" type="text" autoComplete="name" value={form.full_name} onChange={set('full_name')} />
      <Field label="Пароль (минимум 8 символов)" id="password" type="password" autoComplete="new-password" required value={form.password} onChange={set('password')} />
      <Field label="Повтор пароля" id="password2" type="password" autoComplete="new-password" required value={form.password2} onChange={set('password2')} />
      <SubmitButton busy={busy}>Зарегистрироваться</SubmitButton>
    </form>
  )
}

export default function RegisterPage() {
  const mode = useRegistrationMode()
  const footer = (
    <>
      Уже есть аккаунт?{' '}
      <Link href="/login" className="text-accent">
        Войти
      </Link>
    </>
  )

  if (mode === 'closed') {
    return (
      <AuthCard title="Регистрация закрыта" footer={footer}>
        <p className="text-sm text-muted">
          Сервис пока работает в закрытом режиме: аккаунт создаёт администратор. Если вам выдали email и пароль — войдите
          с ними.
        </p>
      </AuthCard>
    )
  }

  return (
    <AuthCard title="Регистрация" footer={footer}>
      {mode === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : (
        <Suspense>
          <RegisterForm invite={mode === 'invite'} />
        </Suspense>
      )}
    </AuthCard>
  )
}
