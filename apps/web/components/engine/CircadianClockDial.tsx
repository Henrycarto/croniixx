'use client'

import { useMemo, useState } from 'react'
import type { DialDrugWindow, DialMarker, DialSleepSegment } from '@croniixx/shared-types'

import {
  annularSectorPath,
  arcSpanHours,
  assignTracks,
  formatHour,
  formatOffset,
  hourToAngle,
  normalizeHour,
  polar,
  radialLine,
} from '@/lib/dial-geometry'

/**
 * The CircadianClockDial.
 *
 * A 24 hour biological clock face. The outer ring is the patient's own sleep
 * architecture, staged by their wearable. The inner rings are the timing
 * windows for their regimen. Two needles run from the centre: a faint one at
 * the wall clock, and a violet one at the patient's biological position. The
 * angle between them is the phase offset, which means the central claim of
 * this product is legible before a single number is read.
 *
 * Drawn in raw SVG. A charting library would impose its own idea of what an
 * axis is, and this is a clock face rather than a plot.
 */

const VIEWBOX = 400
const CENTER = VIEWBOX / 2

const SLEEP_RING_OUTER = 188
const SLEEP_RING_INNER = 168

const TICK_OUTER = 164
const TICK_MINOR_INNER = 156
const TICK_MAJOR_INNER = 148

const HOUR_LABEL_RADIUS = 134

const TRACK_OUTER_START = 122
const TRACK_HEIGHT = 13
const TRACK_GAP = 5

const HUB_RADIUS = 54
const NEEDLE_INNER = 56
const NEEDLE_OUTER = 146

const STAGE_FILL: Record<DialSleepSegment['stage'], string> = {
  // Violet darkens with sleep depth. Deep sleep is the darkest point of the
  // biological night, so it is the darkest point on the ring.
  deep: '#4C1D95',
  rem: '#A78BFA',
  light: '#5B21B6',
  awake: '#E6EAF4',
  unmeasurable: '#1E2942',
}

const STAGE_LABEL: Record<DialSleepSegment['stage'], string> = {
  deep: 'Deep',
  rem: 'REM',
  light: 'Light',
  awake: 'Wake',
  unmeasurable: 'Unstaged',
}

const WINDOW_FILL: Record<DialDrugWindow['status'], string> = {
  optimal: '#10B981',
  acceptable: '#0EA5E9',
  suboptimal: '#F59E0B',
  contraindicated: '#EF4444',
}

export interface CircadianClockDialProps {
  /** Wall clock hour in the patient's timezone, decimal. */
  currentHour: number
  /** Signed minutes from the population reference. Positive is delayed. */
  phaseOffsetMin: number
  sleepSegments: DialSleepSegment[]
  drugWindows: DialDrugWindow[]
  markers?: DialMarker[]
  /** Rendered size in pixels. The SVG scales; the geometry does not change. */
  size?: number
  showCenterReadout?: boolean
  onSelectWindow?: (windowId: string) => void
  selectedWindowId?: string | null
  className?: string
}

