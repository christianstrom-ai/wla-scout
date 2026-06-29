import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { api } from './api.js';
// useState används både i Feed och i Expanded

export default function Feed() {
  const [data, setData] = useState(null);
  const [openId, setOpenId] = useState(null);
  const { id: clipId, date: routeDate } = useParams();

  async function refresh() {
    const path = routeDate ? `/feed/by-date/${routeDate}` : '/feed/today';
    setData(await api(path));
  }
  useEffect(() => { refresh(); }, [routeDate]);
  useEffect(() => { if (clipId) setOpenId(clipId); }, [clipId]);

  if (!data) return <div className="text-slate-500">Laddar…</div>;
  const isHistorical = !!routeDate;
  return (
    <div>
      <div className="flex items-baseline justify-between mb-4">
        <h1 className="text-xl font-bold">
          {isHistorical ? 'Feed' : 'Dagens feed'} · {data.date}
        </h1>
        <div className="text-xs text-slate-500 flex items-center gap-3">
          <Link to="/history" className="text-blue-600 hover:underline">← Historik (30d)</Link>
          <span>
            {data.candidates.length} kandidater · golv {data.quality_floor}
            {typeof data.pool_size === 'number' && <> · pool {data.pool_size.toLocaleString('sv-SE')}</>}
          </span>
        </div>
      </div>
      {data.candidates.length === 0 && <Empty />}
      <div className="space-y-3">
        {data.candidates.map((c, i) => (
          <Card key={c.id} c={c} rank={i + 1}
                open={openId === c.id}
                onToggle={() => setOpenId(openId === c.id ? null : c.id)}
                onDecided={refresh} />
        ))}
      </div>
    </div>
  );
}

function Empty() {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-8 text-center text-slate-500 text-sm">
      Inga kandidater levererade ännu. Kör scouting-pipelinen.
    </div>
  );
}

const DECISION_STYLES = {
  yes:    { card: 'bg-emerald-50 border-emerald-300',  hover: 'hover:bg-emerald-100',  badge: 'bg-emerald-600 text-white' },
  no:     { card: 'bg-red-50 border-red-300',          hover: 'hover:bg-red-100',      badge: 'bg-red-600 text-white' },
  snooze: { card: 'bg-amber-50 border-amber-300',      hover: 'hover:bg-amber-100',    badge: 'bg-amber-600 text-white' },
};

