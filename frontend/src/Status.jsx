import { useEffect, useState } from 'react';
import { api } from './api.js';

export default function Status() {
  const [data, setData] = useState(null);
  const [killSwitch, setKillSwitch] = useState(null);
  const [busy, setBusy] = useState(false);
  async function refresh() {
    const [s, k] = await Promise.all([api('/status'), api('/admin/kill-switch')]);
    setData(s); setKillSwitch(k);
  }
  useEffect(() => { refresh(); }, []);

  async function toggle(field) {
    await api('/admin/kill-switch', { method: 'POST', body: JSON.stringify({ [field]: !killSwitch[field] }) });
    refresh();
  }
  async function triggerScout() {
    setBusy(true);
    try { await api('/admin/trigger-scout', { method: 'POST' }); } finally { setBusy(false); refresh(); }
  }

  if (!data || !killSwitch) return <div className="text-slate-500">Laddar…</div>;
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold">Status</h1>
        <div className="flex gap-2">
          <a href="/api/export/decisions.csv?days=7"
            className="px-3 py-1.5 text-sm rounded-md border border-slate-300 hover:bg-slate-100">
            Export 7d CSV
          </a>
          <a href="/api/export/decisions.csv?days=30"
            className="px-3 py-1.5 text-sm rounded-md border border-slate-300 hover:bg-slate-100">
            Export 30d CSV
          </a>
          <button onClick={triggerScout} disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md bg-slate-900 text-white hover:bg-slate-700 disabled:bg-slate-400">
            {busy ? 'Kör…' : 'Trigga scouting nu'}
          </button>
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl p-4">
        <h2 className="text-sm font-semibold mb-2">Kill-switch</h2>
        <div className="flex gap-3">
          <Toggle label="LLM-anrop" on={killSwitch.llm_enabled} onClick={() => toggle('llm_enabled')} />
          <Toggle label="Schemaläggning" on={killSwitch.scheduler_enabled} onClick={() => toggle('scheduler_enabled')} />
        </div>
      </div>

      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-slate-200 text-sm font-semibold">Senaste 20 körningar</div>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="text-left px-4 py-2">Kind</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-left px-4 py-2">Start</th>
              <th className="text-right px-4 py-2">YT-kvot</th>
              <th className="text-right px-4 py-2">LLM $</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map(r => (
              <tr key={r.id} className="border-t border-slate-200">
                <td className="px-4 py-2">{r.kind}</td>
                <td className={`px-4 py-2 ${r.status === 'failed' ? 'text-red-600' : r.status === 'ok' ? 'text-emerald-700' : 'text-slate-500'}`}>{r.status}</td>
                <td className="px-4 py-2 font-mono text-xs">{new Date(r.started_at).toLocaleString('sv-SE')}</td>
                <td className="px-4 py-2 text-right font-mono">{r.youtube_quota_used}</td>
                <td className="px-4 py-2 text-right font-mono">${(r.llm_cost_usd ?? 0).toFixed(3)}</td>
              </tr>
            ))}
            {data.runs.length === 0 && (
              <tr><td colSpan={5} className="px-4 py-6 text-center text-slate-500">Inga körningar än.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Toggle({ label, on, onClick }) {
  return (
    <button onClick={onClick}
      className={`px-3 py-1.5 text-xs rounded-md border ${on ? 'bg-emerald-50 border-emerald-300 text-emerald-800' : 'bg-red-50 border-red-300 text-red-800'}`}>
      {label}: {on ? 'PÅ' : 'AV'}
    </button>
  );
}
