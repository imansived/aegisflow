import { useId } from 'react'

/** The AegisFlow mark: a planet cut by a diagonal gap, with a thin ring sliver running through it. Drawn in currentColor. */
export function Logo({ size = 28, className }: { size?: number; className?: string }) {
  const id = useId()
  return (
    <svg className={className} viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false">
      <defs>
        <mask id={`${id}-cut`}>
          <rect width="64" height="64" fill="#fff" />
          <g transform="rotate(-32 32 32)"><rect x="-8" y="28.6" width="80" height="6.8" fill="#000" /></g>
        </mask>
      </defs>
      <circle cx="31" cy="32" r="21.5" fill="currentColor" mask={`url(#${id}-cut)`} />
      <g transform="rotate(-32 32 32)"><path d="M-4 32 L32 30.2 L68 32 L32 33.8 Z" fill="currentColor" /></g>
    </svg>
  )
}
