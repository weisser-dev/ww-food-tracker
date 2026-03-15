#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPTS_DIR="$ROOT_DIR/skills/ww-screenshot-food-tracker/scripts"

RAW_INPUT="${1:-/tmp/foods_raw.json}"
TODAY="$(date +%F)"
RESOLVED_OUT="${2:-/tmp/foods_resolved_run_skill_${TODAY}.json}"
TRACK_OUT="/tmp/foods_tracked_run_skill_${TODAY}.json"

if [[ ! -f "$RAW_INPUT" ]]; then
  echo "Fehler: Input-Datei nicht gefunden: $RAW_INPUT" >&2
  echo "Nutzung: ./run_skill.sh /pfad/zu/foods_raw.json [resolved_output.json]" >&2
  exit 1
fi

WW_API_INSECURE_OVERRIDE="${WW_API_INSECURE-__UNSET__}"
if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi
if [[ "$WW_API_INSECURE_OVERRIDE" != "__UNSET__" ]]; then
  export WW_API_INSECURE="$WW_API_INSECURE_OVERRIDE"
fi

WW_TLD_VAL="${WW_TLD:-de}"
export WW_API_BASE_URL="${WW_API_BASE_URL:-https://cmx.weightwatchers.${WW_TLD_VAL}}"
WW_API_INSECURE_VAL="${WW_API_INSECURE:-false}"
WW_API_INSECURE_VAL_LC="$(printf '%s' "$WW_API_INSECURE_VAL" | tr '[:upper:]' '[:lower:]')"
AUTH_INSECURE_FLAG=""
if [[ "$WW_API_INSECURE_VAL_LC" == "true" || "$WW_API_INSECURE_VAL" == "1" || "$WW_API_INSECURE_VAL_LC" == "yes" ]]; then
  AUTH_INSECURE_FLAG="--insecure"
fi

echo "==> Token wird abgerufen..."
if ! WW_TOKEN="$(python3 "$SCRIPTS_DIR/ww_auth_jwt.py" --raw --tld "$WW_TLD_VAL" $AUTH_INSECURE_FLAG)"; then
  echo "Fehler: Token konnte nicht abgerufen werden." >&2
  exit 1
fi
export WW_API_TOKEN="$WW_TOKEN"
echo "Token erfolgreich abgerufen."

echo
echo "Folgende Essen wurden erkannt:"
python3 - "$RAW_INPUT" <<'PY'
import json, sys

path = sys.argv[1]
data = json.load(open(path, "r", encoding="utf-8"))

time_map = {
    "MORNING": "Morgens",
    "MIDDAY": "Mittags",
    "EVENING": "Abends",
    "ANYTIME": "Jederzeit",
}

def fmt_num(v):
    try:
        f = float(v)
        if f.is_integer():
            return str(int(f))
        return str(f).rstrip("0").rstrip(".")
    except Exception:
        return str(v)

for row in data:
    name = str(row.get("name", "")).strip()
    size = fmt_num(row.get("portionSize", row.get("quantity", 1)))
    unit = str(row.get("unit", "")).strip()
    mt = str(row.get("mealTime", "ANYTIME")).upper()
    zeit = time_map.get(mt, mt.title())
    print(f"- {name} - {size} {unit} - {zeit}")
PY

echo
echo "==> Essen werden per API aufgelöst..."
python3 "$SCRIPTS_DIR/ww_resolve_foods.py" \
  --input "$RAW_INPUT" \
  --output "$RESOLVED_OUT"

# Optional fallback retry: re-generate a minimal foods_raw.json for items that were not resolved
# and track those as a second pass.
# Enable via: WW_FALLBACK_RETRY=true
FALLBACK_RETRY_VAL="${WW_FALLBACK_RETRY:-false}"
FALLBACK_RETRY_VAL_LC="$(printf '%s' "$FALLBACK_RETRY_VAL" | tr '[:upper:]' '[:lower:]')"
if [[ "$FALLBACK_RETRY_VAL_LC" == "true" || "$FALLBACK_RETRY_VAL" == "1" || "$FALLBACK_RETRY_VAL_LC" == "yes" ]]; then
  FALLBACK_RAW="/tmp/foods_raw_fallback_${TODAY}.json"
  FALLBACK_RESOLVED="/tmp/foods_resolved_fallback_run_skill_${TODAY}.json"
  FALLBACK_TRACK="/tmp/foods_tracked_fallback_run_skill_${TODAY}.json"

  # Default grams per slice for cheese etc. (can be overridden)
  GRAMS_PER_SLICE="${WW_GRAMS_PER_SLICE:-25}"

  echo
  echo "==> Fallback-Retry: Nicht aufgeloeste Eintraege werden mit mehreren Kandidaten (Komma/Slash/Mapping + Scheibe->g) erneut versucht..."
  python3 "$SCRIPTS_DIR/ww_fallback_multi.py" \
    --resolved "$RESOLVED_OUT" \
    --scripts-dir "$SCRIPTS_DIR" \
    --grams-per-slice "$GRAMS_PER_SLICE" \
    --out-prefix "/tmp/ww_fallback_${TODAY}" \
    > "/tmp/ww_fallback_summary_${TODAY}.json" || true
  echo "Fallback Summary: /tmp/ww_fallback_summary_${TODAY}.json"
