import type { Config } from 'tailwindcss'

// Цвета — токены темы из CSS-переменных (src/app/globals.css): тёмная и светлая.
// В компонентах только они — без жёстких hex и slate-* (CLAUDE.md, правило 9).
const token = (name: string) => `rgb(var(--${name}) / <alpha-value>)`

export default {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: token('bg'),
        card: token('card'),
        line: token('line'),
        fg: token('fg'),
        muted: token('muted'),
        faint: token('faint'),
        accent: token('accent'),
        ok: token('ok'),
        bad: token('bad'),
        warn: token('warn'),
      },
    },
  },
  plugins: [],
} satisfies Config
