'use client'

import Link from 'next/link'
import { useRouter, useSearchParams } from 'next/navigation'
import { Suspense, useState } from 'react'

import { AuthCard, Field, FormError, SubmitButton } from '@/components/AuthForm'
import { useAuth } from '@/contexts/AuthContext'
import { errorMessage } from '@/lib/api'
import { useRegistrationMode, type RegistrationMode } from '@/lib/registration'
import { safeNext } from '@/lib/session'

function LoginForm() {
  const { login } = useAuth()
  const router = useRouter()
  const params = useSearchParams()
  const next = safeNext(params.get('next'))
  const passwordChanged = params.get('changed') === '1'
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
      {passwordChanged && !error && (
        <p role="status" className="mb-4 rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok">
          Пароль изменён — войдите с новым паролем.
        </p>
      )}
      <FormError message={error} />
      <Field label="Email" id="email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field label="Пароль" id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      <SubmitButton busy={busy}>Войти</SubmitButton>
    </form>
  )
}

// Подвал под формой — по режиму регистрации; почты пока нет (шаг 8.3a): забытый пароль сбрасывает администратор
function Footer({ mode }: { mode: RegistrationMode | null }) {
  return (
    <>
      {mode === 'open' && (
        <>
          Нет аккаунта?{' '}
          <Link href="/register" className="text-accent">
            Зарегистрироваться
          </Link>
        </>
      )}
      {mode === 'invite' && (
        <>
          Есть приглашение?{' '}
          <Link href="/register" className="text-accent">
            Зарегистрироваться
          </Link>
        </>
      )}
      {mode === 'closed' && <>Нет аккаунта? Доступ выдаёт администратор сервиса.</>}
      <span className="block mt-2 text-xs">Забыли пароль? Новый выдаёт администратор сервиса.</span>
    </>
  )
}

export default function LoginPage() {
  const mode = useRegistrationMode()
  return (
    <AuthCard title="Вход" footer={<Footer mode={mode} />}>
      <Suspense>
        <LoginForm />
      </Suspense>
    </AuthCard>
  )
}
