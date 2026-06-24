# WLA Scout — YouTube klippbedömare

Scoutar engelskspråkiga djur-/feel-good-klipp från små YouTube-kreatörer,
poängsätter dem mot WLA:s vinnarprofil, presenterar upp till 10 kandidater
över ett kvalitetsgolv varje morgon. Redaktionen säger ja/nej. Agenten lär
sig av besluten + faktiskt utfall.

## Stack
- Backend: FastAPI + APScheduler (Python 3.12)
- DB: PostgreSQL (SQLite för dev)
- Frontend: React + Tailwind (Vite)
- LLM: Anthropic (Haiku 4.5 + Sonnet 4.6)
- Deploy: container, redo för Render

## Snabbstart (lokal dev)
```bash
cp .env.example .env  # fyll i nycklar
cd backend && pip install -r requirements.txt
alembic upgrade head
uvicorn src.main:app --reload --port 8000

cd frontend && npm install && npm run dev
```

Dashboarden hamnar på http://localhost:5173, backend på :8000.

## Driftläge
- `python -m src.cli scout` — kör scouting-pipeline manuellt (idempotent)
- `python -m src.cli polla-mognad` — pollar pågående kandidater (cron-jobb)
- `python -m src.cli sync-sheet` — läs WLA Publicerade-sheet och matcha
- `python -m src.cli dry-run` — gå genom pipelinen utan att skriva DB
  eller anropa LLM (kostnadsfritt)

## Konfiguration
Allt via env. Se `.env.example` för fullständig lista.

## Byggstatus
Se commits — byggordningen 1-13 från specen följs.
