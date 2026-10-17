'use client'
// Цены списком (шаг 6.3): мастер из 5 шагов — способ (стратегия или Excel) → товары или файл → предпросмотр по группам →
// подтверждение → применение по 100 товаров с паузой 2 с и ходом по запросам. Правила — BUSINESS_LOGIC §8.
import Link from 'next/link'
import { useEffect, useMemo, useState } from 'react'

import { FormError } from '@/components/AuthForm'
import { Badge, pctText, rub } from '@/components/reports'
import { api, errorMessage } from '@/lib/api'
import { downloadPriceErrors, downloadPriceTemplate, type TemplateFormat } from '@/lib/download'
import { fieldNumber } from '@/lib/pricing'
import {
  BULK_CONFIRM_ABOVE,
  BULK_LIMIT,
  BULK_WORD,
  BULK_WORD_ABOVE,
  OZON_BATCH,
  type BulkGroup,
  type BulkPreview,
  type BulkPreviewItem,
  type BulkStatus,
  type BulkWay,
  type PriceRow,
  type PricesPage,
} from '@/lib/pricing'

const STEPS = ['Способ', 'Товары', 'Предпросмотр', 'Подтверждение', 'Применение']

const WAYS: { value: BulkWay; title: string; hint: string; unit?: string; placeholder?: string }[] = [
  {
    value: 'target_margin',
    title: 'Целевая маржа',
    hint: 'Цена, при которой с каждой продажи остаётся эта маржа: комиссия, логистика, налоги и себестоимость — по расчёту цены.',
    unit: '% маржа',
  },
  { value: 'percent', title: 'Процент к текущей цене', hint: 'Поднять (или с минусом — снизить) текущую цену на X %.', unit: '%', placeholder: '5' },
  {
    value: 'min_plus',
    title: 'Минимальная цена + X %',
    hint: 'Цена без убытка плюс наценка — запас на случай, если расходы вырастут.',
    unit: '% к минимальной',
    placeholder: '10',
  },
  {
    value: 'file',
    title: 'Через Excel',
    hint: 'Скачайте шаблон с текущими ценами, поменяйте колонку «Новая цена» и загрузите — удобно, если цены уже посчитаны у вас.',
  },
]

const GROUPS: { value: BulkGroup; label: string; tone: 'ok' | 'warn' | 'muted' }[] = [
  { value: 'ok', label: 'Применится', tone: 'ok' },
  { value: 'warn', label: 'С предупреждением', tone: 'warn' },
  { value: 'blocked', label: 'Заблокировано', tone: 'muted' },
]

const STATUS_TEXT: Record<string, string> = {
  applied: 'применено',
  rejected: 'Ozon отказал',
  failed: 'ошибка, не изменилось',
  unknown: 'Ozon не ответил — могло примениться',
  blocked: 'не прошло проверку',
  queued: 'ждёт очереди',
  sending: 'отправляется',
  not_applied: 'не применялось',
}

const time = (iso: string) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
const signed = (v: string | null) => (v === null ? '' : `${Number(v) > 0 ? '+' : ''}${pctText(v)}`)
const input = 'rounded-lg border border-line bg-bg px-2 py-1.5 text-sm text-fg outline-none placeholder:text-faint focus:border-accent'

