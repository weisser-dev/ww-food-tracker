#!/usr/bin/env python3
"""Fallback retry for unresolved WW items using multiple candidate names.

Input: resolved JSON from ww_resolve_foods.py (run_skill.sh first pass)
Behavior:
- For each item with status != 'resolved', generate candidate raw entries.
- Resolve candidates one-by-one until one resolves.
- Track resolved candidates (batched per meal+date when possible).

Design goals:
- Prefer generic, repeatable heuristics (comma/slash simplification) over hardcoded one-offs.
- Preserve certain brand/product names when the user wants exact naming (eatlean, (G)éramont).
- Convert slice-like units to grams (Scheibe(n) -> g) using grams_per_slice default.

This script calls existing project scripts via subprocess.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


def lc(s: str) -> str:
    return (s or "").strip().lower()


def norm_meal(s: str) -> str:
    s = (s or "ANYTIME").strip().upper()
    m = {"BREAKFAST": "MORNING", "LUNCH": "MIDDAY", "DINNER": "EVENING", "SNACK": "ANYTIME"}
    return m.get(s, s)


PRESERVE_SUBSTR = ["eatlean", "géramont", "geramont"]


def preserve_name(name: str) -> bool:
    low = lc(name)
    return any(k in low for k in PRESERVE_SUBSTR)


def simplify_candidates(name: str) -> List[str]:
    raw = (name or "").strip()
    low = lc(raw)

    out: List[str] = []

    # Tomato special-cases
    if "tomaten" in low and any(k in low for k in ["cocktail", "kirs", "cherry"]):
        out.append("Cherrytomaten")

    if preserve_name(raw):
        # Keep as-is, but still allow fallbacks for unit conversions etc.
        out.append(raw)
        return dedupe(out)

    # 1) before comma
    before_comma = raw.split(",", 1)[0].strip()
    if before_comma:
        out.append(before_comma)

    # 2) if has comma part, also try first token of the remainder split by '/'
    if "," in raw:
        remainder = raw.split(",", 1)[1].strip()
        if remainder:
            out.append(remainder.split("/", 1)[0].strip())

    # 3) before slash
    before_slash = raw.split("/", 1)[0].strip()
    if before_slash:
        out.append(before_slash)

    # 4) also try raw as last resort
    out.append(raw)

    # Some extra normalization: collapse multiple spaces
    out = [re.sub(r"\s+", " ", s).strip() for s in out if s and s.strip()]
    return dedupe(out)


def dedupe(seq: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for s in seq:
        key = lc(s)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def unit_to_grams(unit: str, portion_size: float, grams_per_slice: float) -> Tuple[str, float]:
    u = (unit or "").strip()
    if u in {"Scheibe(n)", "Scheibe"} and grams_per_slice and portion_size > 0:
        return "g", portion_size * grams_per_slice
    return u, portion_size


@dataclass
class Unresolved:
    name: str
    date: str
    meal: str
    portion_size: float
    unit: str


def parse_unresolved(resolved: Any) -> List[Unresolved]:
    items = resolved.get("items") if isinstance(resolved, dict) else resolved
    if not isinstance(items, list):
        return []

    out: List[Unresolved] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        if row.get("status") == "resolved":
            continue
        date = str(row.get("date") or "").strip()
        if not date:
            continue
        try:
            ps = float(row.get("portionSize", 1))
        except Exception:
            ps = 1.0
        out.append(
            Unresolved(
                name=str(row.get("name") or "").strip(),
                date=date,
                meal=norm_meal(str(row.get("mealTime") or "ANYTIME")),
                portion_size=ps,
                unit=str(row.get("unit") or "").strip(),
            )
        )
    return out


def call(script: str, *args: str) -> None:
    cmd = [sys.executable, script, *args]
    subprocess.run(cmd, check=True)


def resolve_one(scripts_dir: str, raw_path: str, out_path: str) -> Dict[str, Any]:
    call(os.path.join(scripts_dir, "ww_resolve_foods.py"), "--input", raw_path, "--output", out_path)
    with open(out_path, "r", encoding="utf-8") as f:
        return json.load(f)


def is_all_resolved(resolved_json: Dict[str, Any]) -> bool:
    items = resolved_json.get("items") if isinstance(resolved_json, dict) else None
    if not isinstance(items, list):
        return False
    ok = [i for i in items if isinstance(i, dict) and i.get("status") == "resolved"]
    return len(ok) == len(items) and len(items) > 0


def track_resolved(scripts_dir: str, resolved_path: str, track_out: str) -> Dict[str, Any]:
    cmd = [sys.executable, os.path.join(scripts_dir, "ww_track_resolved.py"), "--input", resolved_path, "--verify-summary"]
    with open(track_out, "w", encoding="utf-8") as f:
        subprocess.run(cmd, check=True, stdout=f)
    with open(track_out, "r", encoding="utf-8") as f:
        return json.load(f)


def write_raw(path: str, entry: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([entry], f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resolved", required=True)
    ap.add_argument("--scripts-dir", required=True)
    ap.add_argument("--grams-per-slice", type=float, default=25.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out-prefix", default="/tmp/ww_fallback_multi")
    args = ap.parse_args()

    with open(args.resolved, "r", encoding="utf-8") as f:
        resolved = json.load(f)

    unresolved = parse_unresolved(resolved)
    if not unresolved:
        print(json.dumps({"unresolved": 0, "attempted": 0, "tracked": 0, "failed": 0}))
        return 0

    summary = {"unresolved": len(unresolved), "attempted": 0, "tracked": 0, "failed": 0, "details": []}

    for idx, item in enumerate(unresolved, start=1):
        candidates = simplify_candidates(item.name)

        # Unit normalization (slice->grams) happens on the entry, not name
        for cand in candidates:
            portion = float(item.portion_size)
            unit = item.unit
            unit, portion = unit_to_grams(unit, portion, args.grams_per_slice)

            entry = {
                "name": cand,
                "portionSize": int(portion) if float(portion).is_integer() else round(portion, 4),
                "unit": unit,
                "mealTime": item.meal,
                "date": item.date,
            }

            summary["attempted"] += 1

            if args.dry_run:
                summary["details"].append({"original": item.name, "candidate": cand, "status": "dry_run"})
                break

            raw_path = f"{args.out_prefix}_{idx}_raw.json"
            res_path = f"{args.out_prefix}_{idx}_resolved.json"
            trk_path = f"{args.out_prefix}_{idx}_tracked.json"

            write_raw(raw_path, entry)

            try:
                res_json = resolve_one(args.scripts_dir, raw_path, res_path)
                if not is_all_resolved(res_json):
                    summary["details"].append({"original": item.name, "candidate": cand, "status": "not_resolved"})
                    continue

                _ = track_resolved(args.scripts_dir, res_path, trk_path)
                summary["tracked"] += 1
                summary["details"].append({"original": item.name, "candidate": cand, "status": "tracked"})
                # Clean up per-item temp files (keep only when debug needed)
                for p in (raw_path, res_path, trk_path):
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                break
            except subprocess.CalledProcessError as e:
                summary["details"].append({"original": item.name, "candidate": cand, "status": "error", "error": str(e)})
                continue
        else:
            summary["failed"] += 1

    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
