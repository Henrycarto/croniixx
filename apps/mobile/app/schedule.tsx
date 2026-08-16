import { useCallback, useState } from 'react'
import { ScrollView, StyleSheet, Text, View } from 'react-native'
import { useFocusEffect, useRouter } from 'expo-router'
import type { AdaptiveSchedule } from '@croniixx/shared-types'

import { MedicationCard } from '@/components/MedicationCard'
import { currentSchedule, upcomingDoses, type LocalDose } from '@/db/local'
import { colors, type } from '@/lib/theme'

/**
 * The whole schedule, grouped by day, with its provenance at the bottom.
 *
 * Patients on complex regimens ask where the times came from. Showing the
 * schedule version and whether the coefficients are validated answers that
 * honestly instead of presenting a number as if it were beyond question.
 */

export default function ScheduleScreen() {
  const router = useRouter()
  const [schedule, setSchedule] = useState<AdaptiveSchedule | null>(null)
  const [doses, setDoses] = useState<LocalDose[]>([])

  useFocusEffect(
    useCallback(() => {
      void Promise.all([currentSchedule(), upcomingDoses()]).then(([s, d]) => {
        setSchedule(s)
        setDoses(d)
      })
    }, []),
  )

  const grouped = groupByDay(doses)

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {grouped.length === 0 && (
        <Text style={styles.empty}>No schedule stored on this device yet.</Text>
      )}

      {grouped.map(([day, dayDoses]) => (
        <View key={day}>
          <Text style={styles.dayLabel}>{day}</Text>
          {dayDoses.map((dose) => (
            <MedicationCard
              key={dose.entry_id}
              dose={dose}
              onPress={(entryId) => router.push(`/reminder?entry=${entryId}`)}
            />
          ))}
        </View>
      ))}

      {schedule && (
        <View style={styles.provenance}>
          <Text style={styles.provenanceTitle}>WHERE THESE TIMES COME FROM</Text>
          <Row label="Schedule version" value={String(schedule.schedule_version)} />
          <Row label="Phase offset" value={schedule.phase.offset_display} />
          <Row label="Method" value={schedule.meta.method_version} />
          <Row
            label="Coefficients"
            value={
              schedule.meta.coefficient_source === 'private_validated' ? 'validated' : 'reference'
            }
          />
          <Row label="Valid until" value={schedule.valid_until.slice(0, 16).replace('T', ' ')} />

          <Text style={styles.explainer}>
            Your dose times are calculated from your own sleep and heart rate variability, not
            from a standard clock. When your sleep pattern shifts, these times shift with it.
          </Text>
        </View>
      )}
    </ScrollView>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.row}>
      <Text style={styles.rowLabel}>{label}</Text>
      <Text style={styles.rowValue} numberOfLines={1}>
        {value}
      </Text>
    </View>
  )
}

function groupByDay(doses: LocalDose[]): [string, LocalDose[]][] {
  const buckets = new Map<string, LocalDose[]>()
  for (const dose of doses) {
    const day = new Date(dose.target_time).toDateString().toUpperCase()
    const existing = buckets.get(day)
    if (existing) existing.push(dose)
    else buckets.set(day, [dose])
  }
  return Array.from(buckets.entries())
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.base },
  content: { padding: 16, paddingBottom: 48 },
  dayLabel: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.6,
    color: colors.inkFaint,
    marginTop: 16,
    marginBottom: 8,
  },
  empty: {
    fontFamily: type.sans,
    fontSize: 14,
    color: colors.inkMuted,
    marginTop: 24,
  },
  provenance: {
    borderWidth: 1,
    borderColor: colors.line,
    backgroundColor: colors.surface,
    padding: 16,
    marginTop: 28,
  },
  provenanceTitle: {
    fontFamily: type.mono,
    fontSize: 10,
    letterSpacing: 1.6,
    color: colors.inkFaint,
    marginBottom: 12,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'baseline',
    gap: 12,
    paddingVertical: 6,
    borderTopWidth: 1,
    borderColor: colors.line,
  },
  rowLabel: {
    fontFamily: type.mono,
    fontSize: 11,
    color: colors.inkFaint,
  },
  rowValue: {
    flex: 1,
    textAlign: 'right',
    fontFamily: type.mono,
    fontSize: 12,
    color: colors.ink,
  },
  explainer: {
    fontFamily: type.sans,
    fontSize: 13,
    lineHeight: 20,
    color: colors.inkMuted,
    marginTop: 16,
  },
})
