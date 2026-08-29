import { ArrowDownRight, ArrowUpRight, type LucideIcon } from 'lucide-react'

interface MetricCardProps {
  label: string
  value: string
  delta: string
  trend?: 'up' | 'down'
  icon: LucideIcon
  annotation: string
}

export function MetricCard({ label, value, delta, trend = 'up', icon: Icon, annotation }: MetricCardProps) {
  const TrendIcon = trend === 'up' ? ArrowUpRight : ArrowDownRight
  return (
    <article className="metric-card edge-card">
      <div className="metric-top"><span>{label}</span><Icon size={17} /></div>
      <div className="metric-value">{value}</div>
      <div className="metric-foot">
        <span className={`delta ${trend}`}><TrendIcon size={13} />{delta}</span>
        <span>{annotation}</span>
      </div>
    </article>
  )
}
