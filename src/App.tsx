import { useEffect, useMemo, useState } from 'react'
import { ArrowRight, BrainCircuit, Check, Command, PackagePlus, Search, X } from 'lucide-react'
import { Header } from './components/Header'
import { Sidebar } from './components/Sidebar'
import { Assistant } from './pages/Assistant'
import { Forecasting } from './pages/Forecasting'
import { Inventory } from './pages/Inventory'
import { ModulePage } from './pages/ModulePage'
import { Overview } from './pages/Overview'
import type { View } from './types'

const viewTitles: Record<View, string> = {
  overview: 'Command center', inventory: 'Inventory', forecasting: 'Forecasting', assistant: 'Ask SmartStock', orders: 'Orders', purchasing: 'Purchasing', warehouses: 'Warehouses',
}

function App() {
  const [view, setView] = useState<View>('assistant')
  const [theme, setTheme] = useState<'dark' | 'light'>(() => (localStorage.getItem('smartstock-theme') as 'dark' | 'light') || 'dark')
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [itemModalOpen, setItemModalOpen] = useState(false)
  const [toast, setToast] = useState('')

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('smartstock-theme', theme)
  }, [theme])

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setSearchOpen(true) }
      if (event.key === 'Escape') { setSearchOpen(false); setItemModalOpen(false); setNotificationsOpen(false) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  function navigate(next: View) {
    setView(next)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const content = useMemo(() => {
    if (view === 'overview') return <Overview onNavigate={navigate} />
    if (view === 'inventory') return <Inventory onNewItem={() => setItemModalOpen(true)} />
    if (view === 'forecasting') return <Forecasting />
    if (view === 'assistant') return <Assistant />
    return <ModulePage view={view} />
  }, [view])

  function saveItem(event: React.FormEvent) {
    event.preventDefault()
    setItemModalOpen(false)
    setToast('Product draft created')
    window.setTimeout(() => setToast(''), 2800)
  }

  return (
    <div className={`app-shell ${sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <Sidebar activeView={view} onNavigate={navigate} open={sidebarOpen} onClose={() => setSidebarOpen(false)} collapsed={sidebarCollapsed} onCollapse={() => setSidebarCollapsed((value) => !value)} />
      {sidebarOpen && <button className="sidebar-scrim" onClick={() => setSidebarOpen(false)} aria-label="Close menu" />}
      <div className="app-main">
        <Header title={viewTitles[view]} theme={theme} onThemeToggle={() => setTheme((value) => value === 'dark' ? 'light' : 'dark')} onMenu={() => setSidebarOpen(true)} onSearch={() => setSearchOpen(true)} onNotifications={() => setNotificationsOpen((value) => !value)} />
        {notificationsOpen && <div className="notification-popover panel"><div><strong>Signal center</strong><button onClick={() => setNotificationsOpen(false)}><X size={15} /></button></div><button><span className="alert-pip" /><span><strong>3 products need attention</strong><small>Forecast updated 4 minutes ago</small></span></button><button><span className="success-pip" /><span><strong>PO-2048 was received</strong><small>120 units at Brooklyn Hub</small></span></button><button className="view-notifications">View all signals <ArrowRight size={14} /></button></div>}
        <div className="app-content">{content}</div>
      </div>

      {searchOpen && <div className="modal-backdrop" onMouseDown={() => setSearchOpen(false)}><div className="command-modal" onMouseDown={(e) => e.stopPropagation()}><label><Search size={19} /><input autoFocus placeholder="Search products, orders, suppliers…" /><kbd><Command size={11} /> K</kbd></label><div className="command-section"><span>Jump to</span><button onClick={() => { navigate('inventory'); setSearchOpen(false) }}><PackagePlus size={17} /><span><strong>Inventory</strong><small>Browse all items and stock levels</small></span><em>G then I</em></button><button onClick={() => { navigate('assistant'); setSearchOpen(false) }}><BrainCircuit size={17} /><span><strong>Ask SmartStock</strong><small>Query your operating data</small></span><em>G then A</em></button></div><footer>Navigate with ↑↓ <span>·</span> Select with ↵ <span>·</span> Close with esc</footer></div></div>}

      {itemModalOpen && <div className="modal-backdrop" onMouseDown={() => setItemModalOpen(false)}><form className="item-modal panel" onMouseDown={(e) => e.stopPropagation()} onSubmit={saveItem}><div className="modal-title"><div><span className="section-kicker">Catalog</span><h2>Create new item</h2></div><button type="button" className="icon-button" onClick={() => setItemModalOpen(false)}><X size={17} /></button></div><div className="form-grid"><label className="span-2"><span>Product name</span><input required placeholder="e.g. Signal Wireless Charger" /></label><label><span>SKU</span><input required placeholder="SIG-0001" /></label><label><span>Category</span><select defaultValue=""><option value="" disabled>Select category</option><option>Accessories</option><option>Lighting</option><option>Power</option><option>Workspace</option></select></label><label><span>Opening stock</span><input type="number" min="0" placeholder="0" /></label><label><span>Reorder point</span><input type="number" min="0" placeholder="20" /></label><label className="span-2"><span>Warehouse</span><select><option>Brooklyn Hub</option><option>Austin Central</option><option>Reno West</option></select></label></div><div className="form-actions"><button type="button" className="button secondary" onClick={() => setItemModalOpen(false)}>Cancel</button><button type="submit" className="button primary">Create draft</button></div></form></div>}

      {toast && <div className="toast"><Check size={15} /> {toast}</div>}
    </div>
  )
}

export default App
