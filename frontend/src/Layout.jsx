import { NavLink, Outlet } from 'react-router-dom';

export default function Layout() {
  const link = ({ isActive }) =>
    `px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
      isActive ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-200'
    }`;
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="max-w-6xl mx-auto px-6 py-3 flex items-center gap-6">
          <div className="font-bold text-base">WLA Scout</div>
          <nav className="flex gap-1">
            <NavLink to="/feed" end className={link}>Dagens feed</NavLink>
            <NavLink to="/history" className={link}>Historik</NavLink>
            <NavLink to="/status" className={link}>Status</NavLink>
            <NavLink to="/learning" className={link}>Inlärning</NavLink>
          </nav>
        </div>
      </header>
      <main className="flex-1 max-w-6xl w-full mx-auto px-6 py-6">
        <Outlet />
      </main>
    </div>
  );
}
