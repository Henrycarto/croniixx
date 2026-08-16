/**
 * Angular maths for the CircadianClockDial.
 *
 * The dial maps a 24 hour biological cycle onto a full circle: 15 degrees per
 * hour, midnight at the top, running clockwise. Every function here is pure,
 * so the geometry can be checked without rendering anything.
 *
 * The recurring hazard is the wrap at midnight. A sleep segment from 23:10 to
 * 06:40 is seven and a half hours long, not sixteen and a half, and an arc
 * drawn the wrong way round is not a cosmetic error: it puts the patient's
 * biological night on the opposite side of the dial from where it belongs.
 */

export const HOURS_PER_DAY = 24
export const DEGREES_PER_HOUR = 360 / HOURS_PER_DAY

export interface Point {
  x: number
  y: number
}

/** Wrap any hour into [0, 24). */
export function normalizeHour(hour: number): number {
  const wrapped = hour % HOURS_PER_DAY
  return wrapped < 0 ? wrapped + HOURS_PER_DAY : wrapped
}

/** Degrees for a clock hour, with 00:00 at the top and time running clockwise. */
export function hourToAngle(hour: number): number {
  return normalizeHour(hour) * DEGREES_PER_HOUR - 90
}

/**
 * Forward distance in hours from start to end, going clockwise.
 * A zero length span returns 0; a span that covers the whole day returns 24.
 */
export function arcSpanHours(startHour: number, endHour: number): number {
  const start = normalizeHour(startHour)
  const end = normalizeHour(endHour)
  const span = end - start
  return span > 0 ? span : span + HOURS_PER_DAY
}

/**
 * Coordinate precision, in decimal places.
 *
 * Every coordinate this module emits is rounded. Node and the browser do not
 * always agree on the last bit of a trigonometric result, so an unrounded
 * value renders as 64.9000370096276 on the server and 64.90003700962762 in
 * the client, and React treats that as a hydration mismatch and throws away
 * the server render. Four decimals on a 400 unit viewBox is far below one
 * device pixel, so nothing is lost.
 */
const COORDINATE_PRECISION = 4

export function roundCoordinate(value: number): number {
  const factor = 10 ** COORDINATE_PRECISION
  return Math.round(value * factor) / factor
}

export function polar(cx: number, cy: number, radius: number, angleDeg: number): Point {
  const rad = (angleDeg * Math.PI) / 180
  return {
    x: roundCoordinate(cx + radius * Math.cos(rad)),
    y: roundCoordinate(cy + radius * Math.sin(rad)),
  }
}

function fixed(value: number): string {
  return roundCoordinate(value).toFixed(3)
}

/**
 * Path for a ring segment between two clock hours.
 *
 * A span at or beyond a full day is drawn as two half circles, because a
 * single SVG arc cannot express 360 degrees: the start and end points would
 * coincide and the renderer draws nothing at all.
 */
export function annularSectorPath(
  cx: number,
  cy: number,
  innerRadius: number,
  outerRadius: number,
  startHour: number,
  endHour: number,
): string {
  const spanHours = arcSpanHours(startHour, endHour)
  if (spanHours <= 0) return ''

  if (spanHours >= HOURS_PER_DAY - 1e-6) {
    const half = HOURS_PER_DAY / 2
    return [
      annularSectorPath(cx, cy, innerRadius, outerRadius, startHour, startHour + half),
      annularSectorPath(cx, cy, innerRadius, outerRadius, startHour + half, startHour),
    ].join(' ')
  }

  const startAngle = hourToAngle(startHour)
  const endAngle = startAngle + spanHours * DEGREES_PER_HOUR
  const largeArc = spanHours * DEGREES_PER_HOUR > 180 ? 1 : 0

  const outerStart = polar(cx, cy, outerRadius, startAngle)
  const outerEnd = polar(cx, cy, outerRadius, endAngle)
  const innerEnd = polar(cx, cy, innerRadius, endAngle)
  const innerStart = polar(cx, cy, innerRadius, startAngle)

  return [
    `M ${fixed(outerStart.x)} ${fixed(outerStart.y)}`,
    `A ${fixed(outerRadius)} ${fixed(outerRadius)} 0 ${largeArc} 1 ${fixed(outerEnd.x)} ${fixed(outerEnd.y)}`,
    `L ${fixed(innerEnd.x)} ${fixed(innerEnd.y)}`,
    `A ${fixed(innerRadius)} ${fixed(innerRadius)} 0 ${largeArc} 0 ${fixed(innerStart.x)} ${fixed(innerStart.y)}`,
    'Z',
  ].join(' ')
}

