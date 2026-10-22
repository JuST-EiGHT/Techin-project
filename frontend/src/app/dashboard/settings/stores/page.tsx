'use client'

// Магазины аккаунта (шаг 8.4b): у каждого — свой кабинет Ozon, свои товары, деньги, настройки и планирование.
import { useState } from 'react'

import { Field, FormError, SubmitButton } from '@/components/AuthForm'
import { openStore, type Store, useStores } from '@/contexts/StoreContext'
import { api, errorMessage } from '@/lib/api'

const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('ru-RU')

function KeysNote({ s }: { s: Store }) {
  if (s.keys === 0) return <span className="text-warn">Ключа Ozon нет — данные не загружаются</span>
  if (s.active_keys === 0) return <span className="text-bad">Ключ не работает или истёк — замените его в «API-ключах Ozon»</span>
  return <span className="text-muted">Ключей: {s.keys}, работают: {s.active_keys}</span>
}

function StoreRow({ s, current, onChanged }: { s: Store; current: boolean; onChanged: () => Promise<unknown> }) {
  const [editing, setEditing] = useState(false)
  const [name, setName] = useState(s.name)
  const [confirming, setConfirming] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function patch(body: { name?: string; is_active?: boolean }) {
    setBusy(true)
    setError(null)
    try {
      await api.patch(`/stores/${s.id}`, body)
      await onChanged()
      setEditing(false)
      setConfirming(false)
      // отключили магазин, который сейчас открыт, — к первому магазину
      if (body.is_active === false && current) window.location.reload()
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="border-t border-line py-4 first:border-t-0" data-testid="store-row">
      <div className="flex flex-wrap items-start gap-4">
        <div className="min-w-0 flex-1">
          {editing ? (
            <form
              className="flex flex-wrap items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault()
                patch({ name })
              }}
            >
              <input
                aria-label="Название магазина"
                value={name}
                maxLength={100}
                autoFocus
                onChange={(e) => setName(e.target.value)}
                className="min-w-0 flex-1 rounded-lg border border-line bg-bg px-3 py-1.5 outline-none focus:border-accent"
              />
              <button type="submit" disabled={busy} className="rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60">
                Сохранить
              </button>
              <button type="button" onClick={() => { setEditing(false); setName(s.name) }} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-muted">
                Отмена
              </button>
            </form>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{s.name}</span>
              {current && <span className="rounded bg-accent/10 px-2 py-0.5 text-xs text-accent">Открыт сейчас</span>}
              {s.is_main && <span className="rounded bg-line/60 px-2 py-0.5 text-xs text-muted">Первый — аккаунт</span>}
              {!s.is_active && <span className="rounded bg-bad/10 px-2 py-0.5 text-xs text-bad">Отключён</span>}
              {s.unread_alerts > 0 && <span className="rounded bg-warn/10 px-2 py-0.5 text-xs text-warn">Уведомлений без «Ок»: {s.unread_alerts}</span>}
            </div>
          )}
          <div className="mt-1 text-sm text-muted">
            {s.client_id_masked ? `Кабинет Ozon: Client-Id ${s.client_id_masked}` : 'Кабинет Ozon задаст первый добавленный ключ'}
          </div>
          <div className="mt-1 text-sm"><KeysNote s={s} /></div>
          <div className="mt-1 text-xs text-faint">Добавлен {fmtDate(s.created_at)}</div>
          {error && <div className="mt-2 text-sm text-bad">{error}</div>}
          {confirming && (
            <div className="mt-3 rounded-lg border border-line p-3 text-sm">
              <p className="mb-2">
                Отключить «{s.name}»? Данные сохранятся, но загрузка из Ozon и банков остановится, а магазин пропадёт из
                переключателя. Вернуть можно здесь же.
              </p>
              <div className="flex gap-2">
                <button type="button" disabled={busy} onClick={() => patch({ is_active: false })} className="rounded-lg bg-bad px-3 py-1.5 font-medium text-white hover:opacity-90 disabled:opacity-60">
                  {busy ? 'Отключаю…' : 'Да, отключить'}
                </button>
                <button type="button" onClick={() => setConfirming(false)} className="rounded-lg border border-line px-3 py-1.5 hover:border-muted">
                  Отмена
                </button>
              </div>
            </div>
          )}
        </div>

        {!editing && !confirming && (
          <div className="flex flex-wrap gap-2">
            {s.is_active && !current && (
              <button type="button" onClick={() => openStore(s.id, '/dashboard')} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent">
                Открыть
              </button>
            )}
            <button type="button" onClick={() => setEditing(true)} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-accent hover:text-accent">
              Переименовать
            </button>
            {!s.is_main &&
              (s.is_active ? (
                <button type="button" onClick={() => setConfirming(true)} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-bad hover:text-bad">
                  Отключить
                </button>
              ) : (
                <button type="button" disabled={busy} onClick={() => patch({ is_active: true })} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:border-ok hover:text-ok disabled:opacity-60">
                  Вернуть
                </button>
              ))}
          </div>
        )}
      </div>
    </li>
  )
}

