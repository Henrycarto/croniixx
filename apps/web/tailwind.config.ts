import type { Config } from 'tailwindcss'

// The palette is fixed by the design brief. Violet is the circadian colour and
// stands for biological night, not for decoration; emerald and red are window
// states and must not be reused for anything else, or the dial stops being
// readable at a glance.
const config: Config = {
  content: [
    './app/**/*.{ts,tsx}',
    './components/**/*.{ts,tsx}',
    './lib/**/*.{ts,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        base: '#080B14',
        surface: '#0F1629',
        'surface-raised': '#141C33',
        line: '#1E2942',
        'line-strong': '#2A3B5C',
        circadian: {
          DEFAULT: '#7C3AED',
          deep: '#4C1D95',
          rem: '#A78BFA',
          dim: '#5B21B6',
        },
        stream: '#0EA5E9',
        drift: '#F59E0B',
        window: '#10B981',
        contra: '#EF4444',
        ink: {
          DEFAULT: '#E6EAF4',
          muted: '#8A96B2',
          faint: '#55617D',
        },
      },
      fontFamily: {
        sans: ['var(--font-inter)', 'system-ui', 'sans-serif'],
        mono: ['var(--font-space-mono)', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        none: '0',
        sm: '2px',
        DEFAULT: '2px',
      },
      letterSpacing: {
        label: '0.08em',
      },
      fontSize: {
        micro: ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
}

export default config
