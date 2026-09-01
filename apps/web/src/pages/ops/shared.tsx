import type { ReactNode } from 'react'
import { NavLink } from 'react-router-dom'
import { Boxes, ClipboardList, Home, PackageSearch, ShoppingCart, Warehouse } from 'lucide-react'
import { errorMessage } from './utils'
import './ops.css'

const links = [
  { to: '/', label: 'Canvas', icon: Home },
  { to: '/inventory', label: 'Inventory', icon: Boxes },
  { to: '/products', label: 'Products', icon: PackageSearch },
  { to: '/orders', label: 'Orders', icon: ShoppingCart },
  { to: '/tasks', label: 'Tasks', icon: ClipboardList },
  { to: '/warehouse', label: 'Scanner', icon: Warehouse },
]

export function OpsShell({
  eyebrow,
  title,
  actions,
  children,
}: {
  eyebrow: string
  title: string
  actions?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="ops-app">
      <aside className="ops-sidebar">
        <NavLink className="ops-logo" to="/" aria-label="SmartStock canvas">
          <span>SS</span>
          <strong>SMARTSTOCK</strong>
        </NavLink>
        <nav aria-label="Operational navigation">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink key={to} to={to} aria-label={label} className={({ isActive }) => isActive ? 'active' : ''}>
              <Icon size={17} aria-hidden />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="ops-sidebar-foot">
          <span>DEVELOPMENT</span>
          <small>Live PostgreSQL</small>
        </div>
      </aside>
      <main className="ops-main">
        <header className="ops-header">
          <div>
            <span>{eyebrow}</span>
            <h1>{title}</h1>
          </div>
          {actions && <div className="ops-header-actions">{actions}</div>}
        </header>
        <div className="ops-content">{children}</div>
      </main>
    </div>
  )
}

export function LoadingState({ label = 'Loading operational records…' }: { label?: string }) {
  return <div className="ops-state" role="status"><span className="ops-spinner" />{label}</div>
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <div className="ops-state">{children}</div>
}

export function ErrorState({ error }: { error: unknown }) {
  return <div className="ops-alert danger" role="alert">{errorMessage(error)}</div>
}

export function StatePill({ value }: { value: string }) {
  const tone = ['received', 'shipped', 'completed', 'active', 'allocated'].includes(value)
    ? 'success'
    : ['exception', 'cancelled', 'damaged', 'expired'].includes(value)
      ? 'danger'
      : ['pending_approval', 'partially_received', 'partially_allocated', 'quarantined'].includes(value)
        ? 'warning'
        : ''
  return <span className={`ops-pill ${tone}`}>{value.replaceAll('_', ' ')}</span>
}
