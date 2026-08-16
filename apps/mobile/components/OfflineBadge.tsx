import { StyleSheet, Text, View } from 'react-native'

import { colors, type } from '@/lib/theme'

/**
 * Connection state, stated without alarm.
 *
 * Offline is a normal operating mode here, not an error, so the badge is
 * informational rather than red. What does deserve attention is a queue of
 * unsent acknowledgements, because that is data the clinician has not seen yet.
 */

export interface OfflineBadgeProps {
  online: boolean
  queuedItems: number
  lastSyncAt: Date | null
}

export function OfflineBadge({ online, queuedItems, lastSyncAt }: OfflineBadgeProps) {
  const accent = online ? colors.stream : colors.inkFaint

  return (
    <View style={[styles.container, { borderColor: queuedItems > 0 ? colors.drift : colors.line }]}>
      <View style={[styles.dot, { backgroundColor: accent }]} />
      <Text style={[styles.label, { color: accent }]}>
        {online ? 'ONLINE' : 'OFFLINE'}
      </Text>
      <Text style={styles.detail}>
        {queuedItems > 0
          ? `${queuedItems} to send`
          : lastSyncAt
            ? `synced ${relative(lastSyncAt)}`
            : 'never synced'}
      </Text>
    </View>
  )
}

function relative(when: Date): string {
  const seconds = Math.max(0, Math.floor((Date.now() - when.getTime()) / 1000))
  if (seconds < 60) return 'just now'
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}

const styles = StyleSheet.create({
  container: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: colors.surface,
  },
  dot: {
    width: 6,
    height: 6,
  },
  label: {
    fontFamily: type.mono,
    fontSize: 11,
    letterSpacing: 1.2,
  },
  detail: {
    fontFamily: type.mono,
    fontSize: 11,
    color: colors.inkFaint,
  },
})
