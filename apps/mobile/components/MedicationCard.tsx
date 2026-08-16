import { Pressable, StyleSheet, Text, View } from 'react-native'

import type { LocalDose } from '@/db/local'
import { colors, type, windowColor } from '@/lib/theme'

/**
 * One dose in the patient's day.
 *
 * The window, not the exact minute, is the instruction. A patient told "07:42"
 * either hits it or feels they failed; a patient told "between 07:05 and 09:05,
 * best at 07:42" has something they can actually comply with. The target is
 * still shown because it is what the model recommends.
 */

export interface MedicationCardProps {
  dose: LocalDose
  now?: Date
  onPress?: (entryId: string) => void
}

export function MedicationCard({ dose, now = new Date(), onPress }: MedicationCardProps) {
  const accent = windowColor[dose.window_status] ?? colors.inkMuted
  const start = new Date(dose.window_start)
  const end = new Date(dose.window_end)
  const target = new Date(dose.target_time)

  const taken = dose.status === 'taken'
  const skipped = dose.status === 'skipped'
  const open = now >= start && now <= end
  const past = now > end && !taken && !skipped

  return (
    <Pressable
      onPress={onPress ? () => onPress(dose.entry_id) : undefined}
      style={({ pressed }) => [
        styles.card,
        { borderColor: open ? accent : colors.line },
        pressed && styles.pressed,
      ]}
    >
      <View style={[styles.accent, { backgroundColor: taken || skipped ? colors.line : accent }]} />

      <View style={styles.body}>
        <View style={styles.headerRow}>
          <Text style={styles.name} numberOfLines={1}>
            {dose.display_name}
          </Text>
          <Text style={[styles.target, { color: taken ? colors.inkFaint : colors.ink }]}>
            {clock(target)}
          </Text>
        </View>

        <View style={styles.headerRow}>
          <Text style={styles.dosage}>
            {dose.dose_amount} {dose.dose_unit}
          </Text>
          <Text style={styles.window}>
            {clock(start)} to {clock(end)}
          </Text>
        </View>

        <Text style={[styles.state, { color: stateColor(taken, skipped, open, past) }]}>
          {stateLabel(taken, skipped, open, past, dose.taken_at)}
        </Text>
      </View>
    </Pressable>
  )
}

function stateLabel(
  taken: boolean,
  skipped: boolean,
  open: boolean,
  past: boolean,
  takenAt: string | null,
): string {
  if (taken) return `TAKEN ${takenAt ? clock(new Date(takenAt)) : ''}`.trim()
  if (skipped) return 'SKIPPED'
  if (open) return 'WINDOW OPEN NOW'
  if (past) return 'WINDOW CLOSED'
  return 'UPCOMING'
}

function stateColor(taken: boolean, skipped: boolean, open: boolean, past: boolean): string {
  if (taken) return colors.window
  if (skipped) return colors.inkFaint
  if (open) return colors.circadian
  if (past) return colors.drift
  return colors.inkFaint
}

function clock(date: Date): string {
  return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
}

const styles = StyleSheet.create({
  card: {
    flexDirection: 'row',
    borderWidth: 1,
    backgroundColor: colors.surface,
    marginBottom: 8,
  },
  pressed: {
    backgroundColor: colors.surfaceRaised,
  },
  accent: {
    width: 3,
  },
  body: {
    flex: 1,
    paddingHorizontal: 14,
    paddingVertical: 12,
    gap: 4,
  },
  headerRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    gap: 12,
  },
  name: {
    flex: 1,
    fontFamily: type.sans,
    fontSize: 16,
    color: colors.ink,
  },
  target: {
    fontFamily: type.mono,
    fontSize: 18,
  },
  dosage: {
    fontFamily: type.mono,
    fontSize: 12,
    color: colors.inkMuted,
  },
  window: {
    fontFamily: type.mono,
    fontSize: 12,
    color: colors.inkFaint,
  },
  state: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.2,
    marginTop: 2,
  },
})
