# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**LegisTech Intelligence** — a Brazilian government procurement (licitação) matching platform. It collects public bids from PNCP (Plataforma Nacional de Contratações Públicas), scores them against company profiles using keywords and Google Gemini AI, and sends alerts via Telegram. All code and comments are in Portuguese.

## Running Scripts

Install dependencies first:
```bash
pip install -r requirements.txt
```

Each script requires environment variables set in `.env` (or GitHub Secrets in CI):
- `SUPABASE_URL`, `SUPABASE_KEY`
- `GEMINI_API_KEY`
- `TELEGRAM_TOKEN`

Run the pipeline manually in order:
```bash
# 1. Collect bids from PNCP
python scripts/coletor.py                          # defaults: PI state, today
python scripts/coletor.py --ufs PI MA --dias 7     # multiple states, N days back

# 2. Keyword-based matching
python scripts/motor_matching.py

# 3. AI semantic scoring (Gemini 2.5 Flash, saves only score >= 70)
python scripts/motor_ia.py

# 4. Send Telegram alerts (notificado=False, score >= 65)
python scripts/alertas_telegram.py                 # send alerts
python scripts/alertas_telegram.py --listen        # poll for button callbacks (5 min)
python scripts/alertas_telegram.py --simular       # dry run, no actual messages sent
```

## Architecture & Data Flow

The system is a linear pipeline: **Collect → Match → Score → Alert → Dashboard**

```
coletor.py  →  motor_matching.py  →  motor_ia.py  →  alertas_telegram.py
     ↓                 ↓                  ↓                    ↓
licitacoes_pncp     matches table     score_calculado      notificado=True
  (Supabase)       (Supabase)         resumo_ia             status updated
```

**Supabase tables:**
- `licitacoes_pncp` — raw collected bids (objeto, orgao_nome, uf, link_pncp, valor_estimado, data_publicacao)
- `perfis_empresa` — company profiles with `palavras_chave[]` and `uf_interesse[]`
- `matches` — join table with `score_calculado`, `resumo_ia`, `status`, `notificado`

**Matching logic:**
- `motor_matching.py`: accent-normalized keyword search, filters by `uf_interesse`, assigns initial score 100
- `motor_ia.py`: sends bid object + company keywords to Gemini, gets score 0–100 + one-line summary; discards matches scoring below 70

**Telegram alerts:**
- Sends rich HTML messages with inline buttons (Participar / Dispensar / Ver edital)
- Callbacks update `status` to `participando` or `descartado` in Supabase

**Frontend:**
- `index.html` — static SPA dashboard (navy + gold design), deployed to Vercel
- `vercel.json` rewrites `/` → `/legistech_dashboard.html` (historical artifact — main file is now `index.html`)

## CI/CD

GitHub Actions (`.github/workflows/rodar.yml`) runs the full pipeline automatically:
- **Schedule:** weekdays Mon–Fri at 10:00 UTC
- **Trigger:** also supports `workflow_dispatch` for manual runs
- **States covered:** PI and MA (`--ufs PI MA --dias 1`)
- All secrets are stored as GitHub repository secrets
