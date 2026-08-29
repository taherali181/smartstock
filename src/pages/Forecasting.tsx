import { ArrowUpRight, BrainCircuit, CalendarDays, ChevronDown, CircleGauge, Sparkles, TrendingUp } from 'lucide-react'
import { ForecastChart } from '../components/ForecastChart'
import { products } from '../data/mockData'

export function Forecasting() {
  return (
    <div className="page">
      <section className="page-intro compact">
        <div><span className="eyebrow">Predictive intelligence</span><h1>Demand forecasting</h1><p>Plan inventory with explainable, confidence-scored predictions.</p></div>
        <div className="intro-actions"><button className="button secondary"><CalendarDays size={16} /> Next 30 days <ChevronDown size={13} /></button><button className="button primary"><Sparkles size={16} /> Generate plan</button></div>
      </section>

      <section className="forecast-layout">
        <article className="panel main-forecast-card">
          <div className="panel-heading"><div><span className="section-kicker">All products · all locations</span><h2>Network demand</h2></div><span className="confidence-chip"><CircleGauge size={14} /> 92.4% confidence</span></div>
          <ForecastChart />
          <div className="forecast-model-note"><BrainCircuit size={16} /><span><strong>Model insight</strong> Demand is expected to rise 18% in the final week, led by Accessories. Promotion history and seasonality account for 74% of the movement.</span></div>
        </article>
        <aside className="forecast-side">
          <article className="panel signal-card"><span className="section-kicker">Expected demand</span><strong>2,480 <small>units</small></strong><span className="positive"><ArrowUpRight size={14} /> 14.2% vs prior period</span></article>
          <article className="panel signal-card"><span className="section-kicker">Projected revenue</span><strong>$184.2K</strong><span className="positive"><ArrowUpRight size={14} /> $22.8K opportunity</span></article>
          <article className="panel model-card"><div className="model-head"><span>MODEL</span><i>ACTIVE</i></div><h3>SmartForecast v0.3</h3><p>Gradient boosting ensemble with seasonal baselines.</p><div><span>MAPE</span><strong>7.6%</strong></div><div><span>Last trained</span><strong>2h ago</strong></div></article>
        </aside>
      </section>

      <section className="panel recommendation-panel">
        <div className="panel-heading"><div><span className="section-kicker">Recommended actions</span><h2>Replenishment plan</h2></div><button className="text-button">Review all</button></div>
        <div className="recommendation-list">{products.slice(1, 4).map((product, index) => (
          <div className="recommendation-row" key={product.sku}><span className="rank">0{index + 1}</span><div><strong>{product.name}</strong><small>{product.sku} · {product.warehouse}</small></div><span className="recommend-copy">Order <strong>{index === 2 ? 80 : index === 1 ? 220 : 96} units</strong> by Sep {index + 2}</span><span className="confidence">{94 - index * 3}%</span><button><TrendingUp size={15} /> Review</button></div>
        ))}</div>
      </section>
    </div>
  )
}
