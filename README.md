# WW Screenshot Food Tracker

A small AI-agent project to extract WeightWatchers meal entries from screenshots, convert them into a JSON intermediate format, and track them via the WW API through Telegram.

Ein kleines AI-Agent-Projekt, um WeightWatchers-Essen aus Screenshots zu erkennen, in ein JSON-Zwischenformat zu konvertieren und per WW-API über Telegram automatisiert zu tracken.

This project was developed end-to-end with Codex 5.3 and OpenClaw (Clawd) to automate screenshot-based food tracking in Telegram.

Background: Erik’s girlfriend Mona kindly often prepares meals and already tracks them on her side. The goal of this project is to fully automate this process for Erik via a screenshot-based workflow.

## English

### Purpose

This project processes WW list screenshots (for example lunch/breakfast screens), converts visible food entries into a normalized JSON payload, and tracks them through WeightWatchers APIs.

Workflow:

1. Analyze screenshot (OCR/vision by agent)
2. Create `foods_raw.json`
3. Acquire JWT (`ww_auth_jwt.py`)
4. Resolve foods via WW API
5. Track foods
6. Return a user-facing summary (newly tracked / already existed / failed)

### Important Files (and what should be committed)

- `README.md` -> commit (project docs)
- `AGENT.md` -> commit (execution rules for agents)
- `.opencode/command/track_food.md` -> commit (OpenCode command workflow)
- `run_skill.sh` -> commit (wrapper for resolve + track + summary output)
- `.env.example` -> commit (example configuration without secrets)
- `.env` -> **do not commit** (contains credentials)
- `skills/ww-screenshot-food-tracker/SKILL.md` -> commit (skill instructions)
- `skills/ww-screenshot-food-tracker/scripts/*.py` -> commit (auth/resolve/track/summary logic)
- `skills/ww-screenshot-food-tracker/scripts/ww_daily_summary.py` -> commit (daily protein + meal summary)
- `skills/ww-screenshot-food-tracker/references/*` -> optional commit (API notes/reference dumps)
- Temporary JSON files in `/tmp` -> **do not commit**, delete after successful upload

### Daily Summary script

`ww_daily_summary.py` returns:

- current WW weight (`/api/v2/cmx/members/~`)
- daily tracked meals/macros (`/api/v4/.../my-day-summary/{date}`)
- protein goal (`protein_factor * bodyweight`, default `1.5`)
- current protein intake and deficit
- 13:00 checkpoint (`proteinMinimumBy13`, based on `3/8` of target)
- calories consumed/target/remaining
- 14-day protein average and trend
- short meal feedback + recommendations
- pre-rendered reminder messages (`messages.13`, `messages.19`)

Example:

```bash
WW_API_INSECURE=true \
WW_API_TOKEN="$(python3 skills/ww-screenshot-food-tracker/scripts/ww_auth_jwt.py --raw --tld de --insecure)" \
WW_API_BASE_URL=https://cmx.weightwatchers.de \
python3 skills/ww-screenshot-food-tracker/scripts/ww_daily_summary.py --json
```

### Raw JSON Format (`foods_raw.json`)

Required fields:

- `name` (string)
- `portionSize` (number)
- `mealTime` (`MORNING`, `MIDDAY`, `EVENING`, `ANYTIME`)
- `date` (`YYYY-MM-DD`)

Recommended fields:

- `unit` (string)
- `portionId` (string|number, optional)

### Usage

```bash
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

### Cleanup Rule

After a successful upload, remove temporary JSON files (`raw`, `resolved`, `tracked`). If the run fails, keep them for debugging.

---

## Deutsch

### Zweck

Dieses Projekt verarbeitet WW-Listen-Screenshots (z. B. Mittag/Frühstück), überführt sichtbare Lebensmittel in ein normalisiertes JSON und trackt sie über die WW-API.

Ablauf:

1. Screenshot analysieren
2. `foods_raw.json` erzeugen
3. JWT holen (`ww_auth_jwt.py`)
4. Lebensmittel per WW-API auflösen
5. Lebensmittel tracken
6. Rückmeldung geben (neu / bereits vorhanden / fehlgeschlagen)

### Wichtige Dateien (und ob sie committed werden sollen)

- `README.md` -> committen
- `AGENT.md` -> committen
- `.opencode/command/track_food.md` -> committen
- `run_skill.sh` -> committen
- `.env.example` -> committen
- `.env` -> **nicht** committen
- `skills/ww-screenshot-food-tracker/SKILL.md` -> committen
- `skills/ww-screenshot-food-tracker/scripts/*.py` -> committen
- `skills/ww-screenshot-food-tracker/scripts/ww_daily_summary.py` -> committen
- `skills/ww-screenshot-food-tracker/references/*` -> optional committen
- Temporäre `/tmp/*.json` Dateien -> **nicht** committen

### Daily-Summary Script

`ww_daily_summary.py` liefert:

- aktuelles WW-Gewicht (`/api/v2/cmx/members/~`)
- heutige getrackte Mahlzeiten/Makros (`/api/v4/.../my-day-summary/{date}`)
- Proteinziel (`protein_factor * Körpergewicht`, Standard `1.5`)
- 13:00-Checkpoint (`proteinMinimumBy13` = `3/8` vom Ziel)
- Kalorien gegessen/Ziel/Rest
- aktuelles Protein + Defizit
- 14-Tage-Protein-Schnitt
- kurzes Mahlzeiten-Feedback + Empfehlungen
- fertig formulierte Reminder (`messages.13`, `messages.19`)

Beispiel:

```bash
WW_API_INSECURE=true \
WW_API_TOKEN="$(python3 skills/ww-screenshot-food-tracker/scripts/ww_auth_jwt.py --raw --tld de --insecure)" \
WW_API_BASE_URL=https://cmx.weightwatchers.de \
python3 skills/ww-screenshot-food-tracker/scripts/ww_daily_summary.py --json
```

### Nutzung

```bash
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

### Cleanup-Regel

Nach erfolgreichem Upload temporäre JSON-Dateien (`raw`, `resolved`, `tracked`) löschen. Bei Fehlern zur Diagnose behalten.
