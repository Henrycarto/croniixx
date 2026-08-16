import type { Provider } from '@croniixx/shared-types'
import { PROVIDER_HRV_INDEX, PROVIDER_LABELS } from '@croniixx/shared-types'

/**
 * One connected device.
 *
 * Shows which heart rate variability index the device actually reports,
 * because that determines how much the device contributes to the phase
 * estimate. An Apple Watch supplies SDNN, which is converted, and a clinician
 * reading a phase offset should be able to see that from the device list.
 */

export interface DeviceBadgeProps {
  provider: Provider
  active?: boolean
  stale?: boolean
  className?: string
}

const PROVIDER_MARK: Record<Provider, string> = {
  OURA: 'OUR',
  APPLE: 'APL',
  GARMIN: 'GRM',
  WHOOP: 'WHP',
}

export function DeviceBadge({ provider, active = true, stale = false, className }: DeviceBadgeProps) {
  const borderColor = !active ? '#1E2942' : stale ? '#F59E0B' : '#0EA5E9'
  const textColor = !active ? '#55617D' : stale ? '#F59E0B' : '#0EA5E9'

  return (
    <span
      className={`inline-flex items-stretch border ${className ?? ''}`}
      style={{ borderColor }}
      title={`${PROVIDER_LABELS[provider]} reports ${PROVIDER_HRV_INDEX[provider]}`}
    >
      <span
        className="flex items-center px-2 py-1 font-mono text-micro tracking-label"
        style={{ color: textColor, borderRight: `1px solid ${borderColor}` }}
      >
        {PROVIDER_MARK[provider]}
      </span>
      <span className="flex items-center px-2 py-1 text-micro text-ink-muted">
        {PROVIDER_LABELS[provider]}
      </span>
      <span
        className="flex items-center px-2 py-1 font-mono text-micro text-ink-faint"
        style={{ borderLeft: `1px solid ${borderColor}` }}
      >
        {PROVIDER_HRV_INDEX[provider]}
      </span>
    </span>
  )
}
