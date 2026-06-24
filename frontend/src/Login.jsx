import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { api } from './api.js';

export default function Login() {
  const [pw, setPw] = useState('');
  const [err, setErr] = useState('');
  const nav = useNavigate();
  async function submit(e) {
    e.preventDefault();
    setErr('');
    try {
      await api('/login', { method: 'POST', body: JSON.stringify({ password: pw }) });
      nav('/feed');
    } catch (e) {
      setErr('Fel lösenord');
    }
  }
  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50">
      <form onSubmit={submit} className="w-80 bg-white border border-slate-200 rounded-xl p-6 shadow-sm">
        <h1 className="text-lg font-bold mb-1">WLA Scout</h1>
        <p className="text-xs text-slate-500 mb-4">Internt redaktionellt verktyg</p>
        <input
          type="password"
          autoFocus
          value={pw}
          onChange={e => setPw(e.target.value)}
          placeholder="Lösenord"
          className="w-full px-3 py-2 border border-slate-300 rounded-md text-sm focus:outline-none focus:border-slate-900"
        />
        {err && <p className="text-xs text-red-600 mt-2">{err}</p>}
        <button type="submit" className="w-full mt-3 py-2 bg-slate-900 text-white rounded-md text-sm font-semibold hover:bg-slate-700">
          Logga in
        </button>
      </form>
    </div>
  );
}
