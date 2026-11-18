'use client'

// «Продвижение → Ссылки с UTM» (шаг 11.1): префикс для метки UTM_CAMPAIGN и конструктор ссылок «Внешнего трафика» Ozon.
// API для внешнего трафика у Ozon нет: ссылки собирает сервис по правилам Ozon (backend engine/traffic_links), статистика
// придёт из отчётов Ozon (шаги 11.3–11.4). Ссылки пока не сохраняются — история появится в шаге 11.2.
import { Check, ChevronsUpDown, Copy, ExternalLink, FileSpreadsheet, Link2, QrCode, Tag, X } from 'lucide-react'
import Link from 'next/link'
import { usePathname, useRouter, useSearchParams } from 'next/navigation'
import { useEffect, useId, useMemo, useRef, useState } from 'react'

import { CopyText, writeText } from '@/components/copy'
import { SearchBox, selectClass } from '@/components/filters'
import { Modal, input } from '@/components/planning/ui'
import { SortTh, useSort } from '@/components/table'
import { LinkHistory } from '@/components/traffic-history'
import { Badge, Button, EmptyState, HelpTip, PageHeader, Panel, Segmented, Skeleton, Sub, Tabs, buttonClass } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import {
  type BuildRequest,
  type BuildResult,
  type BuildRow,
  type Channel,
  PREFIX_RE,
  PREFIX_WHY,
  type ProductOption,
  type TrafficSettings,
  createLinks,
  downloadQr,
  exportLinks,
  getTrafficSettings,
  qrObjectUrl,
  saveTrafficPrefix,
  searchTrafficProducts,
} from '@/lib/traffic'

export const plural = (n: number, one: string, few: string, many: string) => {
  const m10 = n % 10
  const m100 = n % 100
  if (m10 === 1 && m100 !== 11) return one
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return few
  return many
}

// ---------- префикс ----------

/** Поле «Префикс для метки UTM_CAMPAIGN»: где взять, зачем, проверка формата. В «Настройках» и в конструкторе. */
export function PrefixForm({ settings, onSaved, compact = false }: {
  settings: TrafficSettings
  onSaved: (s: TrafficSettings) => void
  compact?: boolean
}) {
  const [value, setValue] = useState(settings.utm_prefix ?? '')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [saving, setSaving] = useState(false)
  const clean = value.trim().toLowerCase().replace(/_+$/, '')
  const formatOk = clean === '' || PREFIX_RE.test(clean)

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setError(null)
    setDone(false)
    setSaving(true)
    try {
      const s = await saveTrafficPrefix(clean || null)
      setValue(s.utm_prefix ?? '')
      setDone(true)
      onSaved(s)
    } catch (err) {
      setError(errorMessage(err, 'Не сохранилось — попробуйте ещё раз'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <form onSubmit={save} className="space-y-2" data-testid="traffic-prefix-form">
      <label htmlFor="traffic-prefix" className="block text-sm font-medium">
        Префикс для метки UTM_CAMPAIGN
      </label>
      <div className="flex flex-wrap gap-2">
        <input
          id="traffic-prefix"
          value={value}
          onChange={(e) => {
            setValue(e.target.value)
            setDone(false)
          }}
          placeholder="vendor_org_123456"
          autoComplete="off"
          spellCheck={false}
          className={`${input} min-w-0 flex-1 font-mono sm:max-w-xs`}
          aria-invalid={!formatOk}
          aria-describedby="traffic-prefix-hint"
          data-testid="traffic-prefix"
        />
        <Button type="submit" variant="primary" disabled={saving || !formatOk} data-testid="traffic-prefix-save">
          Сохранить
        </Button>
      </div>
      {!formatOk && (
        <p className="text-sm text-bad" data-testid="traffic-prefix-format">
          Префикс выглядит так: vendor_org_ и цифры, например vendor_org_123456
        </p>
      )}
      {error && (
        <p className="text-sm text-bad" role="alert" data-testid="traffic-prefix-error">
          {error}
        </p>
      )}
      {done && <p className="text-sm text-ok">Сохранено</p>}
      <p id="traffic-prefix-hint" className="text-xs text-muted">
        {!compact && <>{PREFIX_WHY} </>}
        Скопируйте его из кабинета Ozon: блок «Префикс для метки UTM_CAMPAIGN» в разделе{' '}
        <a href={settings.cabinet_url} target="_blank" rel="noreferrer" className="text-accent hover:underline">
          Продвижение → Внешний трафик
        </a>
        . У каждого магазина он свой.
      </p>
    </form>
  )
}

/** Группа «Продвижение» в «Настройках». */
export function TrafficPrefixSettings() {
  const [settings, setSettings] = useState<TrafficSettings | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    getTrafficSettings().then(setSettings, (err) => setError(errorMessage(err)))
  }, [])
  if (error) return <p className="text-sm text-bad">{error}</p>
  if (!settings) return <Skeleton className="h-24" />
  return (
    <div className="space-y-3">
      <PrefixForm settings={settings} onSaved={setSettings} />
      <p className="text-sm">
        <Link href="/dashboard/advertising/links" className="text-accent hover:underline">
          Ссылки с UTM →
        </Link>
      </p>
    </div>
  )
}

