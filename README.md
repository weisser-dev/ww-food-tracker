# WW Screenshot Food Tracker

Ein kleines Projekt, um WeightWatchers-Essen aus Screenshots zu erkennen, in ein JSON-Zwischenformat zu konvertieren und per WW-API automatisiert zu tracken.

A small project to extract WeightWatchers meal entries from screenshots, convert them into a JSON intermediate format, and track them via the WW API.

## Deutsch

### Zweck

Dieses Projekt verarbeitet WW-Listen-Screenshots (z. B. `Mittags`, `Fruehstueck`) und ueberfuehrt die sichtbaren Lebensmittel in API-Requests:

1. Screenshot analysieren (OCR/visuell durch Agent)
2. `foods_raw.json` erzeugen
3. JWT holen (via `ww_auth_jwt.py`)
4. Lebensmittel per WW API aufloesen
5. Lebensmittel tracken
6. Rueckmeldung geben (neu getrackt / bereits vorhanden / fehlgeschlagen)

### Wichtige Dateien (und ob sie committed werden sollen)

- `README.md` -> committen (Projekt-Dokumentation)
- `AGENT.md` -> committen (Ausfuehrungsregeln fuer Agents)
- `.opencode/command/track_food.md` -> committen (OpenCode-Kommando-Workflow)
- `run_skill.sh` -> committen (Wrapper fuer Resolve + Track + Ausgabe)
- `.env.example` -> committen (Beispiel-Konfiguration ohne Secrets)
- `.env` -> **nicht** committen (enthaelt Zugangsdaten/Token-Quellen)
- `skills/ww-screenshot-food-tracker/SKILL.md` -> committen (Skill-Definition)
- `skills/ww-screenshot-food-tracker/scripts/*.py` -> committen (API/Auth/Resolve/Track Logik)
- `skills/ww-screenshot-food-tracker/references/*` -> optional committen (API-Referenz/Reverse-Engineering)
- Temporaere JSON-Dateien in `/tmp` (z. B. `/tmp/foods_raw.json`, `/tmp/foods_resolved_*.json`, `/tmp/foods_tracked_*.json`) -> **nicht** committen, nach erfolgreichem Upload loeschen

### Projektstruktur

- `run_skill.sh`: Haupt-Wrapper. Laedt `.env`, holt Token, fuehrt Resolve und Track aus, zeigt Zusammenfassung.
- `skills/ww-screenshot-food-tracker/SKILL.md`: Fachliche Anleitung (Screenshot -> JSON -> API -> Tracking).
- `skills/ww-screenshot-food-tracker/scripts/ww_auth_jwt.py`: JWT-Abruf aus WW Login.
- `skills/ww-screenshot-food-tracker/scripts/ww_resolve_foods.py`: Sucht/normalisiert Lebensmittel ueber WW-API.
- `skills/ww-screenshot-food-tracker/scripts/ww_track_resolved.py`: Trackt aufgeloeste Lebensmittel und prueft Duplikate.
- `skills/ww-screenshot-food-tracker/scripts/env_loader.py`: Gemeinsames Laden/Verarbeiten von Umgebungsvariablen.

### Voraussetzungen

- `python3`
- gueltige WW Zugangsdaten in `.env`
- Netzwerkzugriff zur WW API

Beispiel `.env` (lokal, **nicht committen**):

```env
WW_USERNAME=you@example.com
WW_PASSWORD=secret
WW_TLD=de
WW_API_BASE_URL=https://cmx.weightwatchers.de
WW_API_INSECURE=false
WW_API_TIMEOUT=20
WW_RESOLVE_ALLOW_SERVER_DEFAULT=true
```

### Input-JSON Format (`foods_raw.json`)

Der Agent erzeugt eine JSON-Liste (Array). Jedes Element beschreibt genau ein Lebensmittel aus dem Screenshot.

Pflichtfelder:

- `name` (string): Lebensmittelname wie im Screenshot sichtbar (moeglichst komplett)
- `portionSize` (number): Menge als Zahl (Brueche umrechnen, z. B. `1 1/2` -> `1.5`)
- `mealTime` (string): `MORNING`, `MIDDAY`, `EVENING`, `ANYTIME`
- `date` (string): Datum im Format `YYYY-MM-DD`

Empfohlene Felder:

- `unit` (string): z. B. `g`, `Stueck`, `EL`, `Packung(en)`, `Portion(en)`
- `portionId` (string|number, optional): Falls bekannt, explizite WW-Portions-ID

Beispiel:

```json
[
  {
    "name": "Raeucherlachs",
    "portionSize": 30,
    "unit": "g",
    "mealTime": "MIDDAY",
    "date": "2026-02-21"
  },
  {
    "name": "Eier, Huehnerei/Ei, ganz",
    "portionSize": 2,
    "unit": "Stueck",
    "mealTime": "MIDDAY",
    "date": "2026-02-21"
  }
]
```

### Nutzung (manuell)

1. Screenshot lesen und `foods_raw.json` erstellen (z. B. `/tmp/foods_raw.json`).
2. Optional unsichere TLS-Pruefung aktivieren (nur wenn noetig): `WW_API_INSECURE=true`
3. Wrapper starten:

```bash
cd /Users/erikweisser/Documents/New\ project
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

### Erwartete Rueckmeldung nach erfolgreichem Lauf

Der ausfuehrende Agent sollte im Messenger antworten mit:

- Datum und Mahlzeit (`YYYY-MM-DD`, z. B. `MIDDAY`)
- Neu getrackte Lebensmittel (Name, Menge, Einheit, Mahlzeit)
- Bereits vorhandene Eintraege (Duplikate)
- Nicht aufgeloeste / fehlgeschlagene Eintraege
- Annahmen/Unsicherheiten (z. B. abgeschnittene Namen)
- Hinweis, dass temporaere JSON-Dateien nach Erfolg geloescht wurden

### Cleanup-Regel

Nach **erfolgreichem** Upload (Tracking ohne kritischen Fehler) sollten temporaere JSON-Dateien geloescht werden:

- Input JSON (`foods_raw.json`)
- Resolve-Output JSON
- Track-Output JSON

Bei Fehlern duerfen die Dateien zur Diagnose erhalten bleiben.

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
- `skills/ww-screenshot-food-tracker/scripts/*.py` -> commit (auth/resolve/track logic)
- `skills/ww-screenshot-food-tracker/references/*` -> optional commit (API notes/reference dumps)
- Temporary JSON files in `/tmp` -> **do not commit**, delete after successful upload

### Raw JSON Format (`foods_raw.json`)

The agent must create a JSON array. Each item represents one visible food entry from the screenshot.

Required fields:

- `name` (string)
- `portionSize` (number)
- `mealTime` (string: `MORNING`, `MIDDAY`, `EVENING`, `ANYTIME`)
- `date` (string: `YYYY-MM-DD`)

Recommended fields:

- `unit` (string)
- `portionId` (string|number, optional)

Example:

```json
[
  {
    "name": "Smoked salmon",
    "portionSize": 30,
    "unit": "g",
    "mealTime": "MIDDAY",
    "date": "2026-02-21"
  }
]
```

### Usage

```bash
cd /Users/erikweisser/Documents/New\ project
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

### Agent Response Requirements

After a successful run, the agent should reply in the messenger/channel where the command was received with:

- Date and meal time
- Newly tracked foods
- Already-existing entries (duplicates)
- Failed/unresolved foods
- Assumptions/uncertainties
- Confirmation that temporary JSON files were deleted

### Cleanup Rule

After a successful upload, remove temporary JSON files (`raw`, `resolved`, `tracked`). If the run fails, keep them for debugging.
