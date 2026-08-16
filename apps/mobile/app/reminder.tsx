import { useCallback, useState } from 'react'
import { ScrollView, StyleSheet, Text } from 'react-native'
import { useFocusEffect, useLocalSearchParams, useRouter } from 'expo-router'

import { ReminderAlert } from '@/components/ReminderAlert'
import { doseById, recordDose, type LocalDose } from '@/db/local'
import { cancelForDose } from '@/lib/notifications'
import { drainOutbox } from '@/lib/sync'
import { colors, type } from '@/lib/theme'

/**
 * The dose screen a notification opens onto.
 *
 * Recording writes to SQLite and returns immediately. The outbox drain that
 * follows is fired without awaiting it, because the patient has already done
 * the thing that matters and should not watch a spinner to find out whether
 * the server agrees.
 */

export default function ReminderScreen() {
  const router = useRouter()
  const { entry } = useLocalSearchParams<{ entry?: string }>()
  const [dose, setDose] = useState<LocalDose | null>(null)
  const [busy, setBusy] = useState(false)

  useFocusEffect(
    useCallback(() => {
      if (!entry) return
      void doseById(entry).then(setDose)
    }, [entry]),
  )

  const record = useCallback(
    async (status: 'taken' | 'skipped') => {
      if (!dose || busy) return
      setBusy(true)

      await recordDose(dose.entry_id, status)
      // The remaining alerts for this dose are no longer wanted. Cancelling
      // them locally is what stops the phone chasing a dose already taken.
      await cancelForDose(dose)

      void drainOutbox()
      setBusy(false)
      router.back()
    },
    [dose, busy, router],
  )

  if (!dose) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
        <Text style={styles.missing}>
          That dose is not on this device. It may belong to a schedule that has since been
          replaced.
        </Text>
      </ScrollView>
    )
  }

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <ReminderAlert
        dose={dose}
        busy={busy}
        onTake={() => void record('taken')}
        onSkip={() => void record('skipped')}
      />
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.base },
  content: { padding: 16 },
  missing: {
    fontFamily: type.sans,
    fontSize: 14,
    lineHeight: 21,
    color: colors.inkMuted,
    marginTop: 24,
  },
})