export function CircadianClockDial({
  currentHour,
  phaseOffsetMin,
  sleepSegments,
  drugWindows,
  markers = [],
  size = 520,
  showCenterReadout = true,
  onSelectWindow,
  selectedWindowId = null,
  className,
}: CircadianClockDialProps) {
  const [hovered, setHovered] = useState<string | null>(null)

  // The body's own reading of the clock. A patient delayed by ninety minutes
  // is biologically an hour and a half earlier than the wall clock says.
  const biologicalHour = useMemo(
    () => normalizeHour(currentHour - phaseOffsetMin / 60),
    [currentHour, phaseOffsetMin],
  )

  const tracks = useMemo(
    () =>
      assignTracks(
        drugWindows.map((window) => ({
          id: window.id,
          startHour: window.startHour,
          endHour: window.endHour,
        })),
      ),
    [drugWindows],
  )

  const trackCount = useMemo(
    () => (tracks.size === 0 ? 0 : Math.max(...Array.from(tracks.values())) + 1),
    [tracks],
  )

  return (
    <div className={className}>
      <svg
        viewBox={`0 0 ${VIEWBOX} ${VIEWBOX}`}
        width={size}
        height={size}
        role="img"
        aria-label={`Circadian clock dial. Phase offset ${formatOffset(phaseOffsetMin)}. Biological time ${formatHour(biologicalHour)}.`}
        className="max-w-full"
      >
        <title>
          {`Circadian phase ${formatOffset(phaseOffsetMin)} against a standard 24 hour cycle`}
        </title>

        <FaceBackground />
        <SleepRing segments={sleepSegments} />
        <HourTicks />
        <HourLabels />

        {drugWindows.map((window) => (
          <DrugArc
            key={window.id}
            window={window}
            track={tracks.get(window.id) ?? 0}
            active={hovered === window.id || selectedWindowId === window.id}
            onHover={setHovered}
            onSelect={onSelectWindow}
          />
        ))}

        <TrackBaselines count={trackCount} />

        {markers.map((marker) => (
          <PhaseMarker key={`${marker.kind}-${marker.hour}`} marker={marker} />
        ))}

        {/* Clock needle first so the biological needle reads as the primary. */}
        <ClockNeedle hour={currentHour} />
        <PhaseNeedle hour={biologicalHour} />

        <circle cx={CENTER} cy={CENTER} r={HUB_RADIUS} fill="#080B14" stroke="#1E2942" />

        {showCenterReadout ? (
          <CenterReadout
            phaseOffsetMin={phaseOffsetMin}
            biologicalHour={biologicalHour}
            currentHour={currentHour}
          />
        ) : (
          <circle cx={CENTER} cy={CENTER} r={3} fill="#7C3AED" />
        )}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Face
// ---------------------------------------------------------------------------

function FaceBackground() {
  return (
    <>
      <circle cx={CENTER} cy={CENTER} r={SLEEP_RING_OUTER} fill="#0F1629" stroke="#1E2942" />
      <circle
        cx={CENTER}
        cy={CENTER}
        r={SLEEP_RING_INNER}
        fill="#080B14"
        stroke="#1E2942"
        strokeWidth={0.75}
      />
      <circle
        cx={CENTER}
        cy={CENTER}
        r={TICK_OUTER}
        fill="none"
        stroke="#1E2942"
        strokeWidth={0.5}
      />
    </>
  )
}

function SleepRing({ segments }: { segments: DialSleepSegment[] }) {
  if (segments.length === 0) {
    return (
      <text
        x={CENTER}
        y={28}
        textAnchor="middle"
        className="fill-ink-faint font-mono"
        fontSize={9}
        letterSpacing="0.12em"
      >
        NO SLEEP DATA
      </text>
    )
  }

  return (
    <g>
      {segments.map((segment, index) => {
        const span = arcSpanHours(segment.startHour, segment.endHour)
        if (span <= 0) return null
        return (
          <path
            key={`${segment.stage}-${segment.startHour}-${index}`}
            d={annularSectorPath(
              CENTER,
              CENTER,
              SLEEP_RING_INNER,
              SLEEP_RING_OUTER,
              segment.startHour,
              segment.endHour,
            )}
            fill={STAGE_FILL[segment.stage]}
            // Wake is the longest segment of any day. At full strength it
            // dominates the ring and the sleep architecture, which is the part
            // carrying the phase information, stops being readable.
            fillOpacity={segment.stage === 'awake' ? 0.55 : 1}
            stroke="#080B14"
            strokeWidth={0.4}
          >
            <title>
              {`${STAGE_LABEL[segment.stage]}  ${formatHour(segment.startHour)} to ${formatHour(segment.endHour)}`}
            </title>
          </path>
        )
      })}
    </g>
  )
}

function HourTicks() {
  const ticks = Array.from({ length: 24 }, (_, hour) => hour)
  return (
    <g>
      {ticks.map((hour) => {
        const major = hour % 6 === 0
        const line = radialLine(
          CENTER,
          CENTER,
          major ? TICK_MAJOR_INNER : TICK_MINOR_INNER,
          TICK_OUTER,
          hour,
        )
        return (
          <line
            key={hour}
            x1={line.x1}
            y1={line.y1}
            x2={line.x2}
            y2={line.y2}
            stroke={major ? '#55617D' : '#1E2942'}
            strokeWidth={major ? 1.25 : 0.75}
          />
        )
      })}
    </g>
  )
}

function HourLabels() {
  const labels = [0, 6, 12, 18]
  return (
    <g>
      {labels.map((hour) => {
        const point = polar(CENTER, CENTER, HOUR_LABEL_RADIUS, hourToAngle(hour))
        return (
          <text
            key={hour}
            x={point.x}
            y={point.y}
            textAnchor="middle"
            dominantBaseline="central"
            className="fill-ink-faint font-mono"
            fontSize={11}
            letterSpacing="0.06em"
          >
            {String(hour).padStart(2, '0')}
          </text>
        )
      })}
    </g>
  )
}

// ---------------------------------------------------------------------------
// Drug windows
// ---------------------------------------------------------------------------

function trackRadii(track: number): { inner: number; outer: number } {
  const outer = TRACK_OUTER_START - track * (TRACK_HEIGHT + TRACK_GAP)
  return { outer, inner: outer - TRACK_HEIGHT }
}

function TrackBaselines({ count }: { count: number }) {
  if (count === 0) return null
  return (
    <g>
      {Array.from({ length: count }, (_, track) => {
        const { outer } = trackRadii(track)
        return (
          <circle
            key={track}
            cx={CENTER}
            cy={CENTER}
            r={outer - TRACK_HEIGHT / 2}
            fill="none"
            stroke="#141C33"
            strokeWidth={TRACK_HEIGHT}
            strokeOpacity={0.5}
          />
        )
      })}
    </g>
  )
}

function DrugArc({
  window,
  track,
  active,
  onHover,
  onSelect,
}: {
  window: DialDrugWindow
  track: number
  active: boolean
  onHover: (id: string | null) => void
  onSelect?: (id: string) => void
}) {
  const { inner, outer } = trackRadii(track)
  if (inner < HUB_RADIUS) return null

  const path = annularSectorPath(
    CENTER,
    CENTER,
    inner,
    outer,
    window.startHour,
    window.endHour,
  )
  if (!path) return null

  const fill = WINDOW_FILL[window.status]
  const target = radialLine(CENTER, CENTER, inner - 3, outer + 3, window.targetHour)

  return (
    <g
      onMouseEnter={() => onHover(window.id)}
      onMouseLeave={() => onHover(null)}
      onClick={onSelect ? () => onSelect(window.id) : undefined}
      style={{ cursor: onSelect ? 'pointer' : 'default' }}
    >
      <path
        d={path}
        fill={fill}
        fillOpacity={active ? 0.9 : window.status === 'contraindicated' ? 0.55 : 0.7}
        stroke={fill}
        strokeWidth={active ? 1.25 : 0.5}
      >
        <title>
          {`${window.label}  ${formatHour(window.startHour)} to ${formatHour(window.endHour)}  target ${formatHour(window.targetHour)}  ${window.status}`}
        </title>
      </path>

      {/* The target is the moment inside the window the model actually
          recommends. Without it a wide window reads as an interval of equal
          value throughout, which is not what the timing model says. */}
      <line
        x1={target.x1}
        y1={target.y1}
        x2={target.x2}
        y2={target.y2}
        stroke="#E6EAF4"
        strokeWidth={active ? 1.75 : 1}
        strokeOpacity={window.status === 'contraindicated' ? 0.3 : 0.9}
      />
    </g>
  )
}

// ---------------------------------------------------------------------------
// Needles and markers
// ---------------------------------------------------------------------------

function ClockNeedle({ hour }: { hour: number }) {
  const line = radialLine(CENTER, CENTER, NEEDLE_INNER, NEEDLE_OUTER, hour)
  return (
    <line
      x1={line.x1}
      y1={line.y1}
      x2={line.x2}
      y2={line.y2}
      stroke="#55617D"
      strokeWidth={1}
      strokeDasharray="3 4"
    >
      <title>{`Wall clock ${formatHour(hour)}`}</title>
    </line>
  )
}

function PhaseNeedle({ hour }: { hour: number }) {
  const line = radialLine(CENTER, CENTER, NEEDLE_INNER, NEEDLE_OUTER, hour)
  const tip = polar(CENTER, CENTER, NEEDLE_OUTER + 7, hourToAngle(hour))
  const left = polar(CENTER, CENTER, NEEDLE_OUTER - 5, hourToAngle(hour) - 1.6)
  const right = polar(CENTER, CENTER, NEEDLE_OUTER - 5, hourToAngle(hour) + 1.6)

  return (
    <g>
      <line
        x1={line.x1}
        y1={line.y1}
        x2={line.x2}
        y2={line.y2}
        stroke="#7C3AED"
        strokeWidth={2}
      />
      <polygon
        points={`${tip.x},${tip.y} ${left.x},${left.y} ${right.x},${right.y}`}
        fill="#7C3AED"
      >
        <title>{`Circadian position ${formatHour(hour)}`}</title>
      </polygon>
    </g>
  )
}

function PhaseMarker({ marker }: { marker: DialMarker }) {
  const angle = hourToAngle(marker.hour)
  const anchor = polar(CENTER, CENTER, SLEEP_RING_OUTER + 1, angle)
  const tipA = polar(CENTER, CENTER, SLEEP_RING_OUTER + 9, angle - 1.4)
  const tipB = polar(CENTER, CENTER, SLEEP_RING_OUTER + 9, angle + 1.4)
  const color = marker.kind === 'dlmo' ? '#7C3AED' : '#0EA5E9'

  return (
    <g>
      <polygon
        points={`${anchor.x},${anchor.y} ${tipA.x},${tipA.y} ${tipB.x},${tipB.y}`}
        fill={color}
      >
        <title>{`${marker.label} at ${formatHour(marker.hour)}`}</title>
      </polygon>
    </g>
  )
}

// ---------------------------------------------------------------------------
// Centre
// ---------------------------------------------------------------------------

function CenterReadout({
  phaseOffsetMin,
  biologicalHour,
  currentHour,
}: {
  phaseOffsetMin: number
  biologicalHour: number
  currentHour: number
}) {
  const direction =
    phaseOffsetMin > 30 ? 'DELAYED' : phaseOffsetMin < -30 ? 'ADVANCED' : 'ALIGNED'

  return (
    <g>
      <text
        x={CENTER}
        y={CENTER - 24}
        textAnchor="middle"
        className="fill-ink-faint font-mono"
        fontSize={8}
        letterSpacing="0.16em"
      >
        PHASE OFFSET
      </text>
      <text
        x={CENTER}
        y={CENTER - 2}
        textAnchor="middle"
        className="fill-circadian font-mono"
        fontSize={26}
        letterSpacing="0.02em"
      >
        {formatOffset(phaseOffsetMin)}
      </text>
      <text
        x={CENTER}
        y={CENTER + 16}
        textAnchor="middle"
        className="fill-ink-muted font-mono"
        fontSize={8}
        letterSpacing="0.16em"
      >
        {direction}
      </text>
      <line
        x1={CENTER - 34}
        y1={CENTER + 24}
        x2={CENTER + 34}
        y2={CENTER + 24}
        stroke="#1E2942"
      />
      <text
        x={CENTER}
        y={CENTER + 38}
        textAnchor="middle"
        className="fill-ink-muted font-mono"
        fontSize={10}
      >
        {`BIO ${formatHour(biologicalHour)}`}
      </text>
      <text
        x={CENTER}
        y={CENTER + 50}
        textAnchor="middle"
        className="fill-ink-faint font-mono"
        fontSize={9}
      >
        {`CLK ${formatHour(currentHour)}`}
      </text>
    </g>
  )
}

// ---------------------------------------------------------------------------
// Legend
// ---------------------------------------------------------------------------

export function CircadianDialLegend({ className }: { className?: string }) {
  const sleepKeys: DialSleepSegment['stage'][] = ['deep', 'rem', 'light', 'awake']
  const windowKeys: DialDrugWindow['status'][] = [
    'optimal',
    'acceptable',
    'suboptimal',
    'contraindicated',
  ]

  return (
    <div className={className}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <span className="label">Sleep</span>
        {sleepKeys.map((stage) => (
          <span key={stage} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-4"
              style={{ backgroundColor: STAGE_FILL[stage] }}
            />
            <span className="font-mono text-micro text-ink-muted">{STAGE_LABEL[stage]}</span>
          </span>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-6 gap-y-2">
        <span className="label">Window</span>
        {windowKeys.map((status) => (
          <span key={status} className="flex items-center gap-2">
            <span
              className="inline-block h-2.5 w-4"
              style={{ backgroundColor: WINDOW_FILL[status], opacity: 0.75 }}
            />
            <span className="font-mono text-micro capitalize text-ink-muted">{status}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export { STAGE_FILL, WINDOW_FILL, STAGE_LABEL }
