'use client'

import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useState } from 'react'

import { AuthCard, Field, FormError, SubmitButton } from '@/components/AuthForm'
import { useAuth } from '@/contexts/AuthContext'
import { errorMessage } from '@/lib/api'

const MIN_PASSWORD = 8 // как на бэкенде (schemas/user.py)

export default function RegisterPage() {
  const { register } = useAuth()
  const router = useRouter()
  const [form, setForm] = useState({ email: '', full_name: '', password: '', password2: '' })
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  function validate(): string | null {
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
      })
      router.replace('/dashboard')
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <AuthCard
      title="Регистрация"
      footer={
        <>
          Уже есть аккаунт?{' '}
          <Link href="/login" className="text-accent">
            Войти
          </Link>
        </>
      }
    >
      <form onSubmit={onSubmit} noValidate>
        <FormError message={error} />
        <Field label="Email" id="email" type="email" autoComplete="email" required value={form.email} onChange={set('email')} />
        <Field label="Имя" id="full_name" type="text" autoComplete="name" value={form.full_name} onChange={set('full_name')} />
        <Field label="Пароль (минимум 8 символов)" id="password" type="password" autoComplete="new-password" required value={form.password} onChange={set('password')} />
        <Field label="Повтор пароля" id="password2" type="password" autoComplete="new-password" required value={form.password2} onChange={set('password2')} />
        <SubmitButton busy={busy}>Зарегистрироваться</SubmitButton>
      </form>
    </AuthCard>
  )
}
