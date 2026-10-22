'use client'
// Ключи Ozon текущего магазина (шаги 0.4, 8.4b). С шага 10.1 — вкладка «Ozon» страницы «Интеграции»
// (прежняя страница «API-ключи Ozon» переадресуется сюда).
import axios from 'axios'
import { useCallback, useEffect, useState } from 'react'

import { Field, FormError, SubmitButton } from '@/components/AuthForm'
import { notifyCredentialsChanged } from '@/components/SyncStatus'
import { openStore, type Store, useStores } from '@/contexts/StoreContext'
import { api, errorMessage } from '@/lib/api'

type Credential = {
  id: string
  label: string
  client_id_masked: string
  is_active: boolean
  expires_at: string
  days_until_expiry: number
  last_used_at: string | null
  created_at: string
}

type VerifyResponse = { ok: boolean; message: string; credential: Credential }

const EXPIRY_WARN_DAYS = 30

const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU')
const fmtDateTime = (iso: string) =>
  new Date(iso).toLocaleString('ru-RU', { dateStyle: 'short', timeStyle: 'short' })

function ExpiryNote({ days, expiresAt }: { days: number; expiresAt: string }) {
  if (days < 0) return <span className="text-bad">Срок ключа истёк {fmtDate(expiresAt)} — создайте новый в кабинете Ozon</span>
  if (days < EXPIRY_WARN_DAYS)
    return (
      <span className="text-warn">
        Истекает через {days} дн. ({fmtDate(expiresAt)}) — заранее создайте новый ключ в кабинете Ozon
      </span>
    )
  return <span className="text-muted">Действует до {fmtDate(expiresAt)}</span>
}

function CredentialRow({ cred, onChanged, onDeleted }: { cred: Credential; onChanged: (c: Credential) => void; onDeleted: (id: string) => void }) {
  const [busy, setBusy] = useState<'verify' | 'delete' | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [result, setResult] = useState<{ ok: boolean; text: string } | null>(null)

  async function verify() {
    setBusy('verify')
    setResult(null)
    try {
      const { data } = await api.post<VerifyResponse>(`/credentials/${cred.id}/verify`)
      onChanged(data.credential)
      setResult({ ok: data.ok, text: data.message })
      notifyCredentialsChanged()
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
    } finally {
      setBusy(null)
    }
  }

  async function remove() {
    setBusy('delete')
    try {
      await api.delete(`/credentials/${cred.id}`)
      onDeleted(cred.id)
      notifyCredentialsChanged()
    } catch (err) {
      setResult({ ok: false, text: errorMessage(err) })
      setBusy(null)
      setConfirming(false)
    }
  }

  return (
    <li className="border-t border-line py-4 first:border-t-0" data-testid="credential">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="font-medium">{cred.label}</span>
            <span
              className={`rounded px-2 py-0.5 text-xs ${cred.is_active ? 'bg-ok/10 text-ok' : 'bg-bad/10 text-bad'}`}
            >
              {cred.is_active ? 'Работает' : 'Не работает'}
            </span>
          </div>
          <div className="mt-1 text-sm text-muted">Client-Id {cred.client_id_masked}</div>
          <div className="mt-1 text-sm">
            <ExpiryNote days={cred.days_until_expiry} expiresAt={cred.expires_at} />
          </div>
          {cred.last_used_at && (
            <div className="mt-1 text-xs text-faint">Последнее использование: {fmtDateTime(cred.last_used_at)}</div>
          )}
          {result && <div className={`mt-2 text-sm ${result.ok ? 'text-ok' : 'text-bad'}`}>{result.text}</div>}
        </div>

        <div className="flex gap-2">
          {confirming ? (
            <>
              <span className="self-center text-sm text-muted">Удалить ключ?</span>
              <button
                onClick={remove}
                disabled={busy !== null}
                className="rounded-lg bg-bad px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              >
                {busy === 'delete' ? 'Удаляю…' : 'Да, удалить'}
              </button>
              <button
                onClick={() => setConfirming(false)}
                disabled={busy !== null}
                className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-muted"
              >
                Отмена
              </button>
            </>
          ) : (
            <>
              <button
                onClick={verify}
                disabled={busy !== null}
                className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent disabled:opacity-60"
              >
                {busy === 'verify' ? 'Проверяю…' : 'Проверить'}
              </button>
              <button
                onClick={() => setConfirming(true)}
                disabled={busy !== null}
                className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad disabled:opacity-60"
              >
                Удалить
              </button>
            </>
          )}
        </div>
      </div>
    </li>
  )
}

const EMPTY_FORM = { label: '', client_id: '', api_key: '', expires_at: '' }