export default function BulkPrices() {
  const [step, setStep] = useState(0)
  const [way, setWay] = useState<BulkWay>('percent')
  const [value, setValue] = useState('')
  const [limitStep, setLimitStep] = useState(true)
  const [reason, setReason] = useState('')
  const [rows, setRows] = useState<PriceRow[] | null>(null)
  const [targetMargin, setTargetMargin] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<BulkPreview | null>(null)
  const [status, setStatus] = useState<BulkStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // товары в продаже и наша целевая маржа — для выбора и подсказки (только расчёт)
  useEffect(() => {
    api
      .get<PricesPage>('/price', { params: { limit: BULK_LIMIT, sort: 'offer_id' } })
      .then((r) => {
        setRows(r.data.items)
        setTargetMargin(r.data.settings.target_margin_pct)
      })
      .catch((err) => setError(errorMessage(err)))
  }, [])

  // пусто у целевой маржи — маржа из настроек; у процентов число нужно ввести
  const strategyValue = value.trim() || (way === 'target_margin' ? targetMargin ?? '' : '')
  const parsed = fieldNumber(strategyValue)
  const valueError =
    way === 'file'
      ? null
      : parsed === null || !Number.isFinite(parsed)
        ? 'Укажите число'
        : way === 'target_margin' && (parsed < 0 || parsed > 90)
          ? 'Целевая маржа — от 0 до 90 %'
          : way === 'min_plus' && parsed < 0
            ? 'Наценка — не меньше 0 %'
            : way === 'percent' && (parsed < -90 || parsed > 500)
              ? 'Процент — от −90 до 500'
              : null

  async function calculate() {
    setBusy(true)
    setError(null)
    try {
      let data: BulkPreview
      if (way === 'file') {
        if (!file) return
        const form = new FormData()
        form.append('file', file)
        if (reason.trim()) form.append('reason', reason.trim())
        data = (await api.post<BulkPreview>('/prices/bulk/upload', form)).data
      } else {
        data = (
          await api.post<BulkPreview>('/prices/bulk/preview', {
            strategy: { kind: way, value: String(parsed), limit_step: limitStep },
            product_ids: Array.from(selected),
            ...(reason.trim() ? { reason: reason.trim() } : {}),
          })
        ).data
      }
      setPreview(data)
      setStep(2)
    } catch (err) {
      setError(errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  type Confirm = { withWarnings: boolean; confirmed: boolean; word: string | null }
  async function apply(c: Confirm) {
    const id = preview?.batch_id
    if (!id) return
    setError(null)
    setStatus(null)
    setStep(4)
    // пока идёт применение — ход по запросам в Ozon
    const poll = setInterval(() => {
      api.get<BulkStatus>(`/prices/bulk/${id}`).then((r) => setStatus((old) => (old?.finished ? old : r.data))).catch(() => undefined)
    }, 1000)
    try {
      const { data } = await api.post<BulkStatus>('/prices/bulk/apply', {
        batch_id: id,
        include_warnings: c.withWarnings,
        confirmed: c.confirmed,
        ...(c.word ? { confirm_word: c.word } : {}),
      })
      setStatus(data)
    } catch (err) {
      setError(errorMessage(err))
      setStep(3)
    } finally {
      clearInterval(poll)
    }
  }

  function restart() {
    setStep(0)
    setPreview(null)
    setStatus(null)
    setSelected(new Set())
    setFile(null)
    setError(null)
  }

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <Link href="/dashboard/prices" className="text-sm text-accent hover:underline">
          ← Цены
        </Link>
        <h1 className="mt-1 text-2xl font-semibold">Цены списком</h1>
        <p className="mt-1 text-sm text-muted">
          Поменять цены многих товаров сразу: по стратегии или из Excel. Правила те же, что у одного товара: не больше 20 % за раз,
          не ниже себестоимости с НДС + 5 %, не чаще раза в час — и ничего не уйдёт в Ozon до подтверждения.
        </p>
      </div>

      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm" data-testid="bulk-steps">
        {STEPS.map((title, i) => (
          <li key={title} className={i === step ? 'font-semibold text-fg' : i < step ? 'text-muted' : 'text-faint'}>
            <span className={`mr-1.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-xs ${i === step ? 'bg-accent text-white' : 'bg-line'}`}>
              {i + 1}
            </span>
            {i === 1 && way === 'file' ? 'Файл' : title}
          </li>
        ))}
      </ol>

      <FormError message={error} />

      {step === 0 && (
        <section className="space-y-4 rounded-xl border border-line bg-card p-4" data-testid="bulk-way">
          <h2 className="font-semibold">Как менять цены</h2>
          <div className="grid gap-3 sm:grid-cols-2">
            {WAYS.map((w) => (
              <label
                key={w.value}
                className={`flex cursor-pointer gap-3 rounded-lg border p-3 text-sm ${way === w.value ? 'border-accent bg-accent/5' : 'border-line hover:border-muted'}`}
              >
                <input type="radio" name="way" checked={way === w.value} onChange={() => setWay(w.value)} className="mt-1" data-testid={`way-${w.value}`} />
                <span>
                  <span className="font-medium">{w.title}</span>
                  <span className="mt-0.5 block text-muted">{w.hint}</span>
                </span>
              </label>
            ))}
          </div>
          {way !== 'file' && (
            <div className="space-y-3 text-sm">
              <label className="block text-xs text-muted">
                {way === 'target_margin' ? 'Целевая маржа' : 'Сколько процентов'}
                <span className="mt-1 flex items-center gap-2">
                  <input
                    inputMode="decimal"
                    value={value}
                    placeholder={way === 'target_margin' ? targetMargin ? String(Number(targetMargin)) : '20' : WAYS.find((w) => w.value === way)?.placeholder}
                    onChange={(e) => setValue(e.target.value)}
                    className={`${input} w-28 text-right`}
                    data-testid="bulk-value"
                  />
                  <span className="text-faint">{WAYS.find((w) => w.value === way)?.unit}</span>
                </span>
                {way === 'target_margin' && <span className="mt-1 block text-faint">Пусто — ваша целевая маржа из настроек расчёта цены.</span>}
              </label>
              {valueError && <p className="text-bad">{valueError}</p>}
              <label className="flex items-start gap-2">
                <input type="checkbox" checked={limitStep} onChange={(e) => setLimitStep(e.target.checked)} className="mt-0.5" data-testid="bulk-limit-step" />
                <span>
                  Не больше 20 % за раз
                  <span className="block text-xs text-muted">
                    Если до цены по стратегии дальше 20 %, поставим ближайшую допустимую, а остальное — следующим шагом, не раньше чем через час.
                    Без галочки такие товары будут заблокированы.
                  </span>
                </span>
              </label>
            </div>
          )}
          <label className="block text-xs text-muted">
            Комментарий — необязательно
            <input
              value={reason}
              maxLength={500}
              placeholder="Например: поднять маржу к сезону"
              onChange={(e) => setReason(e.target.value)}
              className={`${input} mt-1 w-full`}
              data-testid="bulk-reason"
            />
            <span className="mt-1 block text-faint">Попадёт в историю изменений у каждого товара. Пусто — запишем способ.</span>
          </label>
          <div className="flex justify-end">
            <button
              type="button"
              disabled={!!valueError}
              onClick={() => setStep(1)}
              className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
              data-testid="bulk-next"
            >
              Дальше
            </button>
          </div>
        </section>
      )}

      {step === 1 && way !== 'file' && (
        <ProductPicker rows={rows} selected={selected} setSelected={setSelected} busy={busy} onBack={() => setStep(0)} onNext={calculate} />
      )}

      {step === 1 && way === 'file' && <FileStep file={file} setFile={setFile} busy={busy} onBack={() => setStep(0)} onNext={calculate} />}

      {step >= 2 && preview && step < 4 && (
        <PreviewStep
          preview={preview}
          file={way === 'file' ? file : null}
          step={step}
          onBack={() => setStep(step === 3 ? 2 : 1)}
          onNext={() => setStep(3)}
          onApply={apply}
        />
      )}

      {step === 4 && <ProgressStep status={status} onRestart={restart} />}
    </div>
  )
}

function ProductPicker({
  rows,
  selected,
  setSelected,
  busy,
  onBack,
  onNext,
}: {
  rows: PriceRow[] | null
  selected: Set<string>
  setSelected: (s: Set<string>) => void
  busy: boolean
  onBack: () => void
  onNext: () => void
}) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<'all' | 'below_min' | 'below_target'>('all')
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    return (rows ?? []).filter(
      (r) =>
        (filter === 'all' || r[filter]) &&
        (!q || r.offer_id.toLowerCase().includes(q) || (r.name ?? '').toLowerCase().includes(q)),
    )
  }, [rows, search, filter])
  const allShown = shown.length > 0 && shown.every((r) => selected.has(r.id))
  function toggle(id: string) {
    const next = new Set(selected)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setSelected(next)
  }
  function toggleShown() {
    const next = new Set(selected)
    shown.forEach((r) => (allShown ? next.delete(r.id) : next.add(r.id)))
    setSelected(next)
  }
  const tooMany = selected.size > BULK_LIMIT
  return (
    <section className="space-y-3 rounded-xl border border-line bg-card p-4" data-testid="bulk-products">
      <h2 className="font-semibold">Какие товары</h2>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {(
          [
            ['all', 'Все'],
            ['below_min', 'Ниже минимальной'],
            ['below_target', 'Ниже целевой'],
          ] as const
        ).map(([v, label]) => (
          <button
            key={v}
            type="button"
            onClick={() => setFilter(v)}
            className={`rounded-full border px-3 py-1 ${filter === v ? 'border-accent text-accent' : 'border-line text-muted hover:border-muted'}`}
          >
            {label}
          </button>
        ))}
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Артикул или название" className={`${input} ml-auto w-full sm:w-56`} />
      </div>
      {rows === null ? (
        <p className="text-sm text-muted">Загрузка…</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="flex items-center gap-2">
              <input type="checkbox" checked={allShown} onChange={toggleShown} data-testid="bulk-select-all" />
              Выбрать показанные ({shown.length})
            </label>
            <span className={`ml-auto ${tooMany ? 'text-bad' : 'text-muted'}`} data-testid="bulk-selected">
              Выбрано {selected.size} из {BULK_LIMIT} возможных
            </span>
          </div>
          <ul className="max-h-[28rem] divide-y divide-line overflow-y-auto rounded-lg border border-line text-sm">
            {shown.map((r) => (
              <li key={r.id}>
                <label className="flex cursor-pointer items-start gap-3 px-3 py-2 hover:bg-bg/60">
                  <input type="checkbox" checked={selected.has(r.id)} onChange={() => toggle(r.id)} className="mt-1" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{r.offer_id}</span>
                    <span className="block truncate text-xs text-muted">{r.name ?? 'Без названия'}</span>
                  </span>
                  <span className="shrink-0 text-right text-xs tabular-nums">
                    <span className="block text-sm">{rub(r.price)}</span>
                    <span className="text-faint">
                      мин. {rub(r.min_price)} · цель {rub(r.target_price)}
                    </span>
                  </span>
                </label>
              </li>
            ))}
            {shown.length === 0 && <li className="px-3 py-4 text-muted">Ничего не найдено.</li>}
          </ul>
        </>
      )}
      {tooMany && <p className="text-sm text-bad">За один раз — не больше {BULK_LIMIT} товаров. Снимите часть выбора.</p>}
      <div className="flex justify-between gap-2">
        <button type="button" onClick={onBack} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
          Назад
        </button>
        <button
          type="button"
          disabled={busy || selected.size === 0 || tooMany}
          onClick={onNext}
          className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
          data-testid="bulk-calculate"
        >
          {busy ? 'Считаем…' : `Рассчитать (${selected.size})`}
        </button>
      </div>
    </section>
  )
}

