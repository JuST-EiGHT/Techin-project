'use client'
// Меню аккаунта → «Уведомления» (шаги 7.3, 10.25): каналы (в сервисе — всегда, Telegram, e-mail — после почты), разделы
// по карте сервиса — уведомлять или нет, Telegram, важность, сразу или сводкой раз в день; пороги предупреждений —
// у своего раздела (переехали сюда из «Настроек», значения те же); сводка, тихие часы, часовой пояс. Подключение бота,
// тестовое сообщение и отключение — в «Интеграциях» → «Telegram» (components/settings/Telegram.tsx).
import Link from 'next/link'
import { useCallback, useEffect, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { type NotifySection, type NotifySettings, type SectionKey, TELEGRAM_HREF } from '@/components/settings/Telegram'
import { api, errorMessage } from '@/lib/api'

type SectionPrefs = Pick<NotifySection, 'on' | 'telegram' | 'severity' | 'daily'>
type Prefs = {
  sections: Record<string, SectionPrefs>
  digest_at: string
  quiet_enabled: boolean
  quiet_from: string
  quiet_to: string
  timezone: string
}
type Threshold = { key: string; label: string; unit: string; why: string; value: string; default: string; min: string; max: string; places: number }
type Thresholds = { sections: { section: SectionKey; note: string | null; items: Threshold[] }[]; defaults: boolean; stock: { critical: number; low: number; excess: number } }

const TIMES = Array.from({ length: 48 }, (_, i) => `${String(Math.floor(i / 2)).padStart(2, '0')}:${i % 2 ? '30' : '00'}`)
const select = 'rounded-lg border border-line bg-bg px-3 py-1.5 text-sm disabled:opacity-60'
const decimalText = (v: string | number) => String(Number(v)).replace('.', ',')

function Block({ title, hint, children, id, testid }: { title: string; hint?: React.ReactNode; children: React.ReactNode; id?: string; testid?: string }) {
  return (
    <section id={id} className="scroll-mt-20 rounded-xl border border-line bg-card p-5" data-testid={testid}>
      <h2 className="text-lg font-semibold">{title}</h2>
      {hint && <p className="mt-1 text-sm text-muted">{hint}</p>}
      <div className="mt-4">{children}</div>
    </section>
  )
}

function Switch({ on, onChange, label, testid, disabled }: { on: boolean; onChange: (v: boolean) => void; label: string; testid: string; disabled?: boolean }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition disabled:opacity-50 ${on ? 'bg-accent' : 'bg-line'}`}
      data-testid={testid}
    >
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-card shadow transition ${on ? 'left-[1.375rem]' : 'left-0.5'}`} />
    </button>
  )
}

function Channels({ data }: { data: NotifySettings }) {
  return (
    <div className="grid gap-3 sm:grid-cols-3" data-testid="channels">
      {data.channels.map((c) => (
        <div key={c.key} className={`rounded-lg border p-3 ${c.connected ? 'border-ok/40' : 'border-line'}`} data-testid={`channel-${c.key}`}>
          <div className="flex items-baseline gap-2">
            <span className="font-medium">{c.title}</span>
            <span className={`ml-auto text-xs ${c.connected ? 'text-ok' : c.available ? 'text-muted' : 'text-faint'}`}>
              {c.key === 'service' ? 'всегда' : c.connected ? 'подключён' : c.available ? 'не подключён' : c.key === 'email' ? 'скоро' : 'нет на сервере'}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted">{c.note}</p>
          {c.key === 'telegram' && c.available && (
            <Link href={TELEGRAM_HREF} className="mt-1 inline-block text-xs text-accent hover:underline" data-testid="tg-integrations">
              {c.connected ? 'Тестовое сообщение, отключить →' : 'Подключить в «Интеграциях» →'}
            </Link>
          )}
          {c.key === 'telegram' && data.blocked && <p className="mt-1 text-xs text-warn">Бот не может вам написать — запустите его в Telegram снова.</p>}
        </div>
      ))}
    </div>
  )
}

