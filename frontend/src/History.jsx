import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { api } from './api.js';

export default function History() {
  const [dates, setDates] = useState(null);
  useEffect(() => { api('/feed/dates').then(r => setDates(r.dates)); }, []);

  if (!dates) return <div className="text-slate-500">Laddar…</div>;
  if (dates.length === 0) {
    return (
      <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-slate-500 text-sm">
        Ingen historik ännu. Första leveransen dyker upp här efter morgondagens körning.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-bold">Historik · senaste 30 dagarna</h1>
        <div className="text-xs text-slate-500">{dates.length} leveranser</div>
      </div>
      <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="text-left px-4 py-2">Datum</th>
              <th className="text-left px-4 py-2">Veckodag</th>
              <th className="text-right px-4 py-2">Klipp</th>
              <th className="text-right px-4 py-2">Ja</th>
              <th className="text-right px-4 py-2">Nej</th>
              <th className="text-right px-4 py-2">Snooze</th>
              <th className="text-right px-4 py-2">Kvar</th>
              <th className="px-4 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {dates.map(d => {
              const date = new Date(d.date + 'T00:00:00');
              const weekday = date.toLocaleDateString('sv-SE', { weekday: 'long' });
              const pending = d.pending > 0;
              return (
                <tr key={d.date} className={`border-t border-slate-200 ${pending ? 'bg-amber-50/40' : ''}`}>
                  <td className="px-4 py-2 font-mono">{d.date}</td>
                  <td className="px-4 py-2 capitalize text-slate-600">{weekday}</td>
                  <td className="px-4 py-2 text-right font-mono">{d.candidate_count}</td>
                  <td className={`px-4 py-2 text-right font-mono ${d.yes > 0 ? 'text-emerald-700 font-semibold' : 'text-slate-400'}`}>{d.yes}</td>
                  <td className={`px-4 py-2 text-right font-mono ${d.no > 0 ? 'text-red-700' : 'text-slate-400'}`}>{d.no}</td>
                  <td className={`px-4 py-2 text-right font-mono ${d.snooze > 0 ? 'text-slate-600' : 'text-slate-400'}`}>{d.snooze}</td>
                  <td className={`px-4 py-2 text-right font-mono ${pending ? 'text-amber-700 font-semibold' : 'text-slate-400'}`}>
                    {d.pending}
                  </td>
                  <td className="px-4 py-2 text-right">
                    <Link to={`/feed/${d.date}`} className="text-xs text-blue-600 hover:underline">
                      Öppna →
                    </Link>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