function FileStep({ file, setFile, busy, onBack, onNext }: { file: File | null; setFile: (f: File | null) => void; busy: boolean; onBack: () => void; onNext: () => void }) {
  const [error, setError] = useState<string | null>(null)
  async function download(format: TemplateFormat) {
    setError(null)
    try {
      await downloadPriceTemplate(format)
    } catch (err) {
      setError(errorMessage(err))
    }
  }
  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-4 text-sm" data-testid="bulk-file">
      <h2 className="font-semibold">Файл с ценами</h2>
      <ol className="list-decimal space-y-1 pl-5 text-muted">
        <li>
          Скачайте шаблон — в нём ваши товары в продаже, текущие цены и для ориентира минимальная и целевая по расчёту:{' '}
          <button type="button" onClick={() => download('xlsx')} className="text-accent hover:underline" data-testid="bulk-template">
            Excel
          </button>{' '}
          или{' '}
          <button type="button" onClick={() => download('csv')} className="text-accent hover:underline">
            CSV
          </button>
          .
        </li>
        <li>Поменяйте колонку «Новая цена» там, где нужно. Остальные строки не трогайте — они не изменятся.</li>
        <li>Загрузите файл — сначала покажем, что изменится, и проверим каждую цену.</li>
      </ol>
      <FormError message={error} />
      <input
        type="file"
        accept=".xlsx,.csv"
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="block w-full text-sm file:mr-3 file:rounded-lg file:border file:border-line file:bg-bg file:px-3 file:py-1.5 file:text-fg"
        data-testid="bulk-file-input"
      />
      <div className="flex justify-between gap-2">
        <button type="button" onClick={onBack} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
          Назад
        </button>
        <button
          type="button"
          disabled={busy || !file}
          onClick={onNext}
          className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
          data-testid="bulk-upload"
        >
          {busy ? 'Проверяем…' : 'Загрузить и проверить'}
        </button>
      </div>
    </section>
  )
}

