import { Bell, Command, Menu, Moon, Search, Sun } from 'lucide-react'

interface HeaderProps {
  title: string
  theme: 'dark' | 'light'
  onThemeToggle: () => void
  onMenu: () => void
  onSearch: () => void
  onNotifications: () => void
}

export function Header({ title, theme, onThemeToggle, onMenu, onSearch, onNotifications }: HeaderProps) {
  return (
    <header className="topbar">
      <div className="topbar-title">
        <button className="icon-button mobile-menu" onClick={onMenu} aria-label="Open menu"><Menu size={19} /></button>
        <span className="status-pulse" />
        <span>{title}</span>
      </div>
      <div className="topbar-actions">
        <button className="search-trigger" onClick={onSearch}>
          <Search size={16} />
          <span>Search anything</span>
          <kbd><Command size={11} /> K</kbd>
        </button>
        <button className="icon-button" onClick={onThemeToggle} aria-label={`Use ${theme === 'dark' ? 'light' : 'dark'} mode`}>
          {theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
        </button>
        <button className="icon-button notification-button" onClick={onNotifications} aria-label="Notifications">
          <Bell size={18} /><span />
        </button>
        <button className="user-avatar" aria-label="Open profile">TA</button>
      </div>
    </header>
  )
}
