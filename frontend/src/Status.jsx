import { useEffect, useState } from 'react';
import { api } from './api.js';

export default function Status() {
  const [data, setData] = useState(null);
  useEffect(() => { api('/status').then(setData); }, []);
  if (!data) return <div className="text-slate-500">Laddar…</div>;
  return (
    <div>
      <h1 className="text-xl font-bold mb-4">Status</h1>
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="text-left px-4 py-2">Kind</th>
              <th className="text-left px-4 py-2">Status</th>
              <th className="text-left px-4 py-2">Start</th>
              <th className="text-right px-4 py-2">Kvot</th>
              <th className="text-right px-4 py-2">LLM $</th>
            </tr>
          </thead>
          <tbody>
            {data.runs.map(r => (
              <tr key={r.id} className="border-t border-slate-200">
                <td className="px-4 py-2">{r.kind}</td>
                <td className="px-4 py-2">{r.status}</td>
                <td className="px-4 py-2 font-mono text-xs">{r.started_at}</td>
                <td className="px-4 py-2 text-right font-mono">{r.youtube_quota_used}</td>
                <td className="px-4 py-2 text-right font-mono">${r.llm_cost_usd.toFixed(3)}</td>
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