function PreviewStep({
  preview: p,
  file,
  step,
  onBack,
  onNext,
  onApply,
}: {
  preview: BulkPreview
  file: File | null
  step: number
  onBack: () => void
  onNext: () => void
  onApply: (c: { withWarnings: boolean; confirmed: boolean; word: string | null }) => void
}) {
  const [tab, setTab] = useState<BulkGroup>(p.counts.ok ? 'ok' : p.counts.warn ? 'warn' : 'blocked')
  const [withWarnings, setWithWarnings] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [word, setWord] = useState('')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 15_000)
    return () => clearInterval(t)
  }, [])
  const expired = now > Date.parse(p.expires_at)
  const count = p.counts.ok + (withWarnings ? p.counts.warn : 0)
  const needConfirm = count > BULK_CONFIRM_ABOVE
  const needWord = count > BULK_WORD_ABOVE
  const ready = count > 0 && !expired && (!needConfirm || confirmed) && (!needWord || word.trim().toUpperCase() === BULK_WORD)
  const items = p.items.filter((i) => i.group === tab)

  if (!p.batch_id) {
    return (
      <section className="space-y-3 rounded-xl border border-line bg-card p-4 text-sm" data-testid="bulk-nothing">
        <h2 className="font-semibold">Менять нечего</h2>
        <p className="text-muted">
          {p.file
            ? `В файле ${p.file.rows} строк, новая цена ни у одной не отличается от текущей${p.file.errors.length ? `, ${p.file.errors.length} строк с ошибками` : ''}.`
            : 'По этой стратегии цены выбранных товаров не меняются.'}
        </p>
        {p.file && p.file.errors.length > 0 && <FileErrors p={p} file={file} />}
        <button type="button" onClick={onBack} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
          Назад
        </button>
      </section>
    )
  }

  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-4 text-sm" data-testid="bulk-preview">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="font-semibold">{step === 2 ? 'Что изменится' : 'Подтверждение'}</h2>
        {p.description && <span className="text-muted">{p.description}</span>}
        <span className={`ml-auto text-xs ${expired ? 'text-warn' : 'text-faint'}`}>
          {expired ? 'Предпросмотр устарел — рассчитайте заново' : `Предпросмотр действует до ${time(p.expires_at)}`}
        </span>
      </div>
      {p.warnings.map((w) => (
        <p key={w} className="rounded-lg bg-warn/10 px-3 py-2">
          {w}
        </p>
      ))}
      <div className="flex flex-wrap gap-2" data-testid="bulk-groups">
        {GROUPS.map((g) => (
          <button
            key={g.value}
            type="button"
            onClick={() => setTab(g.value)}
            className={`rounded-full border px-3 py-1 ${tab === g.value ? 'border-accent text-fg' : 'border-line text-muted hover:border-muted'}`}
            data-testid={`bulk-group-${g.value}`}
          >
            {g.label} <Badge tone={g.tone}>{p.counts[g.value]}</Badge>
          </button>
        ))}
        {p.counts.unchanged > 0 && <span className="self-center text-xs text-faint">без изменений: {p.counts.unchanged}</span>}
      </div>
      {p.file && p.file.errors.length > 0 && <FileErrors p={p} file={file} />}

      {step === 2 && (
        <ul className="max-h-[30rem] divide-y divide-line overflow-y-auto rounded-lg border border-line" data-testid="bulk-items">
          {items.map((i) => (
            <PreviewRow key={i.preview_id} i={i} />
          ))}
          {items.length === 0 && <li className="px-3 py-4 text-muted">В этой группе товаров нет.</li>}
        </ul>
      )}

      {step === 3 && (
        <div className="space-y-3" data-testid="bulk-confirm">
          <p className="text-base">
            В Ozon уйдут новые цены <b>{count}</b> товаров — {Math.ceil(count / OZON_BATCH) || 0} запрос(а) по {OZON_BATCH} с паузой 2 с. Ozon
            обработает их за 1–3 минуты.
          </p>
          {p.counts.warn > 0 && (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={withWarnings} onChange={(e) => setWithWarnings(e.target.checked)} className="mt-0.5" data-testid="bulk-with-warnings" />
              <span>
                Включить товары с предупреждениями ({p.counts.warn})
                <span className="block text-xs text-muted">Проверьте их во вкладке «С предупреждением»: например, цена далеко от обычной за месяц.</span>
              </span>
            </label>
          )}
          {needConfirm && (
            <label className="flex items-start gap-2">
              <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} className="mt-0.5" data-testid="bulk-confirmed" />
              <span>Подтверждаю: изменить цены {count} товаров в Ozon</span>
            </label>
          )}
          {needWord && (
            <label className="block text-xs text-muted">
              Больше {BULK_WORD_ABOVE} товаров — для защиты от случайного нажатия введите слово «{BULK_WORD}»
              <input value={word} onChange={(e) => setWord(e.target.value)} className={`${input} mt-1 block w-56`} data-testid="bulk-word" />
            </label>
          )}
        </div>
      )}

      <div className="flex justify-between gap-2">
        <button type="button" onClick={onBack} className="rounded-lg border border-line px-4 py-2 hover:border-muted">
          Назад
        </button>
        {step === 2 ? (
          <button
            type="button"
            disabled={expired || p.counts.ok + p.counts.warn === 0}
            onClick={onNext}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
            data-testid="bulk-to-confirm"
          >
            Дальше — подтверждение
          </button>
        ) : (
          <button
            type="button"
            disabled={!ready}
            onClick={() => onApply({ withWarnings, confirmed, word: needWord ? word : null })}
            className="rounded-lg bg-accent px-4 py-2 font-medium text-white disabled:opacity-50"
            data-testid="bulk-apply"
          >
            {`Применить ${count} цен`}
          </button>
        )}
      </div>
    </section>
  )
}

