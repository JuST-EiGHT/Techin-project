import type { Config } from 'tailwindcss'

// Палитра тёмной темы — из CLAUDE.md, правило 9
export default {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0f1117',
        card: '#1a1d27',
        line: '#2a2d3a',
        accent: '#4f8ef7',
        ok: '#22c55e',
        bad: '#f87171',
        warn: '#fbbf24',
      },
    },
  },
  plugins: [],
} satisfies Config
