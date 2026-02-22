# AGENT.md

Diese Datei definiert, wie ein Agent dieses Projekt fuer Screenshot-basiertes WW-Tracking ausfuehren soll.

## Ziel

Ein vom Nutzer gesendeter Screenshot soll in WW-Eintraege umgesetzt werden. Der Agent muss:

1. Screenshot analysieren
2. `foods_raw.json` korrekt erzeugen
3. `run_skill.sh` ausfuehren (bei Bedarf mit `WW_API_INSECURE=true`)
4. Ergebnis auswerten
5. Temporaere JSON-Dateien nach Erfolg loeschen
6. Im gleichen Messenger/Thread zurueckmelden, was wann wie getrackt wurde

## Verbindlicher Ablauf

### 1) Screenshot analysieren

- Meal-Header erkennen und auf `mealTime` mappen:
  - `Fruehstueck` -> `MORNING`
  - `Mittag` / `Mittags` -> `MIDDAY`
  - `Abendessen` -> `EVENING`
  - `Snack` -> `ANYTIME`
- Pro sichtbarem Lebensmittel einen JSON-Eintrag erstellen.
- Mengen aus grauer Zeile lesen und normalisieren:
  - `1 Stueck` -> `portionSize: 1`, `unit: "Stueck"`
  - `30 g` -> `portionSize: 30`, `unit: "g"`
  - `1 1/2 EL` -> `portionSize: 1.5`, `unit: "EL"`
  - `1/4 Packung(en)` -> `portionSize: 0.25`, `unit: "Packung(en)"`
- Punkte-Spalte ignorieren.
- Abgeschnittene Namen bestmoeglich uebernehmen und spaeter als Unsicherheit melden.

### 2) Raw JSON schreiben

- Speicherort bevorzugt: `/tmp/foods_raw.json`
- Format: JSON-Array von Objekten
- Pflichtfelder je Objekt:
  - `name` (string)
  - `portionSize` (number)
  - `mealTime` (`MORNING|MIDDAY|EVENING|ANYTIME`)
  - `date` (`YYYY-MM-DD`)
- Empfohlen:
  - `unit` (string)
  - `portionId` (optional)

Beispiel:

```json
[
  {
    "name": "Raeucherlachs",
    "portionSize": 30,
    "unit": "g",
    "mealTime": "MIDDAY",
    "date": "2026-02-21"
  }
]
```

### 3) Tracking ausfuehren

- Im Projektordner ausfuehren: `/Users/erikweisser/Documents/New project`
- Standardaufruf:

```bash
./run_skill.sh /tmp/foods_raw.json
```

- Wenn TLS/SSL im Zielsystem problematisch ist oder der Nutzer es vorgibt, mit insecure laufen:

```bash
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

Hinweise:

- `run_skill.sh` laedt `.env` automatisch.
- `run_skill.sh` erzeugt zusaetzlich Resolve-/Track-JSON-Dateien in `/tmp` und gibt deren Pfade aus.
- Das Skript meldet getrennt: neu getrackt vs. bereits vorhanden.

### 4) Ergebnis auswerten

Der Agent muss mindestens extrahieren:

- Datum
- Mahlzeit (`mealTime`)
- Neu getrackte Eintraege
- Bereits vorhandene Eintraege (Duplikate)
- Nicht aufgeloeste / Fehler
- Annahmen/Unsicherheiten

### 5) Cleanup (Pflicht nach Erfolg)

Wenn der Upload erfolgreich war (kein kritischer Fehler im Track-Schritt):

- `/tmp/foods_raw.json` loeschen
- Resolve-Output JSON loeschen (Pfad aus Skript-Ausgabe)
- Track-Output JSON loeschen (Pfad aus Skript-Ausgabe)

Wenn ein Fehler auftritt:

- JSON-Dateien **nicht** loeschen (Debugging)
- Fehlertext und vorhandene Artefakte in der Rueckmeldung nennen

### 6) Rueckmeldung im Messenger (Pflicht)

Antwort im gleichen Messenger/Thread, ueber den der Befehl kam. Inhalt:

- Was wurde verarbeitet (Screenshot/Mahlzeit/Datum)
- Welche Lebensmittel neu getrackt wurden (Name, Menge, Einheit, Mahlzeit)
- Welche bereits existierten
- Welche nicht aufloesbar/fehlgeschlagen waren
- Welche Annahmen getroffen wurden (z. B. abgeschnittene Namen, geschaetzte Mengen)
- Ob temporaere JSON-Dateien geloescht wurden

## Sicherheitsregeln

- `.env` niemals ausgeben oder committen.
- Zugangsdaten/Token niemals in Antworten posten.
- Nur kurze, notwendige Erfolg-/Fehlermeldungen zurueckgeben.

## Commit-Regeln

Commitbar:

- `README.md`
- `AGENT.md`
- `.opencode/command/track_food.md`
- `run_skill.sh`
- `skills/...` (Code, Skill, Referenzen nach Bedarf)
- `.env.example`

Nicht commitbar, verboten!!!:

- `.env`
- `/tmp/*.json` (raw/resolved/tracked Laufdateien)
- Token / Credentials / Logs mit Secrets
