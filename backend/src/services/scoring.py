"""Matematiska delscorer (1-4 + 6) och original-källa-bedömning (7).
LLM-baserad story-score (5) hanteras separat i services/llm.py."""
from __future__ import annotations
import math
import re
from datetime import datetime, timezone
from typing import Sequence
from rapidfuzz import fuzz

from ..config import settings


def hours_since(when: datetime) -> float:
    return max(0.5, (datetime.now(timezone.utc) - when).total_seconds() / 3600)


# ---- Normaliseringar ----

def _saturating_log(value: float, scale: float) -> float:
    """Mjuk 0-100 över log-skala. scale = referensvärde för ~85."""
    if value <= 0:
        return 0.0
    raw = math.log10(1 + value) / math.log10(1 + scale)
    return float(min(100.0, max(0.0, raw * 100)))


# ---- 1. Velocity ----

def score_velocity(views: int, published_at: datetime, is_short: bool) -> dict:
    """Views per timme. Shorts mäts mot kortare ref-skala."""
    h = hours_since(published_at)
    vph = views / h
    ref = 5000 if is_short else 1500  # ~85-poäng-referens
    norm = _saturating_log(vph, ref)
    return {
        "raw": round(vph, 1),
        "norm": round(norm, 1),
        "explain": f"{int(vph)} visningar/h över {int(h)}h",
    }


# ---- 2. Engagement ----

def score_engagement(views: int, likes: int, comments: int) -> dict:
    if views <= 0:
        return {"raw": 0, "norm": 0.0, "explain": "0 visningar"}
    rate = (likes + comments) / views
    # 5% engagement ≈ 85 poäng (mycket bra för djurinnehåll)
    norm = min(100.0, rate * 100 * (100 / 5))
    return {
        "raw": round(rate, 4),
        "norm": round(norm, 1),
        "explain": f"{likes + comments} interaktioner / {views} visningar",
    }


# ---- 3. Överprestation mot kanalstorlek ----

def score_overperformance(views: int, subs: int | None) -> dict:
    if not subs or subs <= 0:
        return {"raw": 0, "norm": 0.0, "explain": "okänt antal prenumeranter"}
    ratio = views / subs
    norm = _saturating_log(ratio, 20)  # 20x sub-count ≈ 85
    return {
        "raw": round(ratio, 2),
        "norm": round(norm, 1),
        "explain": f"{round(ratio,1)}× kanalens prenumeranter",
    }


# ---- 4. Profilmatchning mot vinnarprofilen ----

WORD_RE = re.compile(r"[a-zA-Z0-9']{3,}")


