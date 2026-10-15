// Общие элементы страниц входа и регистрации
import Link from 'next/link'

export function AuthCard({ title, children, footer }: { title: string; children: React.ReactNode; footer: React.ReactNode }) {
  return (
    <main className="min-h-screen flex items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <Link href="/" className="block text-center text-sm text-muted mb-6">
          Ozon Analytics
        </Link>
        <div className="bg-card border border-line rounded-xl p-6">
          <h1 className="text-xl font-semibold mb-5">{title}</h1>
          {children}
        </div>
        <p className="text-center text-sm text-muted mt-4">{footer}</p>
      </div>
    </main>
  )
}

type FieldProps = React.InputHTMLAttributes<HTMLInputElement> & { label: string }

export function Field({ label, id, ...props }: FieldProps) {
  return (
    <label htmlFor={id} className="block mb-4">
      <span className="block text-sm text-muted mb-1">{label}</span>
      <input
        id={id}
        className="w-full rounded-lg bg-bg border border-line px-3 py-2 outline-none focus:border-accent disabled:opacity-60"
        {...props}
      />
    </label>
  )
}

export function FormError({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p role="alert" className="mb-4 rounded-lg border border-bad/40 bg-bad/10 px-3 py-2 text-sm text-bad">
      {message}
    </p>
  )
}

export function SubmitButton({ busy, children }: { busy: boolean; children: React.ReactNode }) {
  return (
    <button
      type="submit"
      disabled={busy}
      className="w-full rounded-lg bg-accent py-2 font-medium text-white hover:opacity-90 disabled:opacity-60"
    >
      {busy ? 'Подождите…' : children}
    </button>
  )
}
