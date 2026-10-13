import './globals.css'

import { AuthProvider } from '@/contexts/AuthContext'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { THEME_INIT_SCRIPT } from '@/lib/theme'

export const metadata = { title: 'Ozon Analytics' }

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    // data-theme ставит скрипт до отрисовки — поэтому атрибут на сервере и в браузере расходится
    <html lang="ru" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_INIT_SCRIPT }} />
      </head>
      <body>
        <AuthProvider>
          <ThemeProvider>{children}</ThemeProvider>
        </AuthProvider>
      </body>
    </html>
  )
}
