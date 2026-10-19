import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';

// Storys = Claude-scoutens dagliga flöde (TikTok, Instagram, YouTube, Facebook).
// Data kommer från /api/scout/items; beslut skrivs tillbaka och synkas till
// datarepot så att nästa morgonkörning lär sig av dem.

const TABS = [
  { id: 'ny', label: 'Att granska' },
  { id: 'yes', label: 'Ja – kontakta' },
  { id: 'contacted', label: 'Kontaktade' },
  { id: 'snooze', label: 'Snooze' },
  { id: 'no', label: 'Nej' },
];

const PLATFORM = { youtube: 'YouTube', tiktok: 'TikTok', instagram: 'Instagram', facebook: 'Facebook' };

const fmt = n => {
  n = Number(n) || 0;
  if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1).replace('.', ',') + ' mn';
  if (n >= 1e3) return Math.round(n / 1e3) + ' k';
  return String(n);
};
const dur = s => {
  s = Math.round(Number(s) || 0);
  return s >= 60 ? `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}` : `${s} s`;
};
const dayName = d => {
  try {
    const s = new Date(d + 'T12:00:00').toLocaleDateString('sv-SE', { weekday: 'long', day: 'numeric', month: 'short' });
    return s.charAt(0).toUpperCase() + s.slice(1);
  } catch { return d; }
};

function embedFor(item) {
  const url = item.url || '';
  const id = item.id || '';
  if (item.platform === 'tiktok') {
    const m = url.match(/\/video\/(\d+)/) || id.match(/^tt-(\d+)$/);
    return m ? { src: `https://www.tiktok.com/embed/v2/${m[1]}`, tall: true } : null;
  }
  if (item.platform === 'instagram') {
    const m = url.match(/instagram\.com\/(?:reel|reels|p|tv)\/([\w-]+)/) || id.match(/^ig-([\w-]+)$/);
    return m ? { src: `https://www.instagram.com/reel/${m[1]}/embed`, tall: true } : null;
  }
  if (item.platform === 'youtube') {
    const m = url.match(/(?:v=|shorts\/|youtu\.be\/)([\w-]{6,})/) || id.match(/^yt-([\w-]+)$/);
    const isShort = /shorts\//.test(url) || (item.duration_sec || 0) <= 60;
    return m ? { src: `https://www.youtube-nocookie.com/embed/${m[1]}?rel=0&modestbranding=1`, tall: isShort } : null;
  }
  if (item.platform === 'facebook') {
    return { src: `https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(url)}&show_text=false`, tall: false };
  }
  return null;
}

