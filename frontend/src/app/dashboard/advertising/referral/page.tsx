'use client'
// «Продвижение → Реферальная программа» (шаг 11.7): участники и их личные ссылки — components/referral.tsx.
import { Suspense } from 'react'

import { ReferralPage } from '@/components/referral'

export default function Page() {
  return (
    <Suspense>
      <ReferralPage />
    </Suspense>
  )
}
