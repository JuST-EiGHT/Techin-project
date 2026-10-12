import './globals.css'
export const metadata = { title: 'Ozon Analytics' }
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ru"><body>{children}</body></html>
}
