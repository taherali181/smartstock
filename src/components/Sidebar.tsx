import {
  Boxes,
  BrainCircuit,
  ChartNoAxesCombined,
  ChevronDown,
  ClipboardList,
  LayoutDashboard,
  PackageSearch,
  PanelLeftClose,
  PlugZap,
  ShoppingCart,
  Store,
  Truck,
  Warehouse,
  X,
  type LucideIcon,
} from 'lucide-react'
import type { View } from '../types'

interface SidebarProps {
  activeView: View
  onNavigate: (view: View) => void
  open: boolean
  onClose: () => void
  collapsed: boolean
  onCollapse: () => void
}

const mainNav: { label: string; icon: LucideIcon; view: View; badge?: string }[] = [
  { label: 'Ask SmartStock', icon: BrainCircuit, view: 'assistant' },
  { label: 'Overview', icon: LayoutDashboard, view: 'overview' },
  { label: 'Inventory', icon: Boxes, view: 'inventory', badge: '12' },
  { label: 'Orders', icon: ShoppingCart, view: 'orders', badge: '8' },
  { label: 'Purchasing', icon: ClipboardList, view: 'purchasing' },
  { label: 'Warehouses', icon: Warehouse, view: 'warehouses' },
  { label: 'Forecasting', icon: ChartNoAxesCombined, view: 'forecasting' },
]

const secondaryNav: { label: string; icon: LucideIcon }[] = [
  { label: 'Suppliers', icon: Truck },
  { label: 'Channels', icon: Store },
  { label: 'Reports', icon: PackageSearch },
  { label: 'Integrations', icon: PlugZap },
]

export function Sidebar({ activeView, onNavigate, open, onClose, collapsed, onCollapse }: SidebarProps) {
  return (
    <aside className={`sidebar ${open ? 'is-open' : ''} ${collapsed ? 'is-collapsed' : ''}`}>
      <div className="brand-row">
        <button className="brand" onClick={() => onNavigate('overview')} aria-label="SmartStock overview">
          <span className="brand-mark">S</span>
          <span className="brand-name">SmartStock</span>
        </button>
        <button className="icon-button sidebar-close" onClick={onClose} aria-label="Close menu"><X size={18} /></button>
      </div>

      <button className="workspace-switcher">
        <span className="workspace-avatar">NV</span>
        <span className="workspace-copy"><strong>Nova Supply Co.</strong><small>Operations workspace</small></span>
        <ChevronDown size={14} />
      </button>

      <nav className="nav-sections" aria-label="Primary navigation">
        <div className="nav-section">
          <span className="nav-label">Main</span>
          {mainNav.map(({ label, icon: Icon, view, badge }) => (
            <button
              key={view}
              className={`nav-item ${activeView === view ? 'active' : ''}`}
              onClick={() => { onNavigate(view); onClose() }}
              title={collapsed ? label : undefined}
            >
              <Icon size={18} strokeWidth={1.7} />
              <span>{label}</span>
              {badge && <small>{badge}</small>}
            </button>
          ))}
        </div>

        <div className="nav-section">
          <span className="nav-label">Network</span>
          {secondaryNav.map(({ label, icon: Icon }) => (
            <button key={label} className="nav-item" title={collapsed ? label : undefined}>
              <Icon size={18} strokeWidth={1.7} /><span>{label}</span>
            </button>
          ))}
        </div>
      </nav>

      <div className="sidebar-bottom">
        <button className="account-row">
          <span className="account-avatar">TA</span>
          <span><strong>Taher Ali</strong><small>Administrator</small></span>
          <ChevronDown size={14} />
        </button>
        <button className="collapse-button" onClick={onCollapse}>
          <PanelLeftClose size={16} /> <span>{collapsed ? 'Expand menu' : 'Collapse menu'}</span>
        </button>
      </div>
    </aside>
  )
}