// ---------- конструктор ----------

type Source = 'products' | 'urls'

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

function ProductPicker({ selected, onChange }: { selected: ProductOption[]; onChange: (p: ProductOption[]) => void }) {
  const [search, setSearch] = useState('')
  const q = useDebounced(search, 250)
  const [found, setFound] = useState<ProductOption[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    searchTrafficProducts(q).then(
      (list) => alive && (setFound(list), setError(null)),
      (err) => alive && setError(errorMessage(err)),
    )
    return () => {
      alive = false
    }
  }, [q])
  const chosen = new Set(selected.map((p) => p.id))
  const toggle = (p: ProductOption) => onChange(chosen.has(p.id) ? selected.filter((s) => s.id !== p.id) : [...selected, p])
  const addAll = () => onChange([...selected, ...(found ?? []).filter((p) => !chosen.has(p.id) && p.sku)])

  return (
    <div className="space-y-3" data-testid="traffic-products">
      <div className="flex flex-wrap items-center gap-2">
        <SearchBox value={search} onChange={setSearch} placeholder="Артикул, название, SKU или штрихкод" testid="traffic-product-search" className="min-w-0 flex-1" />
        {found && found.length > 0 && (
          <Button type="button" size="sm" variant="ghost" onClick={addAll} data-testid="traffic-add-all">
            Выбрать найденные
          </Button>
        )}
      </div>
      {error && <p className="text-sm text-bad">{error}</p>}
      {!found && !error && <Skeleton className="h-40" />}
      {found && found.length === 0 && (
        <p className="text-sm text-muted">{q ? 'Ничего не нашлось — проверьте артикул или вставьте ссылку списком' : 'В каталоге нет товаров — обновите товары из Ozon'}</p>
      )}
      {found && found.length > 0 && (
        <ul className="max-h-72 divide-y divide-line overflow-y-auto rounded-lg border border-line" data-testid="traffic-product-list">
          {found.map((p) => (
            <li key={p.id}>
              <label className="flex cursor-pointer items-center gap-3 px-3 py-2 hover:bg-soft">
                <input type="checkbox" checked={chosen.has(p.id)} onChange={() => toggle(p)} className="h-4 w-4 shrink-0 accent-accent" data-testid={`traffic-pick-${p.offer_id}`} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm">{p.name ?? 'Без названия'}</span>
                  <span className="block truncate text-xs text-faint">
                    {p.offer_id}
                    {p.sku ? ` · SKU ${p.sku}` : ' · нет SKU Ozon'}
                    {p.archived && ' · в архиве'}
                  </span>
                </span>
              </label>
            </li>
          ))}
        </ul>
      )}
      {selected.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" data-testid="traffic-selected">
          <span className="text-xs text-muted">
            Выбрано {selected.length} {plural(selected.length, 'товар', 'товара', 'товаров')}:
          </span>
          {selected.slice(0, 12).map((p) => (
            <span key={p.id} className="inline-flex max-w-[12rem] items-center gap-1 rounded-full bg-soft px-2 py-0.5 text-xs">
              <span className="truncate">{p.offer_id}</span>
              <button type="button" onClick={() => toggle(p)} className="text-muted hover:text-fg" aria-label={`Убрать ${p.offer_id}`}>
                <X className="h-3 w-3" aria-hidden />
              </button>
            </span>
          ))}
          {selected.length > 12 && <span className="text-xs text-muted">и ещё {selected.length - 12}</span>}
          <button type="button" onClick={() => onChange([])} className="text-xs text-accent hover:underline" data-testid="traffic-clear">
            Снять все
          </button>
        </div>
      )}
    </div>
  )
}

