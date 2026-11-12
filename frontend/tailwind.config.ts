import type { Config } from 'tailwindcss'

// Дизайн-система (шаг 10.27a, docs/DESIGN.md). Цвета — токены темы из CSS-переменных (src/app/globals.css): тёмная и
// светлая. В компонентах только они — без жёстких hex и slate-* (CLAUDE.md, правило 9).
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        card: token('card'),
        soft: token('soft'),
        line: token('line'),
        fg: token('fg'),
        muted: token('muted'),
        faint: token('faint'),
        accent: token('accent'),
        'on-accent': token('on-accent'),
        ok: token('ok'),
        bad: token('bad'),
        warn: token('warn'),
        chart: {
          1: token('chart-1'),
          2: token('chart-2'),
          3: token('chart-3'),
          4: token('chart-4'),
          5: token('chart-5'),
          6: token('chart-6'),
        },
      },
      // Скругления мягче, чем по умолчанию: элементы управления — lg (10 px), карточки и панели — xl (14 px).
      borderRadius: {
        lg: '0.625rem',
        xl: '0.875rem',
        '2xl': '1.125rem',
      },
      boxShadow: {
        // карточка: едва заметная, глубину в основном даёт рамка
        card: '0 1px 2px rgb(var(--shadow) / 0.05), 0 4px 16px -4px rgb(var(--shadow) / 0.06)',
        // всплывающее: меню, подсказки, окна
        pop: '0 12px 32px -8px rgb(var(--shadow) / 0.28), 0 2px 6px rgb(var(--shadow) / 0.08)',
      },
    },
  },
  plugins: [],
} satisfies Config