export default function Storys() {
  const [data, setData] = useState(null);
  const [error, setError] = useState('');
  const [tab, setTab] = useState(() => {
    try { return localStorage.getItem('wla-storys-tab') || 'ny'; } catch { return 'ny'; }
  });
  const [openId, setOpenId] = useState(null);
  const [syncing, setSyncing] = useState(false);

  async function refresh() {
    try {
      setData(await api('/scout/items'));
      setError('');
    } catch (e) { setError('Kunde inte hämta storys. Ladda om sidan.'); }
  }
  useEffect(() => { refresh(); }, []);
  useEffect(() => { try { localStorage.setItem('wla-storys-tab', tab); } catch { /* ignoreras */ } }, [tab]);

  function replaceItem(updated) {
    setData(d => ({ ...d, items: d.items.map(i => (i.id === updated.id ? updated : i)) }));
  }

  async function syncNow() {
    setSyncing(true);
    try { await api('/admin/scout-sync', { method: 'POST' }); await refresh(); }
    catch { setError('Synken misslyckades. Kontrollera datarepo och token under Status.'); }
    finally { setSyncing(false); }
  }

  const counts = useMemo(() => {
    const c = {};
    for (const i of data?.items || []) c[i.status] = (c[i.status] || 0) + 1;
    return c;
  }, [data]);

  const groups = useMemo(() => {
    const list = (data?.items || []).filter(i => i.status === tab);
    const byDay = {};
    for (const i of list) (byDay[i.feed_date] ||= []).push(i);
    return Object.keys(byDay).sort().reverse()
      .map(d => [d, byDay[d].sort((a, b) => (b.score || 0) - (a.score || 0))]);
  }, [data, tab]);

  if (!data) return <div className="text-slate-500">{error || 'Laddar…'}</div>;
  const run = data.last_run;

  return (
    <div>
      <div className="flex flex-wrap items-baseline justify-between gap-2 mb-3">
        <h1 className="text-xl font-bold">Storys</h1>
        <div className="text-xs text-slate-500 flex flex-wrap items-center gap-3">
          {run ? (
            <span>
              Senaste körning {dayName(run.feed_date)}
              {typeof run.screened === 'number' && <> · {run.screened} klipp granskade</>}
              {typeof run.kept === 'number' && <> · {run.kept} kvar</>}
            </span>
          ) : <span>Ingen körning ännu</span>}
          {data.sync_configured && (
            <button onClick={syncNow} disabled={syncing}
              className="px-2 py-1 rounded-md border border-slate-300 hover:bg-slate-100 disabled:opacity-50">
              {syncing ? 'Synkar…' : 'Synka nu'}
            </button>
          )}
        </div>
      </div>
      {run?.note && <p className="text-xs text-slate-500 mb-3">{run.note}</p>}
      {error && <div className="text-sm text-red-700 mb-3">{error}</div>}

      <div className="flex gap-1 border-b border-slate-200 mb-4 overflow-x-auto">
        {TABS.map(t => (
          <button key={t.id} onClick={() => { setTab(t.id); setOpenId(null); }}
            className={`px-3 py-2 text-sm whitespace-nowrap border-b-2 -mb-px ${
              tab === t.id ? 'border-slate-900 text-slate-900 font-semibold' : 'border-transparent text-slate-500 hover:text-slate-800'}`}>
            {t.label}<span className="ml-1.5 font-mono text-xs text-slate-400">{counts[t.id] || 0}</span>
          </button>
        ))}
      </div>

      {!data.sync_configured && data.items.length === 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 text-sm text-slate-600">
          Storys är inte kopplat än. Sätt <code className="font-mono">SCOUT_DATA_REPO</code> och{' '}
          <code className="font-mono">SCOUT_GITHUB_TOKEN</code> i Render, så hämtas Claude-scoutens storys var 15:e minut.
        </div>
      )}
      {data.sync_configured && groups.length === 0 && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 text-center text-sm text-slate-500">
          {{ ny: 'Allt är granskat. Nya storys kommer i morgon bitti.',
             yes: 'Inga storys väntar på kontakt.',
             contacted: 'Ingen är markerad som kontaktad än.',
             snooze: 'Inget snoozat.', no: 'Inga nej än.' }[tab]}
        </div>
      )}

      <div className="space-y-6">
        {groups.map(([day, items]) => (
          <section key={day}>
            <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-500 mb-2">{dayName(day)}</h2>
            <div className="space-y-3">
              {items.map(i => (
                <StoryCard key={i.id} item={i} open={openId === i.id}
                  onToggle={() => setOpenId(openId === i.id ? null : i.id)}
                  onChange={replaceItem}
                  rejectReasons={data.reject_reasons} approveReasons={data.approve_reasons} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function Check({ label, value }) {
  const v = String(value || '').toLowerCase();
  const ok = ['ja', 'nej', 'egen röst', 'original'].includes(v);
  const bad = ['pålagd vo', 'ai', 'omladdad'].includes(v);
  const cls = ok ? 'bg-emerald-50 text-emerald-700' : bad ? 'bg-red-50 text-red-700' : 'bg-amber-50 text-amber-800';
  return <span className={`text-[11px] px-2 py-0.5 rounded-full ${cls}`}>{label}: {value || 'okänt'}</span>;
}

function StoryCard({ item, open, onToggle, onChange, rejectReasons, approveReasons }) {
  const st = item.status;
  const verdictLine = [(item.reasons || []).join(', '), item.decision_note].filter(Boolean).join(' · ');
  return (
    <div className="bg-white border border-slate-200 rounded-lg overflow-hidden">
      <button onClick={onToggle} className="w-full text-left p-3 flex gap-3 hover:bg-slate-50">
        <div className="w-16 sm:w-20 flex-shrink-0 aspect-[9/16] bg-slate-100 rounded overflow-hidden relative">
          {item.thumb_url
            ? <img src={item.thumb_url} alt="" className="w-full h-full object-cover" loading="lazy" />
            : <span className="absolute inset-0 grid place-items-center text-[10px] font-semibold uppercase text-slate-400">{PLATFORM[item.platform] || 'Klipp'}</span>}
          <span className="absolute right-1 bottom-1 bg-black/70 text-white text-[10px] font-mono px-1 rounded">{dur(item.duration_sec)}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-slate-500">
            <span className="text-[10px] font-bold uppercase px-1.5 rounded bg-slate-100 text-slate-700">{PLATFORM[item.platform] || item.platform}</span>
            <span className="font-semibold text-slate-700 truncate max-w-[200px]">{item.creator}</span>
            <span className="font-mono">{fmt(item.followers)} följare</span>
            <span className="font-mono">{fmt(item.views)} visn{item.outlier ? ` · ${Math.round(item.outlier)}× normalt` : ''}</span>
            {item.priority === 'bevaka' && <span className="px-1.5 rounded bg-amber-100 text-amber-800">Bevaka</span>}
          </div>
          <div className="font-semibold text-sm leading-snug mt-1">{item.headline}</div>
          {item.summary && <div className="text-xs text-slate-600 mt-1 line-clamp-3">{item.summary}</div>}
          {st === 'no' && verdictLine && <div className="text-xs text-red-700 mt-1">Nej: {verdictLine}</div>}
          {(st === 'yes' || st === 'contacted') && verdictLine && <div className="text-xs text-emerald-700 mt-1">Ja: {verdictLine}</div>}
        </div>
        <div className="text-right leading-none pl-1">
          <div className="text-xl font-bold font-mono">{item.score != null ? Math.round(item.score) : '–'}</div>
          <div className="text-[9px] uppercase text-slate-400 mt-0.5">score</div>
        </div>
      </button>
      {open && <Expanded item={item} onChange={onChange} rejectReasons={rejectReasons} approveReasons={approveReasons} />}
    </div>
  );
}

const FORM_THEME = {
  yes: { wrap: 'bg-emerald-50/70 border-emerald-200', label: 'text-emerald-900',
         chip: 'bg-white text-emerald-700 border-emerald-300 hover:bg-emerald-100', chipActive: 'bg-emerald-600 text-white border-emerald-600',
         btn: 'bg-emerald-600 hover:bg-emerald-700', title: 'Vad gör detta klippet rätt för WLA?', save: 'Spara ja' },
  no:  { wrap: 'bg-red-50/70 border-red-200', label: 'text-red-900',
         chip: 'bg-white text-red-700 border-red-300 hover:bg-red-100', chipActive: 'bg-red-600 text-white border-red-600',
         btn: 'bg-red-600 hover:bg-red-700', title: 'Varför valde du bort detta klipp?', save: 'Spara nej' },
};

function Expanded({ item, onChange, rejectReasons, approveReasons }) {
  const [pending, setPending] = useState(null);
  const [cats, setCats] = useState([]);
  const [note, setNote] = useState('');
  const [teamNote, setTeamNote] = useState(item.team_note || '');
  const [busy, setBusy] = useState(false);
  const [showRequest, setShowRequest] = useState(item.status === 'yes');
  const st = item.status;
  const c = item.checks || {};
  const embed = embedFor(item);

  async function decide(status, payload = {}) {
    setBusy(true);
    try {
      const updated = await api(`/scout/items/${item.id}/decision`, {
        method: 'POST', body: JSON.stringify({ status, reasons: payload.reasons || [], note: payload.note || null }),
      });
      setPending(null); setCats([]); setNote('');
      onChange(updated);
    } finally { setBusy(false); }
  }
  async function saveTeamNote() {
    if ((teamNote || '') === (item.team_note || '')) return;
    const updated = await api(`/scout/items/${item.id}/note`, { method: 'PATCH', body: JSON.stringify({ team_note: teamNote }) });
    onChange(updated);
  }

  return (
    <div className="border-t border-slate-200 p-4 bg-slate-50/50 space-y-4">
      {embed && (
        <div className={embed.tall ? 'mx-auto' : 'w-full'}
             style={embed.tall ? { aspectRatio: '9/16', maxHeight: 560, maxWidth: 315 } : { aspectRatio: '16/9' }}>
          <iframe src={embed.src} title={item.headline || 'Klipp'} loading="lazy"
            allow="accelerometer; encrypted-media; gyroscope; picture-in-picture; web-share; autoplay"
            allowFullScreen className="w-full h-full rounded-lg bg-black border border-slate-200" />
        </div>
      )}

      <div className="flex flex-wrap gap-1.5">
        <Check label="Story" value={c.story} /><Check label="Original" value={c.original} />
        <Check label="AI" value={c.ai} /><Check label="Ljud" value={c.voice} />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        {item.why && <Box title="Varför den passar">{item.why}</Box>}
        {item.angle && <Box title="Föreslagen vinkel">{item.angle}</Box>}
        {item.contact && <Box title="Kontakt">{item.contact}</Box>}
        {item.creator_url && (
          <Box title="Kreatör">
            <a href={item.creator_url} target="_blank" rel="noopener" className="text-blue-600 hover:underline">{item.creator}</a>
          </Box>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <a href={item.url} target="_blank" rel="noopener" className="text-xs text-blue-600 hover:underline">
          Öppna på {PLATFORM[item.platform] || 'plattformen'} →
        </a>
        <div className="flex flex-wrap gap-2">
          {(st === 'ny' || st === 'snooze') && <>
            {st === 'ny' && <button disabled={busy} onClick={() => decide('snooze')}
              className="px-3 py-1.5 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Snooze</button>}
            <button disabled={busy} onClick={() => { setCats([]); setNote(''); setPending('no'); }}
              className="px-3 py-1.5 text-xs rounded-md bg-red-50 text-red-700 border border-red-200 hover:bg-red-100">Nej</button>
            <button disabled={busy} onClick={() => { setCats([]); setNote(''); setPending('yes'); }}
              className="px-3 py-1.5 text-xs rounded-md bg-emerald-600 text-white hover:bg-emerald-700">Ja</button>
          </>}
          {(st === 'yes' || st === 'contacted') && (
            <button onClick={() => setShowRequest(v => !v)}
              className="px-3 py-1.5 text-xs rounded-md bg-emerald-600 text-white hover:bg-emerald-700">
              {showRequest ? 'Dölj förfrågan' : 'Skriv förfrågan'}
            </button>
          )}
          {st === 'yes' && <button disabled={busy} onClick={() => decide('contacted')}
            className="px-3 py-1.5 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Markera kontaktad</button>}
          {st !== 'ny' && <button disabled={busy} onClick={() => decide('ny')}
            className="px-3 py-1.5 text-xs rounded-md text-slate-500 hover:bg-slate-100">Ångra</button>}
        </div>
      </div>

      {pending && (() => {
        const t = FORM_THEME[pending];
        const list = pending === 'yes' ? approveReasons : rejectReasons;
        return (
          <div className={`border rounded-lg p-3 space-y-3 ${t.wrap}`}>
            <div>
              <div className={`text-xs font-semibold mb-2 ${t.label}`}>{t.title}</div>
              <div className="flex flex-wrap gap-1.5">
                {list.map(cat => (
                  <button key={cat} onClick={() => setCats(cats.includes(cat) ? cats.filter(x => x !== cat) : [...cats, cat])}
                    className={`px-2 py-1 text-[11px] rounded-md border transition-colors ${cats.includes(cat) ? t.chipActive : t.chip}`}>
                    {cat}
                  </button>
                ))}
              </div>
            </div>
            <textarea value={note} onChange={e => setNote(e.target.value)} rows={2}
              placeholder={pending === 'no' ? 'Valfritt: berätta mer, det hjälper morgondagens sökning' : 'Valfritt fritext'}
              className="w-full text-xs px-2 py-1.5 border border-slate-200 rounded-md resize-none focus:outline-none" />
            <div className="flex gap-2 justify-end">
              <button onClick={() => setPending(null)} className="px-3 py-1 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Avbryt</button>
              <button disabled={busy} onClick={() => decide(pending, { reasons: cats, note })}
                className={`px-3 py-1 text-xs rounded-md text-white ${t.btn}`}>{t.save}</button>
            </div>
          </div>
        );
      })()}

      {(st === 'yes' || st === 'contacted') && showRequest && <RequestComposer item={item} />}

      <div>
        <label className="text-xs font-semibold text-slate-700" htmlFor={`team-note-${item.id}`}>Anteckning till teamet</label>
        <textarea id={`team-note-${item.id}`} value={teamNote} onChange={e => setTeamNote(e.target.value)} onBlur={saveTeamNote}
          rows={2} placeholder="Syns för alla, sparas när du klickar utanför rutan"
          className="mt-1 w-full text-xs px-2 py-1.5 border border-slate-200 rounded-md resize-y focus:outline-none focus:border-slate-400" />
      </div>
    </div>
  );
}

function Box({ title, children }) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-3">
      <div className="text-xs font-semibold text-slate-700 mb-1">{title}</div>
      <div className="text-xs text-slate-700">{children}</div>
    </div>
  );
}

// ---------- Förfrågningsmall ----------

export function buildLetter(f) {
  const name = (f.creator_name || '').trim();
  const me = (f.my_name || '').trim() || '[ditt namn]';
  const subject = (f.subject || '').trim() || '[vad videon visar]';
  const topic = (f.topic || '').trim() || '[intervjuämne]';
  const hook = (f.hook || '').trim();
  const interview = f.kind === 'interview';
  if (f.length === 'dm') {
    return [
      `Hi ${name || 'there'}! I'm ${me}, a video editor at Newsner (20M+ followers in 11 languages).`,
      `Your video of ${subject} really stood out to us.${hook ? ' ' + hook : ''}`,
      interview
        ? `Would you be open to a short Zoom interview about ${topic}, and would you give us permission to share your video on our platforms? You'd of course be credited as the original creator.`
        : `Would you give us permission to share your video on our platforms? You'd of course be credited as the original creator.`,
      `Thank you!\n${me}`,
    ].join('\n\n');
  }
  return [
    `Hi ${name || 'there'}!`,
    `My name is ${me}, and I'm a video editor at the viral news network Newsner, which reaches more than 20 million followers across 11 languages.`,
    `I recently came across your video of ${subject}, and I just had to reach out.${hook ? ' ' + hook : ''}`,
    interview
      ? `We'd love to share your story with our audience. Would you be open to a short Zoom interview where you tell us a little more about ${topic}?`
      : `We'd love to share your story with our audience.`,
    `Would you ${interview ? 'also ' : ''}be happy to give us permission to use your video and publish it on our platforms? We would, of course, credit you as the original creator, with a link to your account.`,
    `Looking forward to hearing from you!`,
    `All the best,\n${me}\nVideo editor, Newsner`,
  ].join('\n\n');
}

function RequestComposer({ item }) {
  const [f, setF] = useState(() => {
    let myName = '';
    try { myName = localStorage.getItem('wla-myname') || ''; } catch { /* ignoreras */ }
    return {
      my_name: myName, creator_name: item.creator_name || '',
      subject: item.request_subject || '', hook: item.request_hook || '', topic: item.request_topic || '',
      kind: 'interview', length: 'mail',
    };
  });
  const [text, setText] = useState(() => buildLetter(f));
  const [prev, setPrev] = useState(null);
  const [proofing, setProofing] = useState(false);
  const [msg, setMsg] = useState('');

  function setField(key, value) {
    const next = { ...f, [key]: value };
    setF(next);
    setText(buildLetter(next));
    setPrev(null);
    if (key === 'my_name') { try { localStorage.setItem('wla-myname', value); } catch { /* ignoreras */ } }
  }
  async function proofread() {
    setProofing(true); setMsg('');
    try {
      const res = await api('/scout/proofread', { method: 'POST', body: JSON.stringify({ text }) });
      if (res.changed) { setPrev(text); setText(res.text); setMsg('Texten är rättad. Läs igenom innan du kopierar.'); }
      else setMsg('Inga fel hittades.');
    } catch { setMsg('Rättningen misslyckades, försök igen.'); }
    finally { setProofing(false); }
  }
  async function copy() {
    try { await navigator.clipboard.writeText(text); setMsg('Texten är kopierad.'); }
    catch { setMsg('Kunde inte kopiera automatiskt. Markera texten och kopiera själv.'); }
  }

  const field = (key, label, placeholder, wide, en = true) => (
    <label className={`text-[11px] text-slate-600 grid gap-1 ${wide ? 'sm:col-span-2' : ''}`}>
      {label}
      <input value={f[key]} onChange={e => setField(key, e.target.value)} placeholder={placeholder}
        spellCheck lang={en ? 'en' : undefined}
        className={`text-sm px-2 py-1.5 border rounded-md focus:outline-none focus:border-slate-400 ${
          !f[key].trim() && ['my_name', 'subject', 'topic'].includes(key) ? 'border-amber-400 bg-amber-50' : 'border-slate-200 bg-white'}`} />
    </label>
  );

  return (
    <div className="border border-slate-200 rounded-lg p-3 space-y-3 bg-white">
      <div className="text-xs font-semibold text-slate-700">Förfrågan till {item.creator || 'kreatören'}</div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        {field('my_name', 'Ditt namn', 't.ex. Tom', false, false)}
        {field('creator_name', 'Kreatörens förnamn (om du vet det)', 'Tomt blir “Hi there!”', false, false)}
        {field('subject', 'Vad videon visar (engelska)', 'rescuing a stray dog at your workshop', true)}
        {field('hook', 'Personlig rad (engelska)', 'Vad som berörde dig i just den här videon', true)}
        {field('topic', 'Intervjuämne (engelska)', 'how she is doing today and what happened next', true)}
        <label className="text-[11px] text-slate-600 grid gap-1">Typ
          <select value={f.kind} onChange={e => setField('kind', e.target.value)} className="text-sm px-2 py-1.5 border border-slate-200 rounded-md bg-white">
            <option value="interview">Intervju och tillstånd</option>
            <option value="permission">Bara tillstånd</option>
          </select>
        </label>
        <label className="text-[11px] text-slate-600 grid gap-1">Längd
          <select value={f.length} onChange={e => setField('length', e.target.value)} className="text-sm px-2 py-1.5 border border-slate-200 rounded-md bg-white">
            <option value="mail">Mejl</option>
            <option value="dm">Kort DM</option>
          </select>
        </label>
      </div>
      <textarea value={text} onChange={e => setText(e.target.value)} spellCheck lang="en" rows={14}
        aria-label="Förfrågningstext"
        className="w-full text-sm leading-relaxed px-3 py-2 border border-slate-200 rounded-md resize-y focus:outline-none focus:border-slate-400" />
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-500 mr-auto">
          {msg || 'Ändrar du i rutorna skrivs texten om. Rätta språket sist, precis innan du kopierar.'}
        </span>
        {prev != null && <button onClick={() => { setText(prev); setPrev(null); setMsg(''); }}
          className="px-3 py-1.5 text-xs rounded-md border border-slate-300 hover:bg-slate-100">Ångra rättning</button>}
        <button onClick={proofread} disabled={proofing}
          className="px-3 py-1.5 text-xs rounded-md border border-slate-300 hover:bg-slate-100 disabled:opacity-50">
          {proofing ? 'Rättar…' : 'Rätta språket'}
        </button>
        <button onClick={copy} className="px-3 py-1.5 text-xs rounded-md bg-slate-900 text-white hover:bg-slate-700">Kopiera texten</button>
      </div>
    </div>
  );
}
