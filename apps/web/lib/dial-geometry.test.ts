import { describe, expect, it } from 'vitest'

import {
  annularSectorPath,
  arcSpanHours,
  assignTracks,
  formatHour,
  formatOffset,
  hourToAngle,
  isoToLocalHour,
  normalizeHour,
  polar,
  roundCoordinate,
} from './dial-geometry'

/**
 * The dial is the product argument. An arc drawn on the wrong side of the
 * clock face is not a cosmetic bug, so the wrap at midnight, the direction of
 * travel, and the coordinate stability all get explicit tests.
 */

describe('normalizeHour', () => {
  it('wraps negatives forward', () => {
    expect(normalizeHour(-1)).toBeCloseTo(23)
    expect(normalizeHour(-13.5)).toBeCloseTo(10.5)
  })

  it('wraps values past a day', () => {
    expect(normalizeHour(25)).toBeCloseTo(1)
    expect(normalizeHour(48)).toBeCloseTo(0)
  })
})

describe('hourToAngle', () => {
  it('puts midnight at the top', () => {
    expect(hourToAngle(0)).toBe(-90)
  })

  it('runs clockwise so 06:00 is on the right and 18:00 on the left', () => {
    expect(hourToAngle(6)).toBe(0)
    expect(hourToAngle(12)).toBe(90)
    expect(hourToAngle(18)).toBe(180)
  })
})

describe('polar', () => {
  it('places midnight directly above the centre', () => {
    const point = polar(200, 200, 100, hourToAngle(0))
    expect(point.x).toBeCloseTo(200)
    expect(point.y).toBeCloseTo(100)
  })

  it('places noon directly below the centre', () => {
    const point = polar(200, 200, 100, hourToAngle(12))
    expect(point.x).toBeCloseTo(200)
    expect(point.y).toBeCloseTo(300)
  })

  it('rounds so the server and the browser agree', () => {
    // Unrounded trigonometry differs in the last bit between Node and V8 in
    // the browser, which React reports as a hydration mismatch.
    const point = polar(200, 200, 164, hourToAngle(7))
    expect(String(point.x)).toBe(String(roundCoordinate(point.x)))
    expect(String(point.y).split('.')[1]?.length ?? 0).toBeLessThanOrEqual(4)
  })
})

describe('arcSpanHours', () => {
  it('measures a night that crosses midnight the short way', () => {
    expect(arcSpanHours(23.17, 6.67)).toBeCloseTo(7.5)
  })

  it('does not measure it the long way', () => {
    expect(arcSpanHours(23.17, 6.67)).toBeLessThan(12)
  })

  it('treats a zero length span as a whole day rather than nothing', () => {
    expect(arcSpanHours(4, 4)).toBe(24)
  })

  it('measures an ordinary daytime window directly', () => {
    expect(arcSpanHours(9, 11)).toBeCloseTo(2)
  })
})

describe('annularSectorPath', () => {
  it('produces a closed path', () => {
    const path = annularSectorPath(200, 200, 168, 188, 23, 7)
    expect(path.startsWith('M ')).toBe(true)
    expect(path.trim().endsWith('Z')).toBe(true)
  })

  it('sets the large arc flag past half a day', () => {
    const short = annularSectorPath(200, 200, 100, 120, 0, 4)
    const long = annularSectorPath(200, 200, 100, 120, 0, 20)
    expect(short).toContain('0 0 1')
    expect(long).toContain('0 1 1')
  })

  it('splits a full circle into two arcs, since one cannot express 360 degrees', () => {
    const path = annularSectorPath(200, 200, 100, 120, 4, 4)
    expect(path.match(/M /g)?.length).toBe(2)
  })

  it('returns nothing for a span that does not exist', () => {
    expect(annularSectorPath(200, 200, 100, 120, 4, 4.0000001)).not.toBe('')
  })
})

describe('assignTracks', () => {
  it('keeps disjoint windows on one track', () => {
    const tracks = assignTracks([
      { id: 'a', startHour: 8, endHour: 10 },
      { id: 'b', startHour: 14, endHour: 16 },
    ])
    expect(tracks.get('a')).toBe(0)
    expect(tracks.get('b')).toBe(0)
  })

  it('separates overlapping windows', () => {
    const tracks = assignTracks([
      { id: 'a', startHour: 8, endHour: 12 },
      { id: 'b', startHour: 10, endHour: 14 },
    ])
    expect(tracks.get('a')).not.toBe(tracks.get('b'))
  })

  it('detects an overlap that only exists because a window crosses midnight', () => {
    const tracks = assignTracks([
      { id: 'night', startHour: 22, endHour: 3 },
      { id: 'earlyMorning', startHour: 1, endHour: 5 },
    ])
    expect(tracks.get('night')).not.toBe(tracks.get('earlyMorning'))
  })

  it('reuses a track once the conflict has passed', () => {
    const tracks = assignTracks([
      { id: 'a', startHour: 0, endHour: 6 },
      { id: 'b', startHour: 3, endHour: 9 },
      { id: 'c', startHour: 12, endHour: 15 },
    ])
    expect(tracks.get('c')).toBe(0)
  })
})

describe('formatHour', () => {
  it('pads to a 24 hour clock', () => {
    expect(formatHour(9.5)).toBe('09:30')
    expect(formatHour(0)).toBe('00:00')
    expect(formatHour(23.99)).toBe('23:59')
  })

  it('rolls over rather than printing a sixtieth minute', () => {
    expect(formatHour(23.999)).toBe('00:00')
    expect(formatHour(9.999)).toBe('10:00')
  })

  it('wraps rather than producing a 24th hour', () => {
    expect(formatHour(24)).toBe('00:00')
    expect(formatHour(25.25)).toBe('01:15')
  })
})

describe('formatOffset', () => {
  it('always carries a sign', () => {
    expect(formatOffset(72)).toBe('+01:12')
    expect(formatOffset(-72)).toBe('-01:12')
    expect(formatOffset(0)).toBe('+00:00')
  })
})

describe('isoToLocalHour', () => {
  it('converts into the requested zone', () => {
    // 06:00 UTC in April is 08:00 in Vienna.
    expect(isoToLocalHour('2026-04-10T06:00:00Z', 'Europe/Vienna')).toBeCloseTo(8, 5)
    expect(isoToLocalHour('2026-04-10T06:00:00Z', 'UTC')).toBeCloseTo(6, 5)
  })

  it('renders midnight as zero rather than twenty four', () => {
    expect(isoToLocalHour('2026-04-10T00:00:00Z', 'UTC')).toBe(0)
  })

  it('returns zero for an unparseable timestamp instead of NaN', () => {
    expect(isoToLocalHour('not a date', 'UTC')).toBe(0)
  })
})