export function Field({ label, hint, help, children, htmlFor, required }: {
  label: string
  hint?: React.ReactNode
  help?: React.ReactNode
  htmlFor: string
  required?: boolean
  children: React.ReactNode
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-1">
        <label htmlFor={htmlFor} className="text-sm font-medium">
          {label}
          {required && (
            <span className="text-bad" aria-hidden>
              {' '}*
            </span>
          )}
        </label>
        {help && <HelpTip label={`Что такое «${label}»`}>{help}</HelpTip>}
      </div>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  )
}

/** Поле с выпадающим списком подсказок, как в конструкторе Ozon: выбрать из списка или вписать своё. Список фильтруется по
 *  набранному, ↑ ↓ — выбор, Enter — подставить, Esc — закрыть. */
export function SuggestInput({ id, value, onChange, options, placeholder, disabled, required, testid }: {
  id: string
  value: string
  onChange: (v: string) => void
  options: string[]
  placeholder?: string
  disabled?: boolean
  required?: boolean
  testid: string
}) {
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(-1)
  const box = useRef<HTMLDivElement>(null)
  const listId = useId()
  const q = value.trim().toLowerCase()
  const shown = q && options.some((o) => o.toLowerCase().startsWith(q)) ? options.filter((o) => o.toLowerCase().includes(q)) : options

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => box.current && !box.current.contains(e.target as Node) && setOpen(false)
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
    setActive(-1)
  }
  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setOpen(true)
      if (!shown.length) return
      setActive((i) => (e.key === 'ArrowDown' ? (i + 1) % shown.length : (i - 1 + shown.length) % shown.length))
    } else if (e.key === 'Enter' && open && active >= 0 && shown[active]) {
      e.preventDefault()
      pick(shown[active])
    } else if (e.key === 'Escape' && open) {
      e.preventDefault()
      setOpen(false)
    }
  }

  return (
    <div ref={box} className="relative">
      <input
        id={id}
        role="combobox"
        aria-expanded={open && shown.length > 0}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-required={required}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        value={value}
        disabled={disabled}
        onChange={(e) => {
          onChange(e.target.value)
          setOpen(true)
          setActive(-1)
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        placeholder={placeholder}
        className={`${input} pr-9 disabled:opacity-60`}
        autoComplete="off"
        spellCheck={false}
        data-testid={testid}
      />
      <button
        type="button"
        tabIndex={-1}
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="absolute inset-y-0 right-0 flex w-9 items-center justify-center text-muted hover:text-fg disabled:opacity-40"
        aria-label="Показать подсказки"
        data-testid={`${testid}-toggle`}
      >
        <ChevronsUpDown className="h-4 w-4" aria-hidden />
      </button>
      {open && !disabled && shown.length > 0 && (
        <ul
          id={listId}
          role="listbox"
          className="absolute left-0 right-0 z-30 mt-1 max-h-56 overflow-y-auto rounded-xl border border-line bg-card py-1 shadow-pop"
          data-testid={`${testid}-options`}
        >
          {shown.map((o, i) => (
            <li
              key={o}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={o === value}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => pick(o)}
              onMouseEnter={() => setActive(i)}
              className={`flex cursor-pointer items-center justify-between px-3 py-2 font-mono text-sm ${i === active ? 'bg-soft' : ''}`}
            >
              {o}
              {o === value && <Check className="h-3.5 w-3.5 text-accent" aria-hidden />}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

type LabelsForm = { channel: string; campaign: string; source: string; medium: string; term: string; content: string }

function Labels({ settings, form, set }: { settings: TrafficSettings; form: LabelsForm; set: (patch: Partial<LabelsForm>) => void }) {
  const channel = settings.channels.find((c) => c.key === form.channel) as Channel
  const [target, setTarget] = useState<'content' | 'term'>('content')
  const campaignPreview = `${settings.utm_prefix ?? 'vendor_org_…'}_${form.campaign.trim().toLowerCase().replace(/\s+/g, '_') || 'название'}`
  const addMacro = (code: string) => set({ [target]: form[target] + code })

  return (
    <div className="space-y-4" data-testid="traffic-labels">
      <Field label="Канал" htmlFor="traffic-channel" hint={channel.hint}>
        <select
          id="traffic-channel"
          value={form.channel}
          onChange={(e) => {
            const c = settings.channels.find((x) => x.key === e.target.value) as Channel
            set({ channel: c.key, source: c.fixed_source ? c.source : form.source, medium: c.medium })
          }}
          className={selectClass}
          data-testid="traffic-channel"
        >
          {settings.channels.map((c) => (
            <option key={c.key} value={c.key}>
              {c.label}
            </option>
          ))}
        </select>
      </Field>
      <Field
        label="Название кампании"
        htmlFor="traffic-campaign"
        required
        help="Идёт в метку utm_campaign после вашего префикса. По нему кампания видна в отчётах Ozon и в сервисе."
        hint={
          <>
            utm_campaign: <span className="break-all font-mono text-fg" data-testid="traffic-campaign-preview">{campaignPreview}</span>
          </>
        }
      >
        <input id="traffic-campaign" value={form.campaign} onChange={(e) => set({ campaign: e.target.value })} placeholder="например vk_november" className={input} autoComplete="off" spellCheck={false} data-testid="traffic-campaign" />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field
          label="Источник"
          htmlFor="traffic-source"
          required
          hint="utm_source"
          help="Метка utm_source: откуда переход — сеть или площадка (yandex, vk, inst, tg). Обязательна: без неё в отчёте Ozon нет разбивки по источникам. У рекламных сетей задаётся сама."
        >
          <SuggestInput
            id="traffic-source"
            value={form.source}
            onChange={(v) => set({ source: v })}
            options={channel.source_options}
            disabled={channel.fixed_source}
            required
            placeholder="Выберите или впишите"
            testid="traffic-source"
          />
        </Field>
        <Field
          label="Тип трафика"
          htmlFor="traffic-medium"
          required
          hint="utm_medium"
          help="Метка utm_medium: какой это трафик или где ссылка (cpc — оплата за клик, description, post, story). Обязательна: без неё в отчёте Ozon нет разбивки по источникам."
        >
          <SuggestInput
            id="traffic-medium"
            value={form.medium}
            onChange={(v) => set({ medium: v })}
            options={channel.medium_options}
            required
            placeholder="Выберите или впишите"
            testid="traffic-medium"
          />
        </Field>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Содержание" htmlFor="traffic-content" hint="Необязательно" help="Метка utm_content: какое объявление или место ссылки (link_in_bio, banner_1).">
          <input id="traffic-content" value={form.content} onChange={(e) => set({ content: e.target.value })} onFocus={() => setTarget('content')} className={input} autoComplete="off" spellCheck={false} data-testid="traffic-content" />
        </Field>
        <Field label="Ключевое слово" htmlFor="traffic-term" hint="Необязательно" help="Метка utm_term: ключевая фраза или аудитория.">
          <input id="traffic-term" value={form.term} onChange={(e) => set({ term: e.target.value })} onFocus={() => setTarget('term')} className={input} autoComplete="off" spellCheck={false} data-testid="traffic-term" />
        </Field>
      </div>
      {channel.macros.length > 0 && (
        <div className="space-y-2" data-testid="traffic-macros">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
            <span>Макросы сети — подставит {channel.label.split(' (')[0]} при показе, в</span>
            <Segmented
              size="sm"
              value={target}
              onChange={setTarget}
              options={[
                { value: 'content', label: 'Содержание' },
                { value: 'term', label: 'Ключевое слово' },
              ]}
              label="Куда вставить макрос"
            />
          </div>
          <div className="flex flex-wrap gap-1.5">
            {channel.macros.map((m) => (
              <button key={m.code} type="button" onClick={() => addMacro(m.code)} className="rounded-full border border-line px-2.5 py-1 text-xs text-muted hover:border-accent hover:text-accent" title={m.code}>
                + {m.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

export function QrModal({ url, onClose }: { url: string; onClose: () => void }) {
  const [src, setSrc] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let made: string | null = null
    qrObjectUrl(url).then(
      (u) => {
        made = u
        setSrc(u)
      },
      (err) => setError(errorMessage(err)),
    )
    return () => {
      if (made) URL.revokeObjectURL(made)
    }
  }, [url])
  const save = (f: 'png' | 'svg') => downloadQr(url, f).catch((err) => setError(errorMessage(err)))
  return (
    <Modal title="QR-код ссылки" onClose={onClose}>
      <div className="space-y-4" data-testid="traffic-qr">
        <div className="mx-auto w-56 overflow-hidden rounded-lg border border-line">
          {src ? <img src={src} alt="QR-код ссылки с метками" className="h-auto w-full" /> : !error && <Skeleton className="aspect-square w-full" />}
        </div>
        {error && <p className="text-sm text-bad">{error}</p>}
        <p className="break-all text-xs text-muted">{url}</p>
        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="primary" onClick={() => save('png')} data-testid="traffic-qr-png">
            Скачать PNG
          </Button>
          <Button type="button" onClick={() => save('svg')}>
            Скачать SVG — для печати
          </Button>
        </div>
      </div>
    </Modal>
  )
}

function CopyAll({ text }: { text: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const t = setTimeout(() => setState('idle'), 1500)
    return () => clearTimeout(t)
  }, [state])
  return (
    <Button size="sm" onClick={async () => setState((await writeText(text)) ? 'ok' : 'fail')} data-testid="traffic-copy-all" data-state={state}>
      {state === 'ok' ? <Check className="h-4 w-4 text-ok" aria-hidden /> : <Copy className="h-4 w-4" aria-hidden />}
      {state === 'ok' ? 'Скопировано' : state === 'fail' ? 'Не получилось' : 'Скопировать все'}
    </Button>
  )
}

function Results({ result, request, sections, onHistory }: {
  result: BuildResult
  request: BuildRequest
  sections: Record<string, string>
  onHistory: () => void
}) {
  const [qr, setQr] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const order = useMemo(() => new Map(result.rows.map((r, i) => [r, i])), [result])
  const by = useMemo(
    () => ({
      n: (r: BuildRow) => order.get(r) ?? 0,
      what: (r: BuildRow) => r.product?.name ?? r.input,
      section: (r: BuildRow) => (r.section ? sections[r.section] : null),
      status: (r: BuildRow) => (r.url ? 1 : 0),
    }),
    [order, sections],
  )
  const t = useSort(result.rows, by)
  const all = result.rows.flatMap((r) => (r.url ? [r.url] : [])).join('\n')
  const file = (f: 'xlsx' | 'csv') => exportLinks(request, f).catch((err) => setError(errorMessage(err)))

  return (
    <Panel
      flush
      testid="traffic-results"
      title="Ссылки с метками"
      description={
        <>
          Собрано {result.ok} {plural(result.ok, 'ссылка', 'ссылки', 'ссылок')}
          {result.errors > 0 && <span className="text-bad"> · с ошибкой {result.errors}</span>} · utm_campaign{' '}
          <span className="break-all font-mono">{result.campaign}</span>
        </>
      }
    >
      {result.ok > 0 && (
        // кнопки — отдельной строкой под заголовком: справа от него на телефоне заголовок сжимался в колонку
        <div className="flex flex-wrap gap-2 px-5 pb-3" data-testid="traffic-actions">
          <CopyAll text={all} />
          <Button size="sm" onClick={() => file('xlsx')} data-testid="traffic-xlsx">
            <FileSpreadsheet className="h-4 w-4" aria-hidden /> Excel
          </Button>
          <Button size="sm" onClick={() => file('csv')} data-testid="traffic-csv">
            CSV
          </Button>
          <button type="button" onClick={onHistory} className="ml-auto self-center text-sm text-accent hover:underline" data-testid="traffic-saved">
            Сохранено в «Созданные ссылки» →
          </button>
        </div>
      )}
      {(result.warnings.length > 0 || error) && (
        <div className="space-y-1 px-5 pb-3 text-sm">
          {result.warnings.map((w) => (
            <p key={w} className="text-warn">
              {w}
            </p>
          ))}
          {error && <p className="text-bad">{error}</p>}
        </div>
      )}
      {/* relative — скрытая подпись колонки (sr-only, absolute) остаётся внутри прокрутки и не растягивает страницу вбок */}
      <div className="relative overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm" data-testid="traffic-table">
          <thead className="bg-soft/60 text-xs text-muted">
            <tr>
              <SortTh {...t.th('n')} kind="number" className="w-10 py-2 pl-5 pr-2 font-medium">
                №
              </SortTh>
              <SortTh {...t.th('what')} kind="text" className="px-3 py-2 font-medium">
                Что размечено
              </SortTh>
              <SortTh {...t.th('section')} kind="text" className="px-3 py-2 font-medium">
                Раздел
              </SortTh>
              <SortTh {...t.th('status')} kind="number" className="px-3 py-2 font-medium" title="Сначала собранные или с ошибкой">
                Ссылка с метками
              </SortTh>
              <th className="py-2 pl-3 pr-5 text-right font-medium">
                <span className="sr-only">QR-код</span>
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {t.rows.map((r) => (
              <tr key={order.get(r)} className="align-top" data-testid="traffic-row">
                <td className="py-3 pl-5 pr-2 tabular-nums text-faint">{(order.get(r) ?? 0) + 1}</td>
                <td className="max-w-[16rem] px-3 py-3">
                  {r.product ? (
                    <>
                      <span className="block truncate font-medium">{r.product.name ?? 'Без названия'}</span>
                      <Sub>
                        <CopyText value={r.product.offer_id} label="Скопировать артикул" inline />
                      </Sub>
                    </>
                  ) : (
                    <>
                      <span className="block truncate" title={r.input}>
                        {r.input}
                      </span>
                      <Sub>Строка {r.line}</Sub>
                    </>
                  )}
                </td>
                <td className="whitespace-nowrap px-3 py-3">{r.section ? sections[r.section] : '—'}</td>
                <td className="max-w-[28rem] px-3 py-3">
                  {r.url ? (
                    <CopyText value={r.url} label="Скопировать ссылку" testid="traffic-url">
                      <span className="font-mono text-xs">{r.url}</span>
                    </CopyText>
                  ) : (
                    <Sub tone="bad">{r.error}</Sub>
                  )}
                </td>
                <td className="py-3 pl-3 pr-5 text-right">
                  {r.url && (
                    <button type="button" onClick={() => setQr(r.url)} className="rounded-lg p-1.5 text-muted hover:bg-soft hover:text-fg" aria-label="QR-код ссылки" data-testid="traffic-qr-open">
                      <QrCode className="h-4 w-4" aria-hidden />
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {qr && <QrModal url={qr} onClose={() => setQr(null)} />}
    </Panel>
  )
}

type PageTab = 'build' | 'history'

export function LinkBuilderPage() {
  const params = useSearchParams()
  const router = useRouter()
  const pathname = usePathname()
  const tab: PageTab = params.get('tab') === 'history' ? 'history' : 'build'
  const focus = params.get('link')
  const setTab = (v: PageTab) => router.replace(v === 'history' ? `${pathname}?tab=history` : pathname, { scroll: false })
  const [saved, setSaved] = useState(0) // сохранили новые ссылки — история перечитывается
  const [settings, setSettings] = useState<TrafficSettings | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [source, setSource] = useState<Source>('products')
  const [products, setProducts] = useState<ProductOption[]>([])
  const [urls, setUrls] = useState('')
  const [form, setForm] = useState<LabelsForm>({ channel: 'social', campaign: '', source: '', medium: 'social', term: '', content: '' })
  const [result, setResult] = useState<{ result: BuildResult; request: BuildRequest } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [editPrefix, setEditPrefix] = useState(false)

  useEffect(() => {
    getTrafficSettings().then(setSettings, (err) => setLoadError(errorMessage(err)))
  }, [])

  const current = settings?.channels.find((c) => c.key === form.channel)
  // обязательные поля, как в конструкторе Ozon: название кампании, utm_source (у сетей — сам), utm_medium
  const missing = [
    !form.campaign.trim() && 'название кампании',
    !current?.fixed_source && !form.source.trim() && 'источник (utm_source)',
    !form.medium.trim() && 'тип трафика (utm_medium)',
  ].filter(Boolean) as string[]
  const lines = urls.split('\n').filter((s) => s.trim()).length
  const total = products.length + lines
  const max = settings?.max_links ?? 100

  async function build(e: React.FormEvent) {
    e.preventDefault()
    if (!settings) return
    const channel = settings.channels.find((c) => c.key === form.channel) as Channel
    const request: BuildRequest = {
      product_ids: products.map((p) => p.id),
      urls,
      channel: form.channel,
      campaign: form.campaign,
      source: channel.fixed_source ? null : form.source,
      medium: form.medium,
      term: form.term,
      content: form.content,
    }
    setError(null)
    setBusy(true)
    try {
      setResult({ result: await createLinks(request), request })
      setSaved((n) => n + 1)
    } catch (err) {
      setError(errorMessage(err, 'Не получилось собрать ссылки — попробуйте ещё раз'))
    } finally {
      setBusy(false)
    }
  }

  const header = (
    <PageHeader
      title="Ссылки с UTM"
      titleTestid="traffic-title"
      description="Ссылки на товары, магазин, бренд и акции Ozon с метками для внешней рекламы — Ozon посчитает по ним переходы и заказы"
      actions={
        settings && (
          <a href={settings.cabinet_url} target="_blank" rel="noreferrer" className={buttonClass('ghost', 'sm')}>
            <ExternalLink className="h-4 w-4" aria-hidden /> Внешний трафик в Ozon
          </a>
        )
      }
    >
      <Tabs
        value={tab}
        onChange={setTab}
        label="Ссылки с UTM"
        testid="traffic-tabs"
        itemTestid={(v) => `traffic-tab-${v}`}
        items={[
          { value: 'build', label: 'Конструктор' },
          { value: 'history', label: 'Созданные ссылки' },
        ]}
      />
    </PageHeader>
  )

  if (loadError)
    return (
      <div>
        {header}
        <EmptyState title="Не загрузилось" testid="traffic-load-error">
          {loadError}
        </EmptyState>
      </div>
    )
  if (!settings)
    return (
      <div>
        {header}
        <div className="grid gap-5 lg:grid-cols-3">
          <Skeleton className="h-80 lg:col-span-2" />
          <Skeleton className="h-80" />
        </div>
      </div>
    )

  if (tab === 'history')
    return (
      <div>
        {header}
        <LinkHistory settings={settings} focus={focus} version={saved} />
      </div>
    )

  if (!settings.utm_prefix)
    return (
      <div>
        {header}
        <Panel title="Сначала — префикс магазина" description={PREFIX_WHY} testid="traffic-need-prefix" className="max-w-2xl">
          <PrefixForm settings={settings} onSaved={setSettings} compact />
        </Panel>
      </div>
    )

  return (
    <div>
      {header}
      <form onSubmit={build} className="space-y-5">
        <div className="grid gap-5 lg:grid-cols-3">
          <Panel
            className="min-w-0 lg:col-span-2"
            title="Что размечаем"
            description={`Товары из каталога и ссылки списком — вместе до ${max} за раз`}
            action={
              <HelpTip label="Какие ссылки можно разметить">
                Только ссылки на ozon.ru, ozon.kz или ozon.by и только на товар (/product/), магазин (/seller/), бренд
                (/brand/) или акцию и подборку (/highlight/). Остальные Ozon не посчитает — у такой строки будет ошибка.
              </HelpTip>
            }
          >
            <div className="space-y-4">
              <Segmented
                value={source}
                onChange={setSource}
                testid="traffic-source-mode"
                optionTestid={(v) => `traffic-mode-${v}`}
                label="Откуда ссылки"
                options={[
                  { value: 'products', label: `Товары${products.length ? ` · ${products.length}` : ''}` },
                  { value: 'urls', label: `Ссылки списком${lines ? ` · ${lines}` : ''}` },
                ]}
              />
              {source === 'products' ? (
                <ProductPicker selected={products} onChange={setProducts} />
              ) : (
                <div className="space-y-1">
                  <label htmlFor="traffic-urls" className="sr-only">
                    Ссылки по одной в строке
                  </label>
                  <textarea
                    id="traffic-urls"
                    value={urls}
                    onChange={(e) => setUrls(e.target.value)}
                    rows={8}
                    placeholder={'https://www.ozon.ru/product/…\nhttps://www.ozon.ru/brand/…\nhttps://www.ozon.ru/highlight/…'}
                    className={`${input} font-mono text-xs`}
                    spellCheck={false}
                    data-testid="traffic-urls"
                  />
                  <p className="text-xs text-muted">По одной ссылке в строке; пустые строки пропускаются.</p>
                </div>
              )}
              <p className={`text-xs ${total > max ? 'text-bad' : 'text-muted'}`} data-testid="traffic-count">
                Всего {total} из {max}
                {total > max && ' — уберите лишние, Ozon размечает до 100 ссылок за раз'}
              </p>
            </div>
          </Panel>
          <Panel
            className="min-w-0"
            title="Метки"
            description={
              <>
                Префикс <span className="font-mono text-fg">{settings.utm_prefix}</span> ·{' '}
                <button type="button" onClick={() => setEditPrefix((v) => !v)} className="text-accent hover:underline" data-testid="traffic-prefix-edit">
                  {editPrefix ? 'Скрыть' : 'Изменить'}
                </button>
              </>
            }
          >
            {editPrefix && (
              <div className="mb-4 border-b border-line pb-4">
                <PrefixForm settings={settings} onSaved={(s) => (setSettings(s), setEditPrefix(false))} compact />
              </div>
            )}
            <Labels settings={settings} form={form} set={(patch) => setForm((f) => ({ ...f, ...patch }))} />
          </Panel>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" variant="primary" disabled={busy || total === 0 || total > max || missing.length > 0} data-testid="traffic-build">
            <Link2 className="h-4 w-4" aria-hidden /> Собрать ссылки
          </Button>
          {total > 0 && missing.length > 0 && (
            <span className="text-sm text-muted" data-testid="traffic-missing">
              Заполните: {missing.join(', ')}
            </span>
          )}
          {error && (
            <p className="text-sm text-bad" role="alert" data-testid="traffic-error">
              {error}
            </p>
          )}
        </div>
      </form>
      <div className="mt-5">
        {result ? (
          <Results result={result.result} request={result.request} sections={settings.sections} onHistory={() => setTab('history')} />
        ) : (
          <EmptyState icon={Tag} title="Здесь появятся ссылки с метками" testid="traffic-empty">
            Выберите товары или вставьте ссылки, задайте кампанию и нажмите «Собрать ссылки». Готовые ссылки можно скопировать, скачать
            Excel или CSV и QR-код. Собранные ссылки сохраняются во вкладке «Созданные ссылки».
          </EmptyState>
        )}
      </div>
      <p className="mt-5 flex items-start gap-2 text-xs text-muted">
        <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
        Диплинк в приложение Ozon и подменный артикул для блогеров создаёт только кабинет Ozon — вставьте их к ссылке во вкладке
        «Созданные ссылки», сервис напомнит, когда диплинк пора продлить. Статистика по ссылкам — пока в кабинете Ozon.
      </p>
    </div>
  )
}
