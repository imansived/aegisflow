import type { Site, Zone } from './types'

const M_PER_DEG_LAT = (2 * Math.PI * 6_371_000) / 360 // matches contracts/geo.py

export interface Projection {
  /** lat/lon -> SVG coordinates, in metres from the site origin, with y flipped (north is up). */
  toSvg: (lat: number, lon: number) => { x: number; y: number }
  viewBox: { x: number; y: number; w: number; h: number }
  zonePath: (z: Zone) => string
  zoneCenter: (z: Zone) => { x: number; y: number }
  /** Top-left corner of the zone in SVG space, where its label sits clear of the devices. */
  zoneCorner: (z: Zone) => { x: number; y: number }
}

/** A flat local projection, fine at site scale (tens of metres), and the same approximation the backend uses. */
export function makeProjection(site: Site, marginM = 18): Projection {
  const { latitude: lat0, longitude: lon0 } = site.origin
  const cosLat = Math.cos((lat0 * Math.PI) / 180)
  const toSvg = (lat: number, lon: number) => ({
    x: (lon - lon0) * M_PER_DEG_LAT * cosLat,
    y: -((lat - lat0) * M_PER_DEG_LAT),
  })
  const pts = site.zones.flatMap((z) => z.polygon.map((p) => toSvg(p.latitude, p.longitude)))
  const xs = pts.map((p) => p.x)
  const ys = pts.map((p) => p.y)
  const minX = Math.min(...xs) - marginM
  const minY = Math.min(...ys) - marginM
  return {
    toSvg,
    viewBox: { x: minX, y: minY, w: Math.max(...xs) + marginM - minX, h: Math.max(...ys) + marginM - minY },
    zonePath: (z) =>
      z.polygon
        .map((p, i) => {
          const { x, y } = toSvg(p.latitude, p.longitude)
          return `${i ? 'L' : 'M'}${x.toFixed(2)} ${y.toFixed(2)}`
        })
        .join(' ') + ' Z',
    zoneCorner: (z) => {
      const c = z.polygon.map((p) => toSvg(p.latitude, p.longitude))
      return { x: Math.min(...c.map((p) => p.x)), y: Math.min(...c.map((p) => p.y)) }
    },
    zoneCenter: (z) => {
      const c = z.polygon.map((p) => toSvg(p.latitude, p.longitude))
      return { x: c.reduce((s, p) => s + p.x, 0) / c.length, y: c.reduce((s, p) => s + p.y, 0) / c.length }
    },
  }
}
