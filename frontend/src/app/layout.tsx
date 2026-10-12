import './globals.css'

import { AuthProvider } from '@/contexts/AuthContext'

export const metadata = { title: 'Ozon Analytics' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  )
}
