import { useEffect, useState } from 'react';
import { api } from './api.js';

export default function Feed() {
  const [data, setData] = useState(null);
  useEffect(() => { api('/feed/today').then(setData); }, []);
  if (!data) return <div className="text-slate-500">Laddar…</div>;
  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-xl font-bold">Dagens feed · {data.date}</h1>
        <div className="text-xs text-slate-500">{data.candidates.length} kandidater</div>
      </div>
      {data.candidates.length === 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-slate-500 text-sm">
          Inga kandidater levererade ännu. Kör scouting-pipelinen.
        </div>
      )}
    </div>
  );
}
