import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useNavigate } from 'react-router-dom'
import { RagWorkspace } from './pages/RagWorkspace'
import { WarehouseWorkspace } from './pages/WarehouseWorkspace'
import { OPS_ROUTES } from './pages/ops/routes'

function App() {
  const navigate = useNavigate()
  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('smartstock-theme') as 'dark' | 'light') || 'dark',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('smartstock-theme', theme)
  }, [theme])

  return <Routes>
    <Route path="/" element={
      <RagWorkspace
        theme={theme}
        onThemeToggle={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
        onOpenWarehouse={() => navigate('/warehouse')}
      />
    } />
    <Route path="/warehouse" element={<WarehouseWorkspace onExit={() => navigate('/')} />} />
    {OPS_ROUTES.map((route) => <Route key={route.path} path={route.path} element={route.element} />)}
    <Route path="*" element={<Navigate to="/" replace />} />
  </Routes>
}

export default App
