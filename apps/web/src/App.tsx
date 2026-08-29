import { useEffect, useState } from 'react'
import { RagWorkspace } from './pages/RagWorkspace'

function App() {
  const [theme, setTheme] = useState<'dark' | 'light'>(() =>
    (localStorage.getItem('smartstock-theme') as 'dark' | 'light') || 'dark',
  )

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('smartstock-theme', theme)
  }, [theme])

  return (
    <RagWorkspace
      theme={theme}
      onThemeToggle={() => setTheme((current) => current === 'dark' ? 'light' : 'dark')}
    />
  )
}

export default App