function Card({ c, rank, open, onToggle, onDecided }) {
  const verdict = c.decision?.verdict;
  const style = DECISION_STYLES[verdict];
  const cardClass = style ? style.card : 'bg-white border-slate-200';
  const hoverClass = style ? style.hover : 'hover:bg-slate-50';
  return (
    <div className={`border-2 rounded-lg overflow-hidden transition-colors ${cardClass}`}>
      <button onClick={onToggle} className={`w-full text-left p-2 flex items-center gap-2 ${hoverClass}`}>
        <div className="w-24 aspect-video bg-slate-100 rounded overflow-hidden flex-shrink-0">
          {c.thumbnail_url && <img src={c.thumbnail_url} alt="" className="w-full h-full object-cover" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5 text-[11px] text-slate-500 leading-tight">
            <span className="font-mono">#{rank}</span>
            {style && (
              <span className={`text-[10px] font-bold uppercase px-1 py-0 rounded ${style.badge}`}>
                {verdict === 'yes' ? 'Ja' : verdict === 'no' ? 'Nej' : 'Snooze'}
              </span>
            )}
            <span className="truncate max-w-[180px]">{c.channel_title}</span>
            <span>·</span>
            <span>{(c.channel_subscribers ?? 0).toLocaleString('sv-SE')}</span>
            <span className={`text-[10px] px-1 py-0 rounded ${c.is_short ? 'bg-amber-100 text-amber-800' : 'bg-slate-100 text-slate-700'}`}>
              {c.is_short ? 'S' : 'L'}
            </span>
            {c.source_confidence === 'low' && (
              <span className="text-[10px] px-1 py-0 rounded bg-yellow-100 text-yellow-800">dublett?</span>
            )}
            {c.scores?.ai_generated && (
              <span className="text-[10px] px-1 py-0 rounded bg-purple-100 text-purple-800">AI?</span>
            )}
            {c.total_score < (c.quality_floor ?? 55) && (
              <span className="text-[10px] px-1 py-0 rounded bg-slate-100 text-slate-600">under</span>
            )}
          </div>
          <div className="font-semibold text-sm truncate leading-tight mt-0.5">{c.title}</div>
          <div className="text-[11px] text-slate-500 leading-tight">
            {(c.views ?? 0).toLocaleString('sv-SE')} v · {(c.likes ?? 0).toLocaleString('sv-SE')} l · {(c.comments ?? 0).toLocaleString('sv-SE')} k
          </div>
        </div>
        <div className="text-right pr-2 leading-none">
          <div className="text-xl font-bold font-mono">{c.total_score?.toFixed(0) ?? '–'}</div>
          <div className="text-[9px] uppercase text-slate-400 mt-0.5">score</div>
        </div>
      </button>
      {open && <Expanded c={c} onDecided={onDecided} />}
    </div>
  );
}

const SCORE_LABELS = {
  velocity: 'Velocity',
  engagement: 'Engagemang',
  overperformance: 'Överprestation',
  profile_match: 'Profilmatchning',
  story: 'Story-/intervjupotential',
  reachability: 'Nåbarhet',
  originality: 'Originalkälla',
};

const REJECT_CATEGORIES = [
  'AI-genererat',
  'Fel ämne',
  'Ingen story',
  'Dålig kvalitet',
  'Ej originalkälla',
  'Kontakt saknas',
  'Redan publicerat',
];

const APPROVE_CATEGORIES = [
  'Stark vinkel',
  'Bra timing',
  'Originalkälla',
  'Visuellt starkt',
  'Återkommande kreatör',
  'Kontakt enkel',
];

// Färgschema per verdict-formulär — så ja-formuläret blir grönt, nej-rött.
const FORM_THEME = {
  yes: {
    wrap: 'bg-emerald-50/70 border-emerald-200',
    label: 'text-emerald-900',
    chip:        'bg-white text-emerald-700 border-emerald-300 hover:bg-emerald-100',
    chipActive:  'bg-emerald-600 text-white border-emerald-600',
    textarea:    'border-emerald-200 focus:border-emerald-400',
    btn:         'bg-emerald-600 hover:bg-emerald-700',
    title: 'Vad gör detta klippet rätt för WLA?',
    save: 'Spara ja',
  },
  no: {
    wrap: 'bg-red-50/70 border-red-200',
    label: 'text-red-900',
    chip:        'bg-white text-red-700 border-red-300 hover:bg-red-100',
    chipActive:  'bg-red-600 text-white border-red-600',
    textarea:    'border-red-200 focus:border-red-400',
    btn:         'bg-red-600 hover:bg-red-700',
    title: 'Varför valde du bort detta klipp?',
    save: 'Spara nej',
  },
};

function Expanded({ c, onDecided }) {
  // 'yes' | 'no' | null — vilket beslutsformulär som visas
  const [pending, setPending] = useState(null);
  const [categories, setCategories] = useState([]);
  const [note, setNote] = useState('');
  function toggleCat(cat) {
    setCategories(categories.includes(cat)
      ? categories.filter(c => c !== cat)
      : [...categories, cat]);
  }
  function resetForm() {
    setPending(null); setCategories([]); setNote('');
  }
  async function decide(verdict, payload = {}) {
    const noteText = [
      payload.categories?.length ? payload.categories.join(', ') : null,
      payload.note?.trim() || null,
    ].filter(Boolean).join(' · ');
    await api(`/candidate/${c.id}/decision`, {
      method: 'POST',
      body: JSON.stringify({ verdict, note: noteText || null }),
    });
    resetForm();
    onDecided();
  }
  // Embed YouTube-spelare direkt så redaktören kan bedöma utan tab-switch.
  // Shorts (9:16) får smal centrerad player, vanliga klipp får 16:9 fullbredd.
  // youtube-nocookie för minst tracking.
  const embedUrl = `https://www.youtube-nocookie.com/embed/${c.video_id}?rel=0&modestbranding=1`;
  const isShort = c.is_short;
  return (
    <div className="border-t border-slate-200 p-4 bg-slate-50/50 space-y-4">
      <div className={isShort ? "mx-auto" : "w-full"}
           style={isShort ? { aspectRatio: '9/16', maxHeight: 420, maxWidth: 236 } : { aspectRatio: '16/9' }}>
        <iframe
          src={embedUrl}
          title={c.title}
          loading="lazy"
          allow="accelerometer; encrypted-media; gyroscope; picture-in-picture; web-share"
          allowFullScreen
          className="w-full h-full rounded-lg bg-black border border-slate-200"
        />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {Object.entries(SCORE_LABELS).map(([key, label]) => {
          const s = c.scores?.[key];
          if (!s) return null;
          return (
            <div key={key} className="bg-white border border-slate-200 rounded-lg p-3">
              <div className="flex items-baseline justify-between mb-1">
                <div className="text-xs font-semibold text-slate-700">{label}</div>
                <div className="font-mono text-sm font-bold">{Math.round(s.norm)}</div>
              </div>
              <div className="text-xs text-slate-500">{s.explain}</div>
            </div>
          );
        })}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs font-semibold text-slate-700 mb-1">Kontakt</div>
          {c.contact?.found ? (
            <div className="text-xs space-y-0.5">
              {c.contact.email && <div className="font-mono">{c.contact.email}</div>}
              {c.contact.instagram && <div>@{c.contact.instagram}</div>}
            </div>
          ) : (
            <div className="text-xs text-amber-600">kontakt ej hittad</div>
          )}
        </div>
        <div className="bg-white border border-slate-200 rounded-lg p-3">
          <div className="text-xs font-semibold text-slate-700 mb-1">Föreslagen vinkel</div>
          <div className="text-xs">{c.suggested_angle || c.scores?.story?.explain || '—'}</div>
        </div>
      </div>
      <div className="flex items-center justify-between gap-2 pt-1">
        <a href={c.url} target="_blank" rel="noopener" className="text-xs text-blue-600 hover:underline">
          Öppna på YouTube →
        </a>
        <div className="flex gap-2">
          <button onClick={() => decide('snooze')}
            className="px-3 py-1.5 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Snooze</button>
          <button onClick={() => { resetForm(); setPending('no'); }}
            className="px-3 py-1.5 text-xs rounded-md bg-red-50 text-red-700 border border-red-200 hover:bg-red-100">Nej</button>
          <button onClick={() => { resetForm(); setPending('yes'); }}
            className="px-3 py-1.5 text-xs rounded-md bg-emerald-600 text-white hover:bg-emerald-700">Ja</button>
        </div>
      </div>
      {pending && (() => {
        const t = FORM_THEME[pending];
        const cats = pending === 'yes' ? APPROVE_CATEGORIES : REJECT_CATEGORIES;
        return (
          <div className={`border rounded-lg p-3 space-y-3 ${t.wrap}`}>
            <div>
              <div className={`text-xs font-semibold mb-2 ${t.label}`}>{t.title}</div>
              <div className="flex flex-wrap gap-1.5">
                {cats.map(cat => (
                  <button key={cat} onClick={() => toggleCat(cat)}
                    className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${
                      categories.includes(cat) ? t.chipActive : t.chip
                    }`}>
                    {cat}
                  </button>
                ))}
              </div>
            </div>
            <textarea
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="(valfritt fritext)"
              className={`w-full text-xs px-2 py-1.5 border rounded-md resize-none focus:outline-none ${t.textarea}`}
              rows={2}
            />
            <div className="flex gap-2 justify-end">
              <button onClick={resetForm}
                className="px-3 py-1 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Avbryt</button>
              <button onClick={() => decide(pending, { categories, note })}
                className={`px-3 py-1 text-xs rounded-md text-white ${t.btn}`}>{t.save}</button>
            </div>
          </div>
        );
      })()}
      {c.decision && (
        <div className="text-xs text-slate-500 italic">
          Beslut: {c.decision.verdict} · {new Date(c.decision.decided_at).toLocaleString('sv-SE')}
        </div>
      )}
    </div>
  );
}
