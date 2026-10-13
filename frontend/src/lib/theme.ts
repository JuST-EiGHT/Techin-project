// Тема интерфейса. Выбор пользователя: тёмная (по умолчанию), светлая или как в системе.
export type ThemePreference = 'dark' | 'light' | 'system'
export type Theme = 'dark' | 'light'

export const THEME_STORAGE_KEY = 'theme'
export const DEFAULT_THEME: ThemePreference = 'dark'

export function resolveTheme(pref: ThemePreference): Theme {
  if (pref !== 'system') return pref
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
}

export function applyTheme(pref: ThemePreference): Theme {
  const theme = resolveTheme(pref)
  document.documentElement.dataset.theme = theme
  return theme
}

export function readStoredPreference(): ThemePreference {
  try {
    const v = localStorage.getItem(THEME_STORAGE_KEY)
    return v === 'light' || v === 'dark' || v === 'system' ? v : DEFAULT_THEME
  } catch {
    return DEFAULT_THEME
  }
}

export function storePreference(pref: ThemePreference) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, pref)
  } catch {
    /* приватный режим — тема всё равно сохранится в настройках аккаунта */
  }
}

/** Выполняется в <head> до отрисовки страницы — без вспышки тёмной темы у светлой. */
export const THEME_INIT_SCRIPT = `(function(){try{var p=localStorage.getItem('${THEME_STORAGE_KEY}')||'${DEFAULT_THEME}';var t=p==='system'?(matchMedia('(prefers-color-scheme: light)').matches?'light':'dark'):(p==='light'?'light':'dark');document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme='dark'}})()`
