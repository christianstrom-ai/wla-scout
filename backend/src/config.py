"""Centraliserad config — env-vars + defaults. Importera `settings` överallt."""
from __future__ import annotations
import json
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # YouTube
    youtube_api_key: str = ""
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_token_path: str = ".secrets/google_token.json"
    wla_channel_id: str = ""

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model_broad: str = "claude-haiku-4-5-20251001"
    anthropic_model_escalate: str = "claude-sonnet-4-6"

    # Sheets
    wla_published_sheet_id: str = "1taCKPB-H5K5fXRbQDrghDlwptzZ_BQglCbn6XM8KqbQ"
    wla_published_sheet_tab: str = "Sheet1"

    # Trösklar
    daily_llm_budget_usd: float = 3.30
    max_subscribers: int = 50_000
    candidate_pool_size: int = 200
    llm_topn: int = 40
    quality_floor: float = 55.0
    low_yield_alert_days: int = 3
    min_feedback_datapoints: int = 20
    maturity_poll_hours: str = "6,24,48"
    dedup_window_days: int = 30
    retention_days_rejected: int = 90
    retention_days_unpublished_yes: int = 90

    # Tid/cron
    tz: str = "Europe/Stockholm"
    schedule_cron_scout: str = "0 6 * * *"
    schedule_cron_maturity: str = "0 */6 * * *"
    schedule_cron_sheet_sync: str = "0 2 * * *"
    schedule_cron_daily_status: str = "30 6 * * *"  # 06:30 — efter scout-jobbet 06:00

    # Auth
    dashboard_auth_password: str = ""
    dashboard_auth_secret: str = ""

    # Alerts
    alert_webhook: str = ""

    # Claude-scouten (Storys-fliken). Morgonkörningen lägger feed-filer och
    # bilder i ett datarepo på GitHub; appen hämtar dem och skriver tillbaka
    # redaktionens beslut dit. Utan repo/token är synken avstängd.
    scout_data_repo: str = ""          # "ägare/repo", t.ex. "tomlannge/wla-scout-data"
    scout_data_branch: str = "main"
    scout_github_token: str = ""       # fine-grained token: Contents read/write på datarepot
    scout_data_dir: str = ""           # alternativ för lokal utveckling: läs/skriv en mapp i stället
    schedule_cron_scout_sync: str = "*/15 * * * *"

    # DB
    database_url: str = "sqlite:///./wla.db"

    # Scoring-vikter — JSON-strängar parsade vid use
    weights_shorts_json: str = '{"velocity":0.30,"engagement":0.20,"overperformance":0.15,"profile_match":0.10,"story":0.10,"reachability":0.10,"originality":0.05}'
    weights_longform_json: str = '{"velocity":0.10,"engagement":0.15,"overperformance":0.10,"profile_match":0.25,"story":0.25,"reachability":0.10,"originality":0.05}'

    @property
    def maturity_poll_hours_list(self) -> list[int]:
        return [int(h.strip()) for h in self.maturity_poll_hours.split(",") if h.strip()]

    @property
    def weights_shorts(self) -> dict[str, float]:
        return json.loads(self.weights_shorts_json)

    @property
    def weights_longform(self) -> dict[str, float]:
        return json.loads(self.weights_longform_json)


settings = Settings()
