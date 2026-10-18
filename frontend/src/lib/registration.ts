'use client'

// Кто может зарегистрироваться (шаг 8.4): closed — никто, аккаунт создаёт администратор; invite — по коду
// приглашения; open — любой. Режим задаёт сервер (REGISTRATION_MODE), страницы входа и регистрации подстраиваются.
import { useEffect, useState } from 'react'

import { api } from '@/lib/api'

export type RegistrationMode = 'closed' | 'invite' | 'open'

/** null — ещё загружается (или сервер не ответил: тогда ссылку на регистрацию не показываем). */
export function useRegistrationMode(): RegistrationMode | null {
  const [mode, setMode] = useState<RegistrationMode | null>(null)
  useEffect(() => {
    api
      .get<{ mode: RegistrationMode }>('/auth/registration', { skipAuthRedirect: true })
      .then((r) => setMode(r.data.mode))
      .catch(() => setMode(null))
  }, [])
  return mode
}