function PreviewRow({ i }: { i: BulkPreviewItem }) {
  const profitTone = (v: string | null) => (v === null ? 'text-muted' : Number(v) < 0 ? 'text-bad' : 'text-ok')
  return (
    <li className="px-3 py-2">
      <div className="flex flex-wrap items-baseline justify-end gap-x-3 gap-y-0.5">
        <span className="min-w-[12rem] flex-1">
          <span className="break-all font-medium">{i.product.offer_id}</span>
          {i.line && <span className="text-xs text-faint"> · строка {i.line}</span>}
          <span className="block truncate text-xs text-muted">{i.product.name ?? 'Без названия'}</span>
        </span>
        <span className="text-right tabular-nums">
          <span className="text-muted">{rub(i.price_before)} → </span>
          <b>{i.price_after ? rub(i.price_after) : '—'}</b>
          {i.change_pct && <span className="ml-1 text-xs text-muted">{signed(i.change_pct)}</span>}
          {(i.margin_before !== null || i.margin_after !== null) && (
            <span className="block text-xs text-muted">
              маржа {pctText(i.margin_before)} → <span className={profitTone(i.profit_after)}>{pctText(i.margin_after)}</span>
            </span>
          )}
        </span>
      </div>
      {i.note && <p className="mt-1 text-xs text-muted">{i.note}</p>}
      {i.errors.map((e) => (
        <p key={e} className="mt-1 text-xs text-bad">
          {e}
        </p>
      ))}
      {i.warnings.map((w) => (
        <p key={w} className="mt-1 text-xs text-warn">
          {w}
        </p>
      ))}
    </li>
  )
}

