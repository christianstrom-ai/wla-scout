"""Anthropic-bedömning av story-/intervjupotential. Steg 5 i scoring-modellen.

Designval:
  - Modell-tiering: Haiku 4.5 default. Escalera till Sonnet 4.6 om Haiku
    rapporterar låg confidence eller om gränsfall (score 40-65).
  - Daglig budget-cap: hård cap från config. När nådd → returnera None
    (graceful degradation, kort visas utan story-score).
  - Cache per (video_id, prompt_hash). Aldrig betala dubbelt.
  - Endast topp-N skickas hit (orkestreringen säkrar det).
"""
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timezone
from typing import Iterable, Optional

import anthropic
from sqlalchemy.orm import Session

from ..config import settings
from ..models import LLMCache, RunLog

# Pris per 1M tokens (USD, juni 2026)
PRICING = {
    "claude-haiku-4-5-20251001": {"in": 1.00, "out": 5.00},
    "claude-sonnet-4-6":         {"in": 3.00, "out": 15.00},
}

PROMPT_VERSION = "v3"

SYSTEM = """Du bedömer YouTube-klipp inför publicering på "We love animals" (WLA).
WLA publicerar korta, känslosamma KLIPP MED VERKLIGA DJUR OCH MÄNNISKOR
kombinerat med en intervju med personen som filmade. WLA publicerar
ALDRIG AI-genererat material — det är ett absolut hinder.

Du ser titel, beskrivning, thumbnail-URL och kanalnamn. Inte videon.

Bedöm två saker:

1. STORY (0-100): chansen att klippet (a) har en identifierbar person
   bakom, (b) en historia värd att berätta, (c) går att intervjua. Anonyma
   virala sekunder = lågt. Tydlig namngiven person + händelse = högt.

2. LOOKS_AI_GENERATED (true/false): VAR HÅRD med att flagga. Hellre fel-
   flagga än släppa igenom. Tecken som var och en räcker för true:
   - "AI", "Stories", "Tales", "Compilation", "Animals Daily" i kanalnamn
     när det inte är ett genuint koncept
   - Kanalnamn med generiska adjektiv ("Amazing", "Incredible", "Best") +
     animal-tema utan personlig info
   - Beskrivning nämner Sora, Veo, Runway, Pika, Midjourney, Stable Diffusion,
     "AI-generated", "generated with", "made with AI"
   - Övernaturligt perfekt thumbnail (osannolika djurkonstellationer,
     omöjliga miljöer, för rena bilder)
   - Beskrivning utan platsangivelser, datum eller personnamn
   - Generiska "wholesome" eller "heartwarming" som enda beskrivning
   - Titel som ren list-form ("Animals saving animals", "Top 10 cute moments")
     utan specifik händelse eller person
   - Kanalnamn med uppenbara numerologiska mönster (T0ny, c@t lover)
   - Korta beskrivningar (< 100 tecken) i kombination med generisk titel
   - Inga taggar, inga länkar, ingen biografisk info i kanalbeskrivning

Returnera ENBART JSON:
{
  "score": <0-100>,
  "reason": "<en mening på engelska, max 20 ord>",
  "confidence": "high" | "medium" | "low",
  "looks_ai_generated": true | false
}"""


def _prompt_hash(title: str, description: str | None, model: str) -> str:
    h = hashlib.sha256()
    h.update(PROMPT_VERSION.encode())
    h.update(model.encode())
    h.update((title or "").encode())
    h.update((description or "").encode())
    return h.hexdigest()


def _user_msg(c: dict) -> str:
    parts = [f"Title: {c.get('title')}"]
    if c.get("channel_title"):
        parts.append(f"Channel: {c['channel_title']}")
    if c.get("description"):
        parts.append(f"Description: {c['description'][:1500]}")
    if c.get("thumbnail_url"):
        parts.append(f"Thumbnail URL: {c['thumbnail_url']}")
    return "\n\n".join(parts)