function AddForm({ onAdded }: { onAdded: (c: Credential) => void }) {
  const [form, setForm] = useState(EMPTY_FORM)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [otherCabinet, setOtherCabinet] = useState(false) // ключ от другого кабинета Ozon — предложить отдельный магазин

  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [key]: e.target.value }))

  const expiresAt = () => (form.expires_at ? new Date(`${form.expires_at}T23:59:59`).toISOString() : null)

  async function addAsStore() {
    setBusy(true)
    setError(null)
    try {
      const { data } = await api.post<Store>('/stores', {
        name: form.label.trim() || null,
        client_id: form.client_id.trim(),
        api_key: form.api_key.trim(),
        expires_at: expiresAt(),
      })
      setForm(EMPTY_FORM)
      openStore(data.id, '/dashboard/settings/integrations')
    } catch (err) {
      setError(errorMessage(err))
      setBusy(false)
    }
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setOtherCabinet(false)
    if (!form.client_id.trim() || !form.api_key.trim()) {
      setError('Укажите Client-Id и Api-Key')
      return
    }
    setBusy(true)
    try {
      const { data } = await api.post<Credential>('/credentials', {
        label: form.label.trim() || null,
        client_id: form.client_id.trim(),
        api_key: form.api_key.trim(),
        expires_at: expiresAt(), // дата из поля — конец дня по местному времени
      })
      setForm(EMPTY_FORM) // ключ не держим в памяти страницы дольше нужного
      onAdded(data)
      notifyCredentialsChanged()
    } catch (err) {
      setError(errorMessage(err))
      setOtherCabinet(axios.isAxiosError(err) && err.response?.status === 409)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="bg-card border border-line rounded-xl p-6">
      <h2 className="text-lg font-semibold mb-1">Добавить ключ</h2>
      <p className="text-sm text-muted mb-5">
        Ключ создаётся в кабинете Ozon: Настройки → Seller API, живёт 3 месяца. Для аналитики хватает прав на чтение. Перед сохранением
        ключ проверяется запросом к Ozon и хранится только в зашифрованном виде.
      </p>
      <FormError message={error} />
      {otherCabinet && (
        <div className="-mt-2 mb-4" data-testid="add-as-store">
          <button
            type="button"
            onClick={addAsStore}
            disabled={busy}
            className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
          >
            {busy ? 'Добавляю…' : 'Добавить отдельным магазином'}
          </button>
        </div>
      )}
      <div className="grid gap-x-4 sm:grid-cols-2">
        <Field label="Название" id="label" placeholder="Магазин Ozon" value={form.label} onChange={set('label')} />
        <Field label="Client-Id" id="client_id" inputMode="numeric" autoComplete="off" required value={form.client_id} onChange={set('client_id')} />
        <Field label="Api-Key" id="api_key" type="password" autoComplete="off" required value={form.api_key} onChange={set('api_key')} />
        <Field
          label="Действует до (необязательно — обычно Ozon сообщает срок сам)"
          id="expires_at"
          type="date"
          value={form.expires_at}
          onChange={set('expires_at')}
        />
      </div>
      <div className="sm:w-64">
        <SubmitButton busy={busy}>Проверить и сохранить</SubmitButton>
      </div>
    </form>
  )
}

export function OzonKeysPanel() {
  const { current, active } = useStores()
  const [items, setItems] = useState<Credential[] | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Credential[]>('/credentials')
      setItems(data)
      setLoadError(null)
    } catch (err) {
      setLoadError(errorMessage(err))
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const replace = (c: Credential) => setItems((xs) => xs?.map((x) => (x.id === c.id ? c : x)) ?? null)
  const removeItem = (id: string) => setItems((xs) => xs?.filter((x) => x.id !== id) ?? null)

  return (
    <div className="space-y-6" data-testid="ozon-keys">
      <div>
        <h2 className="text-lg font-semibold">Ключи Ozon{active.length > 1 && current ? ` магазина «${current.name}»` : ''}</h2>
        <p className="mt-1 text-sm text-muted">
          Один магазин — один кабинет Ozon: ключи для замены (срок ключа — 3 месяца) добавляйте сюда, а ключ другого
          кабинета — <a href="#stores" className="text-accent hover:underline">отдельным магазином</a>,
          чтобы товары и деньги разных магазинов не смешались.
        </p>
      </div>

      <section className="bg-card border border-line rounded-xl p-6">
        <h2 className="text-lg font-semibold mb-2">Подключённые ключи</h2>
        <FormError message={loadError} />
        {items === null && !loadError && <p className="text-muted text-sm">Загрузка…</p>}
        {items?.length === 0 && <p className="text-muted text-sm">Ключей пока нет — добавьте первый ниже.</p>}
        {items && items.length > 0 && (
          <ul>
            {items.map((c) => (
              <CredentialRow key={c.id} cred={c} onChanged={replace} onDeleted={removeItem} />
            ))}
          </ul>
        )}
      </section>

      <AddForm onAdded={(c) => setItems((xs) => [...(xs ?? []), c])} />
    </div>
  )
}
