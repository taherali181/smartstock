import { ArrowRight, Banknote, Boxes, CircleAlert, Clock3, PackageCheck, Sparkles, Zap } from 'lucide-react'
import { activities, products } from '../data/mockData'
import { ForecastChart } from '../components/ForecastChart'
import { MetricCard } from '../components/MetricCard'
import type { View } from '../types'

export function Overview({ onNavigate }: { onNavigate: (view: View) => void }) {
  return (
    <div className="page overview-page">
      <section className="page-intro">
        <div>
          <span className="eyebrow"><span /> Live operations</span>
          <h1>Good morning, Taher.</h1>
          <p>Your network is stable. <strong>3 items</strong> need attention today.</p>
        </div>
        <div className="intro-actions">
          <button className="button secondary" onClick={() => onNavigate('inventory')}>View inventory</button>
          <button className="button primary" onClick={() => onNavigate('assistant')}><Sparkles size={16} /> Ask SmartStock</button>
        </div>
      </section>

      <section className="metric-grid">
        <MetricCard label="Inventory value" value="$428.6K" delta="8.2%" icon={Banknote} annotation="vs last month" />
        <MetricCard label="Units available" value="12,842" delta="3.4%" icon={Boxes} annotation="across 3 sites" />
        <MetricCard label="Open orders" value="284" delta="12.1%" icon={PackageCheck} annotation="38 due today" />
        <MetricCard label="Stockout risk" value="3 items" delta="2 resolved" trend="down" icon={CircleAlert} annotation="this week" />
      </section>

      <section className="dashboard-grid">
        <article className="panel forecast-panel">
          <div className="panel-heading">
            <div><span className="section-kicker">Demand signal</span><h2>30-day demand forecast</h2></div>
            <div className="chart-legend"><span className="actual">Actual</span><span className="predicted">Predicted</span></div>
          </div>
          <ForecastChart compact />
          <div className="forecast-summary">
            <div><span>Projected units</span><strong>2,480</strong></div>
            <div><span>Forecast confidence</span><strong>92.4%</strong></div>
            <button onClick={() => onNavigate('forecasting')}>Open forecast <ArrowRight size={14} /></button>
          </div>
        </article>

        <article className="panel attention-panel">
          <div className="panel-heading">
            <div><span className="section-kicker">Signal feed</span><h2>Needs attention</h2></div>
            <button className="text-button">View all</button>
          </div>
          <div className="activity-list">
            {activities.map((activity) => (
              <button className="activity-item" key={activity.id}>
                <span className={`activity-indicator ${activity.tone}`}><Zap size={14} /></span>
                <span><strong>{activity.title}</strong><small>{activity.detail}</small></span>
                <time><Clock3 size={12} /> {activity.time}</time>
              </button>
            ))}
          </div>
        </article>
      </section>

      <section className="panel stock-panel">
        <div className="panel-heading">
          <div><span className="section-kicker">Stock position</span><h2>Priority inventory</h2></div>
          <button className="text-button" onClick={() => onNavigate('inventory')}>View all inventory <ArrowRight size={14} /></button>
        </div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Product</th><th>Warehouse</th><th>Available</th><th>Incoming</th><th>Status</th><th>7-day trend</th></tr></thead>
            <tbody>{products.slice(0, 4).map((product) => (
              <tr key={product.sku}>
                <td><div className="product-cell"><span className="product-glyph">{product.name.slice(0, 2).toUpperCase()}</span><span><strong>{product.name}</strong><small>{product.sku}</small></span></div></td>
                <td>{product.warehouse}</td>
                <td><strong>{product.available}</strong> <small>units</small></td>
                <td>{product.incoming || '—'}</td>
                <td><span className={`status status-${product.status.toLowerCase().replaceAll(' ', '-')}`}>{product.status}</span></td>
                <td><div className="micro-bars">{product.trend.map((v, i) => <i key={i} style={{ height: `${Math.max(v * 1.2, 5)}px` }} />)}</div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>
    </div>
  )
}
