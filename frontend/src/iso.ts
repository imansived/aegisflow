import { makeProjection } from './geo'
import type { Site, Zone } from './types'

// True isometric view (30 degrees). World coordinates are the site's metres: u east, v SOUTH (SVG convention
// from geo.ts), z up. The scene is drawn in SVG, projected by hand, so every element stays crisp, clickable
// and testable, with no CSS 3D to fight.
const C = Math.cos(Math.PI / 6)
const S = 0.5

export interface Pt { x: number; y: number }

export interface ZoneSolid {
  top: Pt[]
  sides: Pt[][] // the visible side faces, nearest-to-viewer last
  floor: Pt[]
  center: Pt
  depth: number // for painter's order: larger is nearer the viewer
}

export interface Iso {
  /** lat/lon (+ height in metres) -> screen point */
  at: (lat: number, lon: number, z?: number) => Pt
  /** a world point offset (du, dv metres) from a lat/lon, at height z */
  near: (lat: number, lon: number, du: number, dv: number, z?: number) => Pt
  viewBox: { x: number; y: number; w: number; h: number }
  solid: (z: Zone, height: number) => ZoneSolid
  ring: (lat: number, lon: number, radiusM: number, z?: number, n?: number) => Pt[]
  /** Floor grid lines, every `stepM` metres, extending `padM` past the site. Each is a [from, to] pair. */
  grid: (stepM?: number, padM?: number) => [Pt, Pt][]
}

export const path = (pts: Pt[], close = true): string =>
  pts.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)} ${p.y.toFixed(2)}`).join(' ') + (close ? ' Z' : '')

export function makeIso(site: Site, margin = 16, maxHeight = 34): Iso {
  const flat = makeProjection(site, 0) // x east, y = -north: that is, (u, v)
  const proj = (u: number, v: number, z = 0): Pt => ({ x: (u - v) * C, y: (u + v) * S - z })
  const at = (lat: number, lon: number, z = 0): Pt => {
    const q = flat.toSvg(lat, lon)
    return proj(q.x, q.y, z)
  }
  const near = (lat: number, lon: number, du: number, dv: number, z = 0): Pt => {
    const q = flat.toSvg(lat, lon)
    return proj(q.x + du, q.y + dv, z)
  }

  const corners: Pt[] = site.zones.flatMap((zn) =>
    zn.polygon.flatMap((p) => [at(p.latitude, p.longitude, 0), at(p.latitude, p.longitude, maxHeight)]),
  )
  const xs = corners.map((c) => c.x)
  const ys = corners.map((c) => c.y)
  const minX = Math.min(...xs) - margin * 1.6
  const minY = Math.min(...ys) - margin
  const viewBox = { x: minX, y: minY, w: Math.max(...xs) + margin * 1.6 - minX, h: Math.max(...ys) + margin - minY }

  const solid = (zn: Zone, height: number): ZoneSolid => {
    const floor = zn.polygon.map((p) => at(p.latitude, p.longitude, 0))
    const top = zn.polygon.map((p) => at(p.latitude, p.longitude, height))
    const cx = floor.reduce((s, p) => s + p.x, 0) / floor.length
    const cy = floor.reduce((s, p) => s + p.y, 0) / floor.length
    // An edge faces the viewer when its midpoint sits lower on screen than the footprint's centre.
    const sides: Pt[][] = []
    for (let i = 0; i < floor.length; i++) {
      const j = (i + 1) % floor.length
      const mid = (floor[i].y + floor[j].y) / 2
      if (mid > cy + 0.01) sides.push([floor[i], floor[j], top[j], top[i]])
    }
    sides.sort((a, b) => (a[0].y + a[1].y) - (b[0].y + b[1].y))
    return { top, sides, floor, center: { x: cx, y: cy }, depth: cy }
  }

  const ring = (lat: number, lon: number, radiusM: number, z = 0, n = 28): Pt[] => {
    const q = flat.toSvg(lat, lon)
    return Array.from({ length: n }, (_, i) => {
      const a = (i / n) * Math.PI * 2
      return proj(q.x + radiusM * Math.cos(a), q.y + radiusM * Math.sin(a), z)
    })
  }

  const uv = site.zones.flatMap((zn) => zn.polygon.map((p) => flat.toSvg(p.latitude, p.longitude)))
  const grid = (stepM = 10, padM = 18): [Pt, Pt][] => {
    const u0 = Math.floor((Math.min(...uv.map((q) => q.x)) - padM) / stepM) * stepM
    const u1 = Math.ceil((Math.max(...uv.map((q) => q.x)) + padM) / stepM) * stepM
    const v0 = Math.floor((Math.min(...uv.map((q) => q.y)) - padM) / stepM) * stepM
    const v1 = Math.ceil((Math.max(...uv.map((q) => q.y)) + padM) / stepM) * stepM
    const lines: [Pt, Pt][] = []
    for (let u = u0; u <= u1; u += stepM) lines.push([proj(u, v0), proj(u, v1)])
    for (let v = v0; v <= v1; v += stepM) lines.push([proj(u0, v), proj(u1, v)])
    return lines
  }

  return { at, near, viewBox, solid, ring, grid }
}