const EMPTY = { name: '', client_id: '', api_key: '' }

function AddStore({ onAdded }: { onAdded: (s: Store) => void }) {
  const [form, setForm] = useState(EMPTY)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const set = (key: keyof typeof EMPTY) => (e: React.ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [key]: e.target.value }))

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    if (!form.client_id.trim() || !form.api_key.trim()) {
      setError('Укажите Client-Id и Api-Key кабинета Ozon этого магазина')
      return
    }
    setBusy(true)
    try {
      const { data } = await api.post<Store>('/stores', {
        name: form.name.trim() || null,
        client_id: form.client_id.trim(),
        api_key: form.api_key.trim(),
      })
      setForm(EMPTY) // ключ не держим в памяти страницы дольше нужного
      onAdded(data)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={onSubmit} noValidate className="rounded-xl border border-line bg-card p-6" data-testid="add-store">
      <h2 className="mb-1 text-lg font-semibold">Добавить магазин</h2>
      <p className="mb-5 text-sm text-muted">
        Нужен ключ Seller API кабинета этого магазина (в кабинете Ozon: Настройки → Seller API). Ключ проверяется в Ozon и
        хранится только зашифрованным; сразу начнут загружаться товары, остатки, акции, начисления и выплаты за 90 дней.
        Себестоимость, налоги и планирование нового магазина настраиваются отдельно — у разных магазинов они бывают разными.
      </p>
      <FormError message={error} />
      <div className="grid gap-x-4 sm:grid-cols-3">
        <Field label="Название (видно только вам)" id="store_name" placeholder="Например, «Подшипники Юг»" value={form.name} onChange={set('name')} maxLength={100} />
        <Field label="Client-Id" id="store_client_id" inputMode="numeric" autoComplete="off" required value={form.client_id} onChange={set('client_id')} />
        <Field label="Api-Key" id="store_api_key" type="password" autoComplete="off" required value={form.api_key} onChange={set('api_key')} />
      </div>
      <div className="sm:w-64">
        <SubmitButton busy={busy}>Проверить и добавить</SubmitButton>
      </div>
    </form>
  )
}

export default function StoresPage() {
  const { stores, current, reload } = useStores()
  const [added, setAdded] = useState<Store | null>(null)

  return (
    <div className="max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Магазины</h1>
        <p className="mt-1 text-sm text-muted">
          Каждый магазин — отдельный кабинет Ozon: свои товары, остатки, деньги, отчёты, себестоимость, налоги и
          планирование. Данные магазинов не смешиваются, переключаться — в полосе под шапкой сайта. Профиль, пароль, тема и
          Telegram — общие для аккаунта: уведомления всех магазинов приходят в один чат с названием магазина.
        </p>
      </div>

      <section className="rounded-xl border border-line bg-card p-6">
        <h2 className="mb-2 text-lg font-semibold">Ваши магазины</h2>
        {added && (
          <div className="mb-3 flex flex-wrap items-center gap-3 rounded-lg border border-ok/40 bg-ok/10 px-3 py-2 text-sm text-ok" data-testid="store-added">
            <span>Магазин «{added.name}» добавлен, данные загружаются.</span>
            <button type="button" onClick={() => openStore(added.id, '/dashboard')} className="rounded-lg bg-accent px-3 py-1 font-medium text-white hover:opacity-90">
              Перейти в магазин
            </button>
          </div>
        )}
        {stores === null && <p className="text-sm text-muted">Загрузка…</p>}
        {stores && (
          <ul>
            {stores.map((s) => (
              <StoreRow key={s.id} s={s} current={current?.id === s.id} onChanged={reload} />
            ))}
          </ul>
        )}
      </section>

      <AddStore
        onAdded={(s) => {
          setAdded(s)
          reload()
        }}
      />
    </div>
  )
}
