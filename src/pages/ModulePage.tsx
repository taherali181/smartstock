import { ArrowRight, CheckCircle2, CircleDashed, Clock3, Plus } from 'lucide-react'
import type { View } from '../types'

const content: Record<'orders' | 'purchasing' | 'warehouses', { title: string; subtitle: string; metric: [string, string][]; rows: [string, string, string][] }> = {
  orders: {
    title: 'Orders', subtitle: 'Track every order from channel to doorstep.',
    metric: [['Open orders', '284'], ['Due today', '38'], ['On-time rate', '96.8%'], ['Returns', '7']],
    rows: [['SO-8402', 'Northstar Studio', 'Ready to ship'], ['SO-8401', 'Axiom Works', 'Picking'], ['SO-8398', 'Atlas & Co.', 'Processing']],
  },
  purchasing: {
    title: 'Purchasing', subtitle: 'Keep replenishment, suppliers, and receiving in sync.',
    metric: [['Open POs', '32'], ['Inbound units', '1,840'], ['Due this week', '9'], ['Late receipts', '2']],
    rows: [['PO-2049', 'Eleven Components', 'Awaiting approval'], ['PO-2048', 'Nova Manufacturing', 'Part received'], ['PO-2042', 'Arc Supply', 'In transit']],
  },
  warehouses: {
    title: 'Warehouses', subtitle: 'One control plane for every location and stock movement.',
    metric: [['Locations', '3'], ['Total capacity', '72%'], ['Open transfers', '6'], ['Pick accuracy', '99.2%']],
    rows: [['WH-BRK-01', 'Brooklyn Hub', 'Operational'], ['WH-AUS-01', 'Austin Central', 'Operational'], ['WH-RNO-01', 'Reno West', 'Cycle count']],
  },
}

export function ModulePage({ view }: { view: Extract<View, 'orders' | 'purchasing' | 'warehouses'> }) {
  const page = content[view]
  return (
    <div className="page">
      <section className="page-intro compact"><div><span className="eyebrow">Operations</span><h1>{page.title}</h1><p>{page.subtitle}</p></div><button className="button primary"><Plus size={16} /> New {view === 'orders' ? 'order' : view === 'purchasing' ? 'purchase order' : 'transfer'}</button></section>
      <section className="inventory-stats">{page.metric.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong><small>Live workspace</small></div>)}</section>
      <section className="panel module-panel"><div className="panel-heading"><div><span className="section-kicker">Live queue</span><h2>Recent activity</h2></div><button className="text-button">View all <ArrowRight size={14} /></button></div>
        <div className="module-rows">{page.rows.map(([id, name, status], i) => <button key={id}><span className="module-icon">{i === 0 ? <Clock3 size={17} /> : i === 1 ? <CircleDashed size={17} /> : <CheckCircle2 size={17} />}</span><span><strong>{id}</strong><small>{name}</small></span><em>{status}</em><ArrowRight size={15} /></button>)}</div>
      </section>
      <section className="coming-banner"><div><span className="section-kicker">Frontend preview</span><h2>Workflow detail is coming with the API layer.</h2><p>This module is represented in the information architecture now so the Node/Express services can slot in without redesigning navigation later.</p></div><span className="construction-grid" /></section>
    </div>
  )
}