fi

echo
echo "==> Essen werden getrackt (inkl. Duplikat-Check)..."
python3 "$SCRIPTS_DIR/ww_track_resolved.py" \
  --input "$RESOLVED_OUT" \
  --verify-summary > "$TRACK_OUT"

python3 - "$RESOLVED_OUT" "$TRACK_OUT" <<'PY'
import json, sys
from collections import Counter

resolved_path, track_path = sys.argv[1], sys.argv[2]
resolved = json.load(open(resolved_path, "r", encoding="utf-8"))
track = json.load(open(track_path, "r", encoding="utf-8"))

items = resolved["items"] if isinstance(resolved, dict) else resolved
actions = track.get("actions", [])

def norm_tod(v):
    s = str(v or "ANYTIME").strip().upper()
    if s == "BREAKFAST":
        s = "MORNING"
    elif s == "LUNCH":
        s = "MIDDAY"
    elif s == "DINNER":
        s = "EVENING"
    elif s == "SNACK":
        s = "ANYTIME"
    return s

def sig_from_resolved(row):
    r = row.get("resolved") or {}
    pid = r.get("portionId")
    return (
        str(r.get("id") or ""),
        str(r.get("versionId") or ""),
        "" if pid is None else str(pid),
        round(float(row.get("portionSize", 1)), 4),
        norm_tod(row.get("mealTime")),
    )

def sig_from_req(req):
    pid = req.get("portionId")
    return (
        str(req.get("_id") or ""),
        str(req.get("versionId") or ""),
        "" if pid is None else str(pid),
        round(float(req.get("portionSize", 1)), 4),
        norm_tod(req.get("timeOfDay")),
    )

def fmt_num(v):
    f = float(v)
    if f.is_integer():
        return str(int(f))
    return str(f).rstrip("0").rstrip(".")

def fmt_line(row):
    time_map = {
        "MORNING": "Morgens",
        "MIDDAY": "Mittags",
        "EVENING": "Abends",
        "ANYTIME": "Jederzeit",
    }
    name = str(row.get("name", "")).strip()
    size = fmt_num(row.get("portionSize", 1))
    unit = str(row.get("unit", "")).strip()
    zeit = time_map.get(norm_tod(row.get("mealTime")), str(row.get("mealTime", "ANYTIME")))
    return f"{name} - {size} {unit} - {zeit}"

skipped_counter = Counter()
for a in actions:
    for s in a.get("skipped", []) or []:
        req = (s or {}).get("item")
        if isinstance(req, dict):
            skipped_counter[sig_from_req(req)] += 1

resolved_ok = [r for r in items if isinstance(r, dict) and r.get("status") == "resolved" and isinstance(r.get("resolved"), dict)]

tracked_rows = []
existing_rows = []
for row in resolved_ok:
    sig = sig_from_resolved(row)
    if skipped_counter[sig] > 0:
        skipped_counter[sig] -= 1
        existing_rows.append(row)
    else:
        tracked_rows.append(row)

print()
print("Folgende Essen wurden getracked:")
if tracked_rows:
    for row in tracked_rows:
        print(f"- {fmt_line(row)}")
else:
    print("- Keine neuen Eintraege")

print()
print("Folgende Essen haben schon existiert:")
if existing_rows:
    for row in existing_rows:
        print(f"- {fmt_line(row)}")
else:
    print("- Keine")
PY

echo
echo "Fertig."
echo "Resolved JSON: $RESOLVED_OUT"
echo "Track Output:  $TRACK_OUT"
