"""DB-modeller. Schemat följer specen §11."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    String, Integer, Float, Boolean, DateTime, Text, ForeignKey, JSON,
    UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Candidate(Base):
    """En råkandidat hittad via YouTube-scouting. Lever även om den aldrig
    levereras eller väljs bort. Hålls i 30/90 dagar enligt rensningsregler."""
    __tablename__ = "candidates"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    tracking_id: Mapped[str] = mapped_column(String, default=_uuid, unique=True, index=True)
    video_id: Mapped[str] = mapped_column(String, index=True)
    channel_id: Mapped[str] = mapped_column(String, index=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    thumbnail_url: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    is_short: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(16))

    channel_title: Mapped[str | None] = mapped_column(Text)
    channel_subscribers: Mapped[int | None] = mapped_column(Integer)
    channel_description: Mapped[str | None] = mapped_column(Text)

    # Senaste statistik-snapshot
    views: Mapped[int] = mapped_column(Integer, default=0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)

    # Scoring (skrivs av pipeline-steg 2)
    scores: Mapped[dict | None] = mapped_column(JSON)            # {velocity:{raw,norm,explain}, ...}
    total_score: Mapped[float | None] = mapped_column(Float, index=True)

    # Källa
    source_confidence: Mapped[str | None] = mapped_column(String(16))  # 'high' | 'low'
    source_flags: Mapped[dict | None] = mapped_column(JSON)            # {watermark:false, credit_to:true, ...}

    # Kontakt
    contact_email: Mapped[str | None] = mapped_column(String)
    contact_instagram: Mapped[str | None] = mapped_column(String)
    contact_other: Mapped[str | None] = mapped_column(Text)
    contact_found: Mapped[bool] = mapped_column(Boolean, default=False)

    # Vinkel-förslag
    suggested_angle: Mapped[str | None] = mapped_column(Text)

    # Metadata
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    poll_history: Mapped[list | None] = mapped_column(JSON)  # [{at, views, likes, comments}]

    # Pipeline-flags
    filtered_out: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    filter_reason: Mapped[str | None] = mapped_column(Text)

    decisions: Mapped[list["Decision"]] = relationship(back_populates="candidate", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("video_id", name="uq_candidate_video"),
        Index("ix_candidate_first_seen_filtered", "first_seen_at", "filtered_out"),
    )


class DailyDelivery(Base):
    """En dags 10 utvalda kandidater. Idempotent: kör om en dag → samma rad."""
    __tablename__ = "daily_deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    date: Mapped[str] = mapped_column(String(10), unique=True, index=True)  # YYYY-MM-DD
    candidate_ids: Mapped[list] = mapped_column(JSON)                       # ordnade
    run_id: Mapped[str | None] = mapped_column(String, ForeignKey("run_logs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    candidate_count: Mapped[int] = mapped_column(Integer, default=0)
    quality_floor: Mapped[float] = mapped_column(Float)


class Decision(Base):
    """Redaktionsbeslut: ja/nej/snooze. Ångringsbar genom ny rad."""
    __tablename__ = "decisions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    candidate_id: Mapped[str] = mapped_column(String, ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    verdict: Mapped[str] = mapped_column(String(16))  # 'yes' | 'no' | 'snooze'
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    decided_by: Mapped[str | None] = mapped_column(String)  # username v1: 'redaktion'
    note: Mapped[str | None] = mapped_column(Text)

    candidate: Mapped["Candidate"] = relationship(back_populates="decisions")


class PublishedOutcome(Base):
    """Faktisk WLA-prestanda på publicerad video, kopplad till kandidat.
    7d och 30d-mätningar lagras separat (samma video kan ha flera rader)."""
    __tablename__ = "published_outcomes"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    candidate_id: Mapped[str | None] = mapped_column(String, ForeignKey("candidates.id"), index=True)
    wla_video_id: Mapped[str] = mapped_column(String, index=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    measurement_window_days: Mapped[int] = mapped_column(Integer)  # 7 eller 30
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    views: Mapped[int] = mapped_column(Integer)
    avg_view_duration_sec: Mapped[float | None] = mapped_column(Float)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0)
    estimated_minutes_watched: Mapped[int] = mapped_column(Integer, default=0)
    # Klass: 'hit' | 'miss' | 'expected' efter jämförelse med agentens score
    outcome_class: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (UniqueConstraint("wla_video_id", "measurement_window_days", name="uq_outcome_window"),)


class ScoreWeightsVersion(Base):
    """Versionerad vikt-profil. Skapas vid varje justering (manuell el. heuristisk)."""
    __tablename__ = "score_weights_versions"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    profile: Mapped[str] = mapped_column(String(16))  # 'shorts' | 'longform'
    weights: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    based_on_datapoints: Mapped[int | None] = mapped_column(Integer)


class RunLog(Base):
    """En körning (scout, maturity-polling, sheet-sync). För idempotens + felsökning."""
    __tablename__ = "run_logs"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # 'scout' | 'maturity' | 'sheet_sync' | ...
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # 'running' | 'ok' | 'failed'
    error: Mapped[str | None] = mapped_column(Text)
    stats: Mapped[dict | None] = mapped_column(JSON)
    # Kostnadsspår
    youtube_quota_used: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    llm_tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


class LLMCache(Base):
    """Cachad LLM-output per (video_id, prompt_hash). Aldrig betala dubbelt."""
    __tablename__ = "llm_cache"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    video_id: Mapped[str] = mapped_column(String, index=True)
    prompt_hash: Mapped[str] = mapped_column(String, index=True)
    model: Mapped[str] = mapped_column(String)
    response: Mapped[dict] = mapped_column(JSON)
    tokens_in: Mapped[int] = mapped_column(Integer)
    tokens_out: Mapped[int] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Float)
    cached_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    __table_args__ = (UniqueConstraint("video_id", "prompt_hash", name="uq_llm_cache"),)


class SheetSync(Base):
    """Senaste lyckade läsning av WLA Publicerade-sheet."""
    __tablename__ = "sheet_sync"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    rows_read: Mapped[int] = mapped_column(Integer)
    rows_matched: Mapped[int] = mapped_column(Integer)
    rows_needs_manual: Mapped[int] = mapped_column(Integer)


class WinnerProfile(Base):
    """Vinnarprofilen byggd från WLA Analytics. Versionerad per körning."""
    __tablename__ = "winner_profiles"
    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    keywords: Mapped[list] = mapped_column(JSON)  # [{ngram, weight, n}]
    typical_duration_sec: Mapped[int | None] = mapped_column(Integer)
    tone_signals: Mapped[dict | None] = mapped_column(JSON)
    based_on_videos: Mapped[int] = mapped_column(Integer)
