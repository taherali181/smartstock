import { forecast } from '../data/mockData'

export function ForecastChart({ compact = false }: { compact?: boolean }) {
  const width = 760
  const height = compact ? 180 : 260
  const padX = 18
  const padY = 20
  const all = forecast.flatMap((point) => [point.actual, point.predicted])
  const min = Math.min(...all) - 8
  const max = Math.max(...all) + 6
  const toPoints = (key: 'actual' | 'predicted') => forecast.map((point, index) => {
    const x = padX + (index / (forecast.length - 1)) * (width - padX * 2)
    const y = padY + ((max - point[key]) / (max - min)) * (height - padY * 2)
    return `${x},${y}`
  }).join(' ')

  const areaPoints = `${padX},${height - padY} ${toPoints('predicted')} ${width - padX},${height - padY}`

  return (
    <div className="forecast-chart">
      <svg viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label="Actual and forecast demand trend">
        <defs>
          <linearGradient id="forecast-fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--accent)" stopOpacity=".16" />
            <stop offset="100%" stopColor="var(--accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.2, 0.4, 0.6, 0.8].map((n) => <line key={n} x1="0" x2={width} y1={height * n} y2={height * n} className="chart-grid" />)}
        <polygon points={areaPoints} fill="url(#forecast-fill)" />
        <polyline points={toPoints('actual')} className="actual-line" fill="none" />
        <polyline points={toPoints('predicted')} className="predicted-line" fill="none" />
        {forecast.map((point, index) => {
          const x = padX + (index / (forecast.length - 1)) * (width - padX * 2)
          const y = padY + ((max - point.predicted) / (max - min)) * (height - padY * 2)
          return <circle key={point.day} cx={x} cy={y} r="3" className="forecast-dot" />
        })}
      </svg>
      <div className="chart-labels">{forecast.map((point) => <span key={point.day}>AUG {point.day}</span>)}</div>
    </div>
  )
}