/** A straight radial line, used for ticks and for the target marker on an arc. */
export function radialLine(
  cx: number,
  cy: number,
  innerRadius: number,
  outerRadius: number,
  hour: number,
): { x1: number; y1: number; x2: number; y2: number } {
  const angle = hourToAngle(hour)
  const inner = polar(cx, cy, innerRadius, angle)
  const outer = polar(cx, cy, outerRadius, angle)
  return { x1: inner.x, y1: inner.y, x2: outer.x, y2: outer.y }
}

export interface TrackedInterval {
  id: string
  startHour: number
  endHour: number
}

const MINUTES_PER_DAY = HOURS_PER_DAY * 60

/**
 * Assign each interval to a concentric track so no two overlapping windows
 * are drawn on top of each other.
 *
 * Occupancy is tested minute by minute rather than by comparing endpoints,
 * because an interval that crosses midnight is two ranges in linear terms and
 * the endpoint comparison for that case is where circular layout code usually
 * goes wrong. A day is 1440 minutes, so exactness costs nothing here.
 */
export function assignTracks(intervals: TrackedInterval[]): Map<string, number> {
  const assignment = new Map<string, number>()
  const tracks: Uint8Array[] = []

  const ordered = [...intervals].sort(
    (a, b) => normalizeHour(a.startHour) - normalizeHour(b.startHour),
  )

  for (const interval of ordered) {
    const spanMinutes = Math.max(
      1,
      Math.round(arcSpanHours(interval.startHour, interval.endHour) * 60),
    )
    const startMinute = Math.round(normalizeHour(interval.startHour) * 60) % MINUTES_PER_DAY

    let placed = false
    for (let trackIndex = 0; trackIndex < tracks.length; trackIndex += 1) {
      if (fits(tracks[trackIndex], startMinute, spanMinutes)) {
        occupy(tracks[trackIndex], startMinute, spanMinutes)
        assignment.set(interval.id, trackIndex)
        placed = true
        break
      }
    }

    if (!placed) {
      const track = new Uint8Array(MINUTES_PER_DAY)
      occupy(track, startMinute, spanMinutes)
      tracks.push(track)
      assignment.set(interval.id, tracks.length - 1)
    }
  }

  return assignment
}

function fits(track: Uint8Array, startMinute: number, spanMinutes: number): boolean {
  for (let offset = 0; offset < spanMinutes; offset += 1) {
    if (track[(startMinute + offset) % MINUTES_PER_DAY] === 1) return false
  }
  return true
}

function occupy(track: Uint8Array, startMinute: number, spanMinutes: number): void {
  for (let offset = 0; offset < spanMinutes; offset += 1) {
    track[(startMinute + offset) % MINUTES_PER_DAY] = 1
  }
}

/** Decimal hours to a 24 hour clock string. */
export function formatHour(hour: number): string {
  const normalized = normalizeHour(hour)
  const hours = Math.floor(normalized)
  const minutes = Math.round((normalized - hours) * 60)
  if (minutes === 60) {
    return `${String((hours + 1) % HOURS_PER_DAY).padStart(2, '0')}:00`
  }
  return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
}

/** Signed minutes rendered the way a chronobiologist writes them. */
export function formatOffset(minutes: number): string {
  const sign = minutes >= 0 ? '+' : '-'
  const magnitude = Math.abs(Math.round(minutes))
  const hours = Math.floor(magnitude / 60)
  const remainder = magnitude % 60
  return `${sign}${String(hours).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
}

/** Convert an ISO timestamp into decimal local hours for a given timezone. */
export function isoToLocalHour(iso: string, timeZone: string): number {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 0

  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  }).formatToParts(date)

  const read = (type: string): number => Number(parts.find((p) => p.type === type)?.value ?? '0')
  // Intl renders midnight as 24 in some locales, which would wrap the hour to
  // the far side of the dial.
  const hour = read('hour') % 24
  return hour + read('minute') / 60 + read('second') / 3600
}