function ThresholdInput({ t, value, onChange }: { t: Threshold; value: string; onChange: (v: string) => void }) {
  const changed = Number(value.replace(',', '.')) !== Number(t.default)
  return (
    <div>
      <label htmlFor={t.key} className="block text-sm text-muted">
        {t.label}
      </label>
      <div className="mt-1 flex items-center gap-2">
        <input
          id={t.key}
          inputMode={t.places ? 'decimal' : 'numeric'}
          value={value}
          onChange={(e) => onChange(e.target.value.replace(t.places ? /[^\d.,]/g : /\D/g, ''))}
          className="w-20 rounded-lg border border-line bg-bg px-3 py-1.5 text-sm outline-none focus:border-accent"
          data-testid={`threshold-${t.key}`}
        />
        <span className="text-sm text-muted">{t.unit}</span>
        {changed && <span className="text-xs text-faint">по умолчанию {decimalText(t.default)}</span>}
      </div>
      <p className="mt-1 text-xs text-faint">{t.why}</p>
    </div>
  )
}

export default function NotificationsSettingsPage() {
  const [data, setData] = useState<NotifySettings | null>(null)
  const [prefs, setPrefs] = useState<Prefs | null>(null)
  const [limits, setLimits] = useState<Thresholds | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)
  const [busy, setBusy] = useState(false)

  const apply = useCallback((s: NotifySettings) => {
    setData(s)
    setPrefs({
      sections: Object.fromEntries(s.sections.map((x) => [x.key, { on: x.on, telegram: x.telegram, severity: x.severity, daily: x.daily }])),
      digest_at: s.digest_at,
      quiet_enabled: s.quiet_enabled,
      quiet_from: s.quiet_from,
      quiet_to: s.quiet_to,
      timezone: s.timezone,
    })
  }, [])
  const applyLimits = useCallback((t: Thresholds) => {
    setLimits(t)
    setValues(Object.fromEntries(t.sections.flatMap((s) => s.items.map((i) => [i.key, decimalText(i.value)]))))
  }, [])

  useEffect(() => {
    Promise.all([api.get<NotifySettings>('/notifications/settings'), api.get<Thresholds>('/notifications/thresholds')])
      .then(([s, t]) => {
        apply(s.data)
        applyLimits(t.data)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [apply, applyLimits])

  // из ссылки «Пороги →» (#thresholds) — к разделам, когда они загрузились
  useEffect(() => {
    if (data && window.location.hash === '#thresholds') document.getElementById('thresholds')?.scrollIntoView({ block: 'start' })
  }, [data])

  const touch = () => setSaved(false)
  const setSection = (key: string, patch: Partial<SectionPrefs>) => {
    touch()
    setPrefs((p) => p && { ...p, sections: { ...p.sections, [key]: { ...p.sections[key], ...patch } } })
  }
  const set = <K extends keyof Prefs>(key: K, value: Prefs[K]) => {
    touch()
    setPrefs((p) => p && { ...p, [key]: value })
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    if (!prefs || !limits) return
    setError(null)
    setSaved(false)
    // пороги — только изменённые; проверка формата здесь, границы и «жёлтый меньше красного» — на сервере
    const changed: Record<string, string> = {}
    for (const t of limits.sections.flatMap((s) => s.items)) {
      const raw = (values[t.key] ?? '').trim().replace(',', '.')
      if (raw === '' || !Number.isFinite(Number(raw))) return setError(`«${t.label}» — укажите число`)
      if (Number(raw) !== Number(t.value)) changed[t.key] = raw
    }
    setBusy(true)
    try {
      const sections = Object.fromEntries(Object.entries(prefs.sections).filter(([k]) => !data?.sections.find((x) => x.key === k)?.soon))
      apply((await api.patch<NotifySettings>('/notifications/settings', { ...prefs, sections })).data)
      if (Object.keys(changed).length) applyLimits((await api.patch<Thresholds>('/notifications/thresholds', { values: changed })).data)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  async function resetLimits() {
    setError(null)
    setBusy(true)
    try {
      applyLimits((await api.patch<Thresholds>('/notifications/thresholds', { reset: true })).data)
      setSaved(true)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const tgOn = data?.channels.find((c) => c.key === 'telegram')
  const anyDaily = prefs ? Object.values(prefs.sections).some((s) => s.on && s.telegram && s.daily) : false
  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-semibold">Уведомления</h1>
        <p className="mt-1 text-sm text-muted">
          Раз в час сервис проверяет продажи, цены, остатки, акции, выплаты и данные. Здесь — какие разделы уведомлять, куда и как часто, и пороги: когда
          считать это проблемой.{' '}
          <Link href="/dashboard/alerts" className="text-accent hover:underline">
            Все уведомления →
          </Link>
        </p>
      </div>
      <FormError message={error} />
      {!data && !error && <p className="text-sm text-muted">Загружаем…</p>}

      {data && (
        <Block title="Каналы" hint="В сервисе уведомления видны всегда. Telegram — чтобы не держать сайт открытым; e-mail появится, когда подключим почту.">
          <Channels data={data} />
        </Block>
      )}

      {data && prefs && limits && (
        <form onSubmit={save} className="space-y-6" data-testid="notify-prefs">
          <Block
            id="thresholds"
            testid="notify-sections"
            title="Разделы и пороги"
            hint={
              <>
                Выключенный раздел не тревожит: его нет в колокольчике, «Требует внимания» и Telegram, а на странице «Уведомления» он виден с пометкой. Пороги —
                для текущего магазина; раньше они были в «Настройках», значения те же.
              </>
            }
          >
            <div className="space-y-4">
              {data.sections.map((x) => {
                const sp = prefs.sections[x.key]
                const group = limits.sections.find((g) => g.section === x.key)
                if (x.soon)
                  return (
                    <div key={x.key} className="rounded-lg border border-dashed border-line p-4 text-sm" data-testid={`section-${x.key}`}>
                      <div className="flex items-baseline gap-2">
                        <span className="font-medium">{x.title}</span>
                        <span className="ml-auto text-xs text-faint">скоро</span>
                      </div>
                      <p className="mt-1 text-xs text-faint">{x.hint} Появится вместе с автоматизацией (шаги {x.soon}).</p>
                    </div>
                  )
                return (
                  <div key={x.key} className={`rounded-lg border border-line p-4 ${sp.on ? '' : 'opacity-80'}`} data-testid={`section-${x.key}`} data-on={sp.on}>
                    <div className="flex items-start gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">{x.title}</div>
                        <p className="mt-0.5 text-xs text-muted">{x.hint}</p>
                      </div>
                      <Switch on={sp.on} onChange={(v) => setSection(x.key, { on: v })} label={`Уведомлять: ${x.title}`} testid={`section-on-${x.key}`} />
                    </div>
                    {sp.on && (
                      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
                        <label className="flex cursor-pointer items-center gap-2" title={tgOn?.connected ? undefined : 'Telegram не подключён — настройка сработает, когда подключите'}>
                          <input type="checkbox" checked={sp.telegram} onChange={(e) => setSection(x.key, { telegram: e.target.checked })} className="accent-accent" data-testid={`section-tg-${x.key}`} />
                          Telegram
                        </label>
                        {x.severities.length > 1 && (
                          <select
                            value={sp.severity}
                            disabled={!sp.telegram}
                            onChange={(e) => setSection(x.key, { severity: e.target.value as SectionPrefs['severity'] })}
                            className={select}
                            aria-label={`Важность в Telegram: ${x.title}`}
                            data-testid={`section-severity-${x.key}`}
                          >
                            <option value="all">красные и жёлтые</option>
                            <option value="red">только красные</option>
                          </select>
                        )}
                        <select
                          value={sp.daily ? 'daily' : 'now'}
                          disabled={!sp.telegram}
                          onChange={(e) => setSection(x.key, { daily: e.target.value === 'daily' })}
                          className={select}
                          aria-label={`Как часто в Telegram: ${x.title}`}
                          data-testid={`section-daily-${x.key}`}
                        >
                          <option value="now">сразу</option>
                          <option value="daily">сводкой раз в день</option>
                        </select>
                        <span className="text-xs text-faint" title="Появится, когда подключим почту">
                          E-mail — скоро
                        </span>
                      </div>
                    )}
                    {(group?.items.length || group?.note || x.key === 'stock') && (
                      <div className="mt-3 border-t border-line pt-3">
                        {group?.note && <p className="mb-2 text-xs text-faint">{group.note}</p>}
                        {x.key === 'stock' && (
                          <p className="mb-2 text-xs text-muted" data-testid="stock-thresholds">
                            Сейчас: «Критично» — меньше {limits.stock.critical} дн., «Мало» — меньше {limits.stock.low} дн., «Избыток» — больше {limits.stock.excess} дн.{' '}
                            <Link href="/dashboard/settings#stock" className="text-accent hover:underline">
                              Изменить в «Настройках» →
                            </Link>
                          </p>
                        )}
                        {group && group.items.length > 0 && (
                          <div className="grid gap-3 sm:grid-cols-3">
                            {group.items.map((t) => (
                              <ThresholdInput
                                key={t.key}
                                t={t}
                                value={values[t.key] ?? ''}
                                onChange={(v) => {
                                  touch()
                                  setValues((old) => ({ ...old, [t.key]: v }))
                                }}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
            {!limits.defaults && (
              <button type="button" disabled={busy} onClick={resetLimits} className="mt-3 text-sm text-accent hover:underline disabled:opacity-60" data-testid="thresholds-reset">
                Вернуть пороги по умолчанию
              </button>
            )}
          </Block>

          <Block title="Сводка и тихие часы" hint="Разделы «сводкой раз в день» приходят одним сообщением в выбранное время. В тихие часы сообщения не приходят — соберём их и пришлём после. Тестовое сообщение приходит всегда.">
            <div className="flex flex-wrap items-end gap-3 text-sm">
              <label className="block">
                <span className="block text-muted">Сводка раз в день — в</span>
                <select value={prefs.digest_at} onChange={(e) => set('digest_at', e.target.value)} className={`mt-1 ${select}`} data-testid="pref-digest">
                  {TIMES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              {!anyDaily && <span className="pb-2 text-xs text-faint">сейчас ни один раздел не приходит сводкой</span>}
            </div>
            <label className="mt-4 flex cursor-pointer items-center gap-3 text-sm">
              <input type="checkbox" checked={prefs.quiet_enabled} onChange={(e) => set('quiet_enabled', e.target.checked)} className="accent-accent" data-testid="pref-quiet" />
              Тихие часы — не присылать в это время
            </label>
            <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
              <label className="block">
                <span className="block text-muted">с</span>
                <select value={prefs.quiet_from} disabled={!prefs.quiet_enabled} onChange={(e) => set('quiet_from', e.target.value)} className={`mt-1 ${select}`} data-testid="pref-quiet-from">
                  {TIMES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
              <label className="block">
                <span className="block text-muted">до</span>
                <select value={prefs.quiet_to} disabled={!prefs.quiet_enabled} onChange={(e) => set('quiet_to', e.target.value)} className={`mt-1 ${select}`} data-testid="pref-quiet-to">
                  {TIMES.map((t) => (
                    <option key={t}>{t}</option>
                  ))}
                </select>
              </label>
            </div>
            <label className="mt-3 block text-sm">
              <span className="block text-muted">Часовой пояс — сводку и тихие часы считаем по нему</span>
              <select value={prefs.timezone} onChange={(e) => set('timezone', e.target.value)} className={`mt-1 w-full max-w-xs ${select}`} data-testid="pref-timezone">
                {data.timezones.map((t) => (
                  <option key={t.value} value={t.value}>
                    {t.label}
                  </option>
                ))}
              </select>
            </label>
          </Block>

          <div className="sticky bottom-0 -mx-1 flex flex-wrap items-center gap-3 bg-bg/90 px-1 py-3 backdrop-blur">
            <button type="submit" disabled={busy} className="rounded-lg bg-accent px-4 py-1.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60" data-testid="prefs-save">
              Сохранить
            </button>
            {saved && (
              <span className="text-sm text-ok" data-testid="prefs-saved">
                Сохранено — маржа на страницах сразу, уведомления — при следующей проверке
              </span>
            )}
          </div>
        </form>
      )}
    </div>
  )
}