def _ngrams(tokens: Sequence[str], n: int) -> list[str]:
    if len(tokens) < n:
        return []
    return [" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]


def score_profile_match(text: str, profile_keywords: list[dict]) -> dict:
    """profile_keywords = [{ngram, weight, n}] från WinnerProfile."""
    if not profile_keywords:
        return {"raw": 0, "norm": 0.0, "explain": "ingen vinnarprofil"}
    tokens = [t.lower() for t in WORD_RE.findall(text or "")]
    grams = set(tokens) | set(_ngrams(tokens, 2)) | set(_ngrams(tokens, 3))
    matched_weight = 0.0
    total_weight = 0.0
    hits = []
    for kw in profile_keywords:
        total_weight += kw["weight"]
        if kw["ngram"] in grams:
            matched_weight += kw["weight"]
            hits.append(kw["ngram"])
    if total_weight <= 0:
        return {"raw": 0, "norm": 0.0, "explain": "profil saknar vikter"}
    norm = (matched_weight / total_weight) * 100
    return {
        "raw": round(matched_weight, 2),
        "norm": round(min(100.0, norm), 1),
        "explain": f"{len(hits)} träffar: {', '.join(hits[:5])}",
    }


# ---- 6. Nåbarhet ----

def score_reachability(contact_found: bool, has_email: bool, has_instagram: bool) -> dict:
    if not contact_found:
        return {"raw": 0, "norm": 10.0, "explain": "kontakt ej hittad"}
    if has_email and has_instagram:
        return {"raw": 2, "norm": 95.0, "explain": "mejl + Instagram"}
    if has_email:
        return {"raw": 1, "norm": 85.0, "explain": "mejl"}
    return {"raw": 1, "norm": 65.0, "explain": "Instagram"}


# ---- 7. Originalkälla ----

AGGREGATOR_HINTS = re.compile(
    r"\b(compilation|viral|funny\s*animals|epic|best\s*of|tiktok|trending)\b",
    re.IGNORECASE,
)
CREDIT_TO = re.compile(r"\b(credit\s*(?:to|:)|via\b|original\s*by|©|reposted)\b", re.IGNORECASE)

# Heuristik för uppenbart AI-genererat innehåll. Hårdfilter i scout:
# matchar en av dessa → kandidaten kommer aldrig nå LLM eller feed.
AI_CHANNEL_PATTERNS = re.compile(
    r"\b("
    r"ai\s+(pet|animal|cat|dog|wildlife|stories|tales)|"
    r"(animal|pet|cat|dog)\s+(tales|stories|moments|world)\s*(daily|24|7|365|hd|tv|official)?|"
    r"(amazing|incredible|epic|best|top)\s+(animal|pet|cat|dog)|"
    r"(generated|created|made)\s+(with|using|by)\s+ai|"
    r"sora|veo\s+3|runway|pika\s+labs|midjourney|stable\s+diffusion"
    r")\b",
    re.IGNORECASE,
)
AI_DESCRIPTION_PATTERNS = re.compile(
    r"\b("
    r"ai[-\s]?generated|"
    r"generated\s+(with|by)\s+ai|"
    r"made\s+(with|using)\s+ai|"
    r"created\s+(with|using|by)\s+(sora|veo|runway|pika|midjourney|stable\s+diffusion)|"
    r"#aiart|#aianimal|#aigenerated"
    r")\b",
    re.IGNORECASE,
)


# Hårdfilter: kandidaten måste nämna något djurord i titel/beskrivning.
# Utan detta blir feeden full av kricket, politik och filmrecensioner.
ANIMAL_TOPIC_RE = re.compile(
    r"\b("
    r"animal|pet|kitten|kittens|cat|cats|kitty|puppy|puppies|dog|dogs|doggo|"
    r"wildlife|stray|rescue|rescued|adopt|adopted|adoption|shelter|"
    r"horse|horses|pony|foal|colt|filly|stallion|mare|"
    r"bird|birds|owl|parrot|eagle|hawk|falcon|crow|raven|sparrow|pigeon|seagull|"
    r"wolf|wolves|fox|foxes|bear|bears|deer|moose|elk|"
    r"raccoon|squirrel|hedgehog|otter|beaver|opossum|skunk|"
    r"rabbit|rabbits|bunny|bunnies|hamster|guinea[-\s]?pig|ferret|chinchilla|"
    r"monkey|monkeys|ape|apes|gorilla|chimp|orangutan|baboon|"
    r"elephant|giraffe|zebra|lion|tiger|leopard|cheetah|panther|jaguar|cougar|"
    r"sheep|lamb|cow|cows|cattle|calf|goat|pig|piglet|donkey|llama|alpaca|"
    r"dolphin|whale|shark|seal|walrus|orca|manatee|"
    r"turtle|tortoise|lizard|gecko|iguana|snake|frog|toad|salamander|"
    r"chicken|chicks|duck|ducks|duckling|goose|geese|swan|peacock|hen|rooster|"
    r"koala|kangaroo|wallaby|panda|sloth|wombat|tasmanian|"
    r"mouse|mice|rat|rats|mole|bat|bats|"
    r"farm[-\s]+animal|sea[-\s]+creature|reptile|amphibian|mammal|marsupial|"
    r"vet|veterinarian|veterinary"
    r")\b",
    re.IGNORECASE,
)


def is_animal_topic(*, title: str | None, description: str | None) -> bool:
    """True om titel eller beskrivnings första 500 tecken nämner djurord."""
    text = f"{title or ''} {(description or '')[:500]}"
    return bool(ANIMAL_TOPIC_RE.search(text))


def looks_ai_heuristic(*, title: str, description: str | None, channel_title: str | None, channel_description: str | None) -> tuple[bool, str | None]:
    """Snabb regex-pre-filter för uppenbart AI-innehåll. Returnerar
    (is_ai, reason) — om reason är None passerade kandidaten."""
    ct = channel_title or ""
    cd = channel_description or ""
    desc = description or ""
    if AI_CHANNEL_PATTERNS.search(ct):
        return True, f"kanalnamn matchar AI-mönster: '{ct}'"
    if AI_DESCRIPTION_PATTERNS.search(desc) or AI_DESCRIPTION_PATTERNS.search(cd):
        return True, "beskrivning nämner AI-generering"
    if AI_DESCRIPTION_PATTERNS.search(title):
        return True, "titel nämner AI-generering"
    # Tom kanalbeskrivning + generisk kanalnamn = misstänkt
    if not cd.strip() and re.search(r"^(amazing|epic|cute|best|top|incredible|wholesome)\s+(animal|pet|cat|dog|wildlife)", ct.strip(), re.IGNORECASE):
        return True, "generisk kanalnamn utan biografisk info"
    return False, None


def score_originality(
    *,
    title: str,
    description: str | None,
    channel_title: str | None,
    channel_description: str | None,
    similar_existing_titles: list[str] | None = None,
) -> tuple[dict, bool, dict]:
    """Returnerar (score-dict, is_hard_filtered, flags).

    Hårdfiltret slår till bara om kanalnamnet ser ut som ren aggregator-
    konto. Vid text-sim-träff: släpp igenom men flagga `source_confidence=low`.
    """
    flags = {
        "aggregator_channel": False,
        "credit_to_in_description": False,
        "similar_title_exists": False,
        "similar_title_sample": None,
    }
    description = description or ""
    channel_title = channel_title or ""
    channel_description = channel_description or ""

    if AGGREGATOR_HINTS.search(channel_title) and not channel_description.strip():
        flags["aggregator_channel"] = True
        return (
            {"raw": 0, "norm": 0.0, "explain": "kanalnamn ser ut som aggregator"},
            True,
            flags,
        )

    if CREDIT_TO.search(description):
        flags["credit_to_in_description"] = True

    if similar_existing_titles:
        best = None
        best_score = 0
        for t in similar_existing_titles:
            s = fuzz.token_set_ratio(title, t)
            if s > best_score:
                best_score = s
                best = t
        if best_score >= 85:
            flags["similar_title_exists"] = True
            flags["similar_title_sample"] = best

    # Score: börja på 90, dra av för flaggor
    score = 90
    if flags["credit_to_in_description"]:
        score -= 30
    if flags["similar_title_exists"]:
        score -= 30
    score = max(0, score)

    explain = "ser unik ut"
    if flags["credit_to_in_description"] and flags["similar_title_exists"]:
        explain = "credit-to + liknande titel finns"
    elif flags["credit_to_in_description"]:
        explain = "credit-to i beskrivning"
    elif flags["similar_title_exists"]:
        explain = "liknande titel finns redan"

    return (
        {"raw": score, "norm": float(score), "explain": explain},
        False,
        flags,
    )


# ---- Totalscore ----

def combine(scores: dict, weights: dict[str, float]) -> float:
    """scores[k] = {raw, norm, explain}. weights[k] = float. Allt 0-100."""
    total = 0.0
    wsum = 0.0
    for key, w in weights.items():
        if key in scores and isinstance(scores[key], dict) and "norm" in scores[key]:
            total += scores[key]["norm"] * w
            wsum += w
    if wsum <= 0:
        return 0.0
    return round(total / wsum * (sum(weights.values()) or 1), 2)


def pick_weights(is_short: bool) -> dict[str, float]:
    return settings.weights_shorts if is_short else settings.weights_longform
