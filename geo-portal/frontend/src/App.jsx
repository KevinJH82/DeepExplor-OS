import { useEffect } from 'react'
import { HashRouter, Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import { useAuth } from './store'
import Login from './views/Login'
import Projects from './views/Projects'
import Workspace from './views/Workspace'
import Admin from './views/Admin'

function Guard({ children }) {
  const user = useAuth((s) => s.user)
  const ready = useAuth((s) => s.ready)
  if (!ready) return null
  if (!user) return <Navigate to="/login" replace />
  return children
}

function AdminGuard({ children }) {
  const user = useAuth((s) => s.user)
  const ready = useAuth((s) => s.ready)
  if (!ready) return null
  if (!user) return <Navigate to="/login" replace />
  if (!['org_admin', 'platform_admin'].includes(user.tenant_role)) return <Navigate to="/projects" replace />
  return children
}

export default function App() {
  const boot = useAuth((s) => s.boot)
  useEffect(() => { boot() }, [boot])
  return (
    <HashRouter>
      <div id="bgfx" />
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/projects" element={<Guard><Projects /></Guard>} />
        <Route path="/projects/:id" element={<Guard><Workspace /></Guard>} />
        <Route path="/admin" element={<AdminGuard><Admin /></AdminGuard>} />
        <Route path="*" element={<Navigate to="/projects" replace />} />
      </Routes>
    </HashRouter>
  )
}
