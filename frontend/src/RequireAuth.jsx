import { useEffect, useState } from 'react';
import { Navigate } from 'react-router-dom';
import { api } from './api.js';

export default function RequireAuth({ children }) {
  const [state, setState] = useState('checking'); // 'checking' | 'ok' | 'fail'
  useEffect(() => {
    api('/me').then(() => setState('ok')).catch(() => setState('fail'));
  }, []);
  if (state === 'checking') return <div className="p-8 text-slate-500">…</div>;
  if (state === 'fail') return <Navigate to="/login" replace />;
  return children;
}