def _cost(usage: anthropic.types.Usage | dict, model: str) -> float:
    p = PRICING.get(model, {"in": 1.0, "out": 5.0})
    if hasattr(usage, "input_tokens"):
        in_t, out_t = usage.input_tokens, usage.output_tokens
    else:
        in_t, out_t = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
    return (in_t * p["in"] + out_t * p["out"]) / 1_000_000


def _today_spent(db: Session) -> float:
    today = datetime.now(timezone.utc).date().isoformat()
    rows = db.query(RunLog).filter(RunLog.started_at >= today + "T00:00:00").all()
    return float(sum(r.llm_cost_usd or 0 for r in rows))


def score_story(db: Session, run_log: RunLog, candidate: dict) -> Optional[dict]:
    """Returnerar {"score","reason","confidence"} eller None om budget slut/error."""
    if not settings.anthropic_api_key:
        return None

    spent = _today_spent(db) + (run_log.llm_cost_usd or 0)
    if spent >= settings.daily_llm_budget_usd:
        return None  # graceful degradation

    video_id = candidate["video_id"]

    # 1) Cache-lookup för båda modellerna (helst Haiku först)
    for model in (settings.anthropic_model_broad, settings.anthropic_model_escalate):
        h = _prompt_hash(candidate.get("title", ""), candidate.get("description"), model)
        cached = db.query(LLMCache).filter(LLMCache.video_id == video_id, LLMCache.prompt_hash == h).first()
        if cached:
            return cached.response

    # 2) Anropa Haiku
    result = _call(db, run_log, candidate, settings.anthropic_model_broad)
    if not result:
        return None

    # 3) Escalera till Sonnet om Haiku säger låg confidence eller gränsfall
    if (result.get("confidence") == "low") or (40 <= result.get("score", 0) <= 65):
        spent2 = _today_spent(db) + (run_log.llm_cost_usd or 0)
        if spent2 < settings.daily_llm_budget_usd:
            sonnet = _call(db, run_log, candidate, settings.anthropic_model_escalate)
            if sonnet:
                return sonnet

    return result


def _call(db: Session, run_log: RunLog, candidate: dict, model: str) -> Optional[dict]:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    h = _prompt_hash(candidate.get("title", ""), candidate.get("description"), model)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=200,
            system=SYSTEM,
            messages=[{"role": "user", "content": _user_msg(candidate)}],
        )
    except Exception as exc:  # noqa: BLE001
        run_log.error = (run_log.error or "") + f"\nLLM err {model}: {exc}"[:500]
        return None

    text = (resp.content[0].text if resp.content else "").strip()
    parsed = _parse(text)
    cost = _cost(resp.usage, model)

    run_log.llm_calls = (run_log.llm_calls or 0) + 1
    run_log.llm_tokens_in = (run_log.llm_tokens_in or 0) + resp.usage.input_tokens
    run_log.llm_tokens_out = (run_log.llm_tokens_out or 0) + resp.usage.output_tokens
    run_log.llm_cost_usd = (run_log.llm_cost_usd or 0) + cost

    if parsed is None:
        return None

    db.add(LLMCache(
        video_id=candidate["video_id"],
        prompt_hash=h,
        model=model,
        response=parsed,
        tokens_in=resp.usage.input_tokens,
        tokens_out=resp.usage.output_tokens,
        cost_usd=cost,
    ))
    db.commit()
    return parsed


def _parse(text: str) -> Optional[dict]:
    s = text.find("{")
    e = text.rfind("}")
    if s < 0 or e < 0:
        return None
    try:
        d = json.loads(text[s:e + 1])
    except json.JSONDecodeError:
        return None
    if "score" not in d:
        return None
    return {
        "score": int(max(0, min(100, d.get("score", 0)))),
        "reason": str(d.get("reason", ""))[:200],
        "confidence": d.get("confidence", "medium"),
        "looks_ai_generated": bool(d.get("looks_ai_generated", False)),
    }
