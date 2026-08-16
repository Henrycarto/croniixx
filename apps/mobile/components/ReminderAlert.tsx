import { Pressable, StyleSheet, Text, View } from 'react-native'

import type { LocalDose } from '@/db/local'
import { colors, type, windowColor } from '@/lib/theme'

/**
 * The dose confirmation surface.
 *
 * Two actions and no third. Adding "remind me later" would let a patient defer
 * past the end of a window that exists for a pharmacological reason, and the
 * app would have helped them miss it. A dose outside its window is a decision
 * for the patient and their clinician, not a button.
 */

export interface ReminderAlertProps {
  dose: LocalDose
  now?: Date
  onTake: () => void
  onSkip: () => void
  busy?: boolean
}

export function ReminderAlert({ dose, now = new Date(), onTake, onSkip, busy }: ReminderAlertProps) {
  const accent = windowColor[dose.window_status] ?? colors.circadian
  const start = new Date(dose.window_start)
  const end = new Date(dose.window_end)
  const target = new Date(dose.target_time)

  const minutesLeft = Math.round((end.getTime() - now.getTime()) / 60000)
  const inWindow = now >= start && now <= end

  return (
    <View style={styles.container}>
      <View style={[styles.statusStrip, { backgroundColor: accent }]} />

      <Text style={styles.label}>
        {inWindow ? 'WINDOW OPEN' : now < start ? 'NOT YET OPEN' : 'WINDOW CLOSED'}
      </Text>

      <Text style={styles.name}>{dose.display_name}</Text>
      <Text style={styles.dosage}>
        {dose.dose_amount} {dose.dose_unit}
      </Text>

      <View style={styles.metrics}>
        <Metric label="Opens" value={clock(start)} />
        <Metric label="Target" value={clock(target)} accent />
        <Metric label="Closes" value={clock(end)} />
      </View>

      {inWindow && (
        <Text style={styles.countdown}>
          {minutesLeft > 0 ? `${minutesLeft} minutes left in this window` : 'closing now'}
        </Text>
      )}

      {dose.rationale ? <Text style={styles.rationale}>{dose.rationale}</Text> : null}

      <View style={styles.actions}>
        <Pressable
          disabled={busy}
          onPress={onTake}
          style={({ pressed }) => [
            styles.primary,
            pressed && styles.pressed,
            busy && styles.disabled,
          ]}
        >
          <Text style={styles.primaryLabel}>TAKEN</Text>
        </Pressable>

        <Pressable
          disabled={busy}
          onPress={onSkip}
          style={({ pressed }) => [
            styles.secondary,
            pressed && styles.pressed,
            busy && styles.disabled,
          ]}
        >
          <Text style={styles.secondaryLabel}>SKIP</Text>
        </Pressable>
      </View>

      <Text style={styles.note}>
        Recorded on this device straight away. It reaches your clinician the next time you
        have a connection.
      </Text>
    </View>
  )
}

function Metric({ label, value, accent }: { label: string; value: string; accent?: boolean }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, accent && { color: colors.circadian }]}>{value}</Text>
    </View>
  )
}

function clock(date: Date): string {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

const styles = StyleSheet.create({
  container: {
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    padding: 20,
    gap: 6,
  },
  statusStrip: {
    height: 3,
    marginBottom: 10,
    marginHorizontal: -20,
    marginTop: -20,
  },
  label: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.6,
    color: colors.inkFaint,
  },
  name: {
    fontFamily: type.sans,
    fontSize: 26,
    color: colors.ink,
  },
  dosage: {
    fontFamily: type.mono,
    fontSize: 15,
    color: colors.inkMuted,
  },
  metrics: {
    flexDirection: 'row',
    borderTopWidth: 1,
    borderBottomWidth: 1,
    borderColor: colors.line,
    marginTop: 14,
  },
  metric: {
    flex: 1,
    paddingVertical: 12,
    gap: 4,
  },
  metricLabel: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.2,
    color: colors.inkFaint,
  },
  metricValue: {
    fontFamily: type.mono,
    fontSize: 20,
    color: colors.ink,
  },
  countdown: {
    fontFamily: type.mono,
    fontSize: 12,
    color: colors.circadian,
    marginTop: 10,
  },
  rationale: {
    fontFamily: type.sans,
    fontSize: 13,
    lineHeight: 20,
    color: colors.inkMuted,
    marginTop: 8,
  },
  actions: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 20,
  },
  primary: {
    flex: 2,
    borderWidth: 1,
    borderColor: colors.circadian,
    backgroundColor: 'rgba(124, 58, 237, 0.12)',
    paddingVertical: 16,
    alignItems: 'center',
  },
  primaryLabel: {
    fontFamily: type.mono,
    fontSize: 13,
    letterSpacing: 1.6,
    color: colors.circadian,
  },
  secondary: {
    flex: 1,
    borderWidth: 1,
    borderColor: colors.lineStrong,
    paddingVertical: 16,
    alignItems: 'center',
  },
  secondaryLabel: {
    fontFamily: type.mono,
    fontSize: 13,
    letterSpacing: 1.6,
    color: colors.inkMuted,
  },
  pressed: {
    opacity: 0.7,
  },
  disabled: {
    opacity: 0.4,
  },
  note: {
    fontFamily: type.sans,
    fontSize: 12,
    lineHeight: 18,
    color: colors.inkFaint,
    marginTop: 14,
  },
})