function FileErrors({ p, file }: { p: BulkPreview; file: File | null }) {
  const [error, setError] = useState<string | null>(null)
  const errors = p.file?.errors ?? []
  return (
    <div className="space-y-1 rounded-lg bg-bad/10 px-3 py-2" data-testid="bulk-file-errors">
      <p className="font-medium text-bad">Строки с ошибками ({errors.length}) — не изменятся:</p>
      <ul className="space-y-0.5 text-xs">
        {errors.slice(0, 10).map((e) => (
          <li key={e.line}>
            Строка {e.line}
            {e.offer_id ? ` (${e.offer_id})` : ''}: {e.message}
          </li>
        ))}
      </ul>
      {file && (
        <button
          type="button"
          onClick={() => downloadPriceErrors(file).catch((err) => setError(errorMessage(err)))}
          className="text-xs text-accent hover:underline"
        >
          Скачать строки с ошибками — исправить и загрузить снова
        </button>
      )}
      <FormError message={error} />
    </div>
  )
}

function ProgressStep({ status: shown, onRestart }: { status: BulkStatus | null; onRestart: () => void }) {
  const done = shown?.finished
  const total = shown ? shown.batches.reduce((a, b) => a + b.size, 0) : 0
  const sent = shown ? shown.batches.filter((b) => b.state === 'done').reduce((a, b) => a + b.size, 0) : 0
  const failedItems = (shown?.items ?? []).filter((i) => !['applied', 'not_applied', 'queued', 'sending'].includes(i.status))
  return (
    <section className="space-y-4 rounded-xl border border-line bg-card p-4 text-sm" data-testid="bulk-progress">
      <h2 className="font-semibold">{done ? 'Готово' : 'Отправляем в Ozon…'}</h2>
      {total > 0 && (
        <div>
          <div className="h-2 overflow-hidden rounded-full bg-line">
            <div className="h-full bg-accent transition-all" style={{ width: `${Math.round((sent / total) * 100)}%` }} />
          </div>
          <p className="mt-1 text-xs text-muted">
            Отправлено {sent} из {total}
          </p>
        </div>
      )}
      <ul className="space-y-1" data-testid="bulk-batches">
        {(shown?.batches ?? []).map((b) => (
          <li key={b.no} className="flex flex-wrap justify-between gap-2 rounded-lg border border-line px-3 py-2">
            <span>
              Запрос {b.no} из {shown?.batches.length} · {b.size} товаров
            </span>
            <span className={b.state === 'done' ? (b.failed || b.unknown || b.rejected ? 'text-warn' : 'text-ok') : 'text-muted'}>
              {b.state === 'waiting'
                ? 'ждёт очереди'
                : b.state === 'sending'
                  ? 'отправляется…'
                  : `применено ${b.applied}${b.rejected ? `, отказ ${b.rejected}` : ''}${b.failed ? `, ошибка ${b.failed}` : ''}${b.unknown ? `, нет ответа ${b.unknown}` : ''}`}
            </span>
          </li>
        ))}
      </ul>
      {done && shown && (
        <>
          {shown.message && (
            <p className="rounded-lg bg-ok/10 px-3 py-2" role="status" data-testid="bulk-done">
              {shown.message}
            </p>
          )}
          <p className="text-muted">
            Применено {shown.counts.applied}
            {shown.counts.blocked ? ` · не прошло проверку ${shown.counts.blocked}` : ''}
            {shown.counts.rejected ? ` · Ozon отказал ${shown.counts.rejected}` : ''}
            {shown.counts.failed ? ` · ошибка ${shown.counts.failed}` : ''}
            {shown.counts.unknown ? ` · нет ответа ${shown.counts.unknown}` : ''}
          </p>
          {failedItems.length > 0 && (
            <ul className="max-h-64 space-y-1 overflow-y-auto text-xs" data-testid="bulk-not-applied">
              {failedItems.map((i) => (
                <li key={i.preview_id}>
                  <b>{i.product.offer_id}</b> — {STATUS_TEXT[i.status]}
                  {i.error ? `: ${i.error}` : ''}
                </li>
              ))}
            </ul>
          )}
          <button type="button" onClick={onRestart} className="rounded-lg border border-accent px-4 py-2 font-medium text-accent hover:bg-accent/10" data-testid="bulk-restart">
            Новое изменение
          </button>
        </>
      )}
    </section>
  )
}
