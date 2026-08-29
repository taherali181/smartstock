export function Sparkline({ values, accent = false }: { values: number[]; accent?: boolean }) {
  const width = 82
  const height = 28
  const max = Math.max(...values)
  const min = Math.min(...values)
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width
    const y = height - ((value - min) / Math.max(max - min, 1)) * (height - 4) - 2
    return `${x},${y}`
  }).join(' ')

  return (
    <svg className="sparkline" viewBox={`0 0 ${width} ${height}`} role="img" aria-label="Seven day stock trend">
      <polyline points={points} fill="none" stroke={accent ? 'var(--accent)' : 'var(--line-strong)'} strokeWidth="1.8" vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
