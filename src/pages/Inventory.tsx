import { ArrowDownToLine, ChevronDown, Filter, MoreHorizontal, Plus, Search, SlidersHorizontal } from 'lucide-react'
import { useMemo, useState } from 'react'
import { products } from '../data/mockData'
import { Sparkline } from '../components/Sparkline'

export function Inventory({ onNewItem }: { onNewItem: () => void }) {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<'All' | 'Attention'>('All')
  const visible = useMemo(() => products.filter((product) => {
    const matchesQuery = `${product.name} ${product.sku} ${product.category}`.toLowerCase().includes(query.toLowerCase())
    const matchesFilter = filter === 'All' || product.status === 'Low stock' || product.status === 'Out of stock'
    return matchesQuery && matchesFilter
  }), [query, filter])

  return (
    <div className="page">
      <section className="page-intro compact">
        <div><span className="eyebrow">Inventory control</span><h1>Inventory</h1><p>A live view of stock across your entire network.</p></div>
        <div className="intro-actions"><button className="button secondary"><ArrowDownToLine size={16} /> Export</button><button className="button primary" onClick={onNewItem}><Plus size={16} /> New item</button></div>
      </section>

      <section className="inventory-stats">
        <div><span>Total SKUs</span><strong>1,248</strong><small>+24 this month</small></div>
        <div><span>Low stock</span><strong className="warning-text">12</strong><small>6 need action</small></div>
        <div><span>Out of stock</span><strong>3</strong><small>$4.2K at risk</small></div>
        <div><span>Sell-through</span><strong>68.4%</strong><small>Last 30 days</small></div>
      </section>

      <section className="panel inventory-table-panel">
        <div className="inventory-toolbar">
          <div className="segmented">
            <button className={filter === 'All' ? 'active' : ''} onClick={() => setFilter('All')}>All inventory <span>1,248</span></button>
            <button className={filter === 'Attention' ? 'active' : ''} onClick={() => setFilter('Attention')}>Needs attention <span>15</span></button>
          </div>
          <div className="toolbar-actions">
            <label className="table-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search items or SKU" /></label>
            <button className="filter-button"><Filter size={15} /> Filter <ChevronDown size={13} /></button>
            <button className="icon-button"><SlidersHorizontal size={16} /></button>
          </div>
        </div>
        <div className="table-wrap inventory-full-table">
          <table>
            <thead><tr><th><input type="checkbox" aria-label="Select all" /></th><th>Product</th><th>Category</th><th>Location</th><th>Available</th><th>Committed</th><th>Incoming</th><th>Status</th><th>Trend</th><th /></tr></thead>
            <tbody>{visible.map((product) => (
              <tr key={product.sku}>
                <td><input type="checkbox" aria-label={`Select ${product.name}`} /></td>
                <td><div className="product-cell"><span className="product-glyph">{product.name.slice(0, 2).toUpperCase()}</span><span><strong>{product.name}</strong><small>{product.sku}</small></span></div></td>
                <td>{product.category}</td><td>{product.warehouse}</td><td><strong>{product.available}</strong></td><td>{product.committed}</td><td>{product.incoming || '—'}</td>
                <td><span className={`status status-${product.status.toLowerCase().replaceAll(' ', '-')}`}>{product.status}</span></td>
                <td><Sparkline values={product.trend} accent={product.status === 'Healthy'} /></td>
                <td><button className="table-more"><MoreHorizontal size={17} /></button></td>
              </tr>
            ))}</tbody>
          </table>
          {visible.length === 0 && <div className="empty-state">No products match that search.</div>}
        </div>
        <div className="pagination"><span>Showing {visible.length} of 1,248 items</span><div><button disabled>Previous</button><button>Next</button></div></div>
      </section>
    </div>
  )
}
