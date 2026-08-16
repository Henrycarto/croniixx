/**
 * The palette from the design brief, in React Native form.
 *
 * Kept in one file so the app and the dashboard cannot drift apart on what
 * emerald means. A patient looking at a green row on their phone and a
 * clinician looking at a green row on the dashboard have to be looking at the
 * same claim about the same window.
 */

export const colors = {
  base: '#080B14',
  surface: '#0F1629',
  surfaceRaised: '#141C33',
  line: '#1E2942',
  lineStrong: '#2A3B5C',

  circadian: '#7C3AED',
  circadianDeep: '#4C1D95',
  circadianRem: '#A78BFA',

  stream: '#0EA5E9',
  drift: '#F59E0B',
  window: '#10B981',
  contra: '#EF4444',

  ink: '#E6EAF4',
  inkMuted: '#8A96B2',
  inkFaint: '#55617D',
} as const

export const windowColor: Record<string, string> = {
  optimal: colors.window,
  acceptable: colors.stream,
  suboptimal: colors.drift,
  contraindicated: colors.contra,
}

export const type = {
  mono: 'SpaceMono',
  sans: 'Inter',
} as const

export const spacing = {
  xs: 4,
  sm: 8,
  md: 16,
  lg: 24,
  xl: 32,
} as const
