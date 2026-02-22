# track_food

Nutze dieses Kommando, wenn ein Nutzer einen WW-Screenshot sendet und Essen automatisch getrackt werden soll.

## Ziel

Screenshot -> `foods_raw.json` -> `run_skill.sh` -> WW Upload -> Rueckmeldung im gleichen Messenger -> Cleanup der JSON-Dateien (nur bei Erfolg).

## Eingabe

- Ein Screenshot (typischerweise WW-Liste mit Mahlzeit-Header und Lebensmitteln)
- Optional vom Nutzer genannte Hinweise (Datum, Mahlzeit, Mengenkorrekturen)

## Erforderliches JSON-Format (`foods_raw.json`)

Speichere ein JSON-Array, z. B. unter `/tmp/foods_raw.json`.

Pflichtfelder pro Eintrag:

- `name` (string)
- `portionSize` (number)
- `mealTime` (`MORNING` | `MIDDAY` | `EVENING` | `ANYTIME`)
- `date` (`YYYY-MM-DD`)

Empfohlene Felder:

- `unit` (string)
- `portionId` (optional)

Beispiel:

```json
[
  {
    "name": "Eier, Huehnerei/Ei, ganz",
    "portionSize": 2,
    "unit": "Stueck",
    "mealTime": "MIDDAY",
    "date": "2026-02-21"
  }
]
```

## Ausfuehrungsschritte (verbindlich)

1. Screenshot lesen und Lebensmittel + Mengen extrahieren.
2. Mahlzeit-Header auf `mealTime` mappen (`Fruehstueck`/`Mittags`/`Abendessen`/`Snack`).
3. Mengen normalisieren (z. B. `1 1/2` -> `1.5`, `1/4` -> `0.25`).
4. `/tmp/foods_raw.json` schreiben.
5. Im Projektordner `/Users/erikweisser/Documents/New project` `run_skill.sh` starten:

```bash
./run_skill.sh /tmp/foods_raw.json
```

6. Falls der Nutzer oder die Umgebung es verlangt (TLS-Probleme), mit insecure ausfuehren:

```bash
WW_API_INSECURE=true ./run_skill.sh /tmp/foods_raw.json
```

7. Ausgabe lesen und unterscheiden:
   - neu getrackt
   - bereits vorhanden
   - Fehler / nicht aufgeloest
8. Erfolgreiche Runs: alle erzeugten JSON-Dateien loeschen (`raw`, `resolved`, `tracked`).
9. Fehlerhafte Runs: JSON-Dateien behalten, damit Debugging moeglich bleibt.
10. Antwort im gleichen Messenger/Thread senden.

## Messenger-Antwort (Pflichtformat)

Die Antwort soll klar benennen, welches Essen wann wie getrackt wurde.

Pflichtinhalte:

- Datum (`YYYY-MM-DD`)
- Mahlzeit (`MORNING|MIDDAY|EVENING|ANYTIME`)
- `Neu getrackt:` Liste mit `Name - Menge Einheit - Mahlzeit`
- `Schon vorhanden:` Liste mit Duplikaten
- `Nicht erfolgreich:` Liste mit Fehlern/nicht aufgeloesten Eintraegen
- `Annahmen:` Unsicherheiten (z. B. abgeschnittene Eintragsnamen)
- `Cleanup:` bestaetigen, dass temporaere JSON-Dateien nach Erfolg geloescht wurden

Beispiel-Ausgabe (Messenger):

- Datum: 2026-02-21
- Mahlzeit: MIDDAY
- Neu getrackt: Raeucherlachs (30 g), Eier (2 Stueck)
- Schon vorhanden: Honig-Senf-Sauce (1.5 EL)
- Nicht erfolgreich: keine
- Annahmen: keine
- Cleanup: `/tmp/foods_raw.json`, resolve- und track-JSON wurden geloescht

## Wichtige Regeln

- Keine WW Zugangsdaten / Tokens im Messenger posten.
- Punkte-Spalte aus dem Screenshot nicht fuer Payload verwenden.
- Bei abgeschnittenen Namen konservativ erfassen und Unsicherheit melden.
- Temp-JSON nur nach erfolgreichem Upload loeschen.
