'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'

import { AuthCard, Field, FormError, SubmitButton } from '@/components/AuthForm'
import { useAuth } from '@/contexts/AuthContext'
import { errorMessage } from '@/lib/api'
import { safeNext } from '@/lib/session'

function LoginForm() {
  const { login } = useAuth()
  const router = useRouter()
  const next = safeNext(useSearchParams().get('next'))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setBusy(true)
    try {
      await login(email.trim(), password)
      router.replace(next)
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate>
      <FormError message={error} />
      <Field label="Email" id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Пароль" id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      <SubmitButton busy={busy}>Войти</SubmitButton>
    </form>
  )
}

export default function LoginPage() {
  return (
    <AuthCard
      title="Вход"
      footer={
        <>
          Нет аккаунта?{' '}
          <Link href="/register" className="text-accent">
            Зарегистрироваться
          </Link>
        </>
      }
    >
      <Suspense>
        <LoginForm />
      </Suspense>
    </AuthCard>
  )
}
