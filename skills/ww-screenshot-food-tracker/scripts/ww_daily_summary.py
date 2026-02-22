#!/usr/bin/env python3
"""Daily WW summary with protein target analysis and short recommendations."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import urllib.error
import urllib.request
from typing import Any

from env_loader import load_dotenv


class ApiError(RuntimeError):
    def __init__(self, code: int, url: str, body: str):
        super().__init__(f"HTTP {code} for {url}: {body}")
        self.code = code
        self.url = url
        self.body = body


def _ctx(insecure: bool) -> ssl.SSLContext:
    return ssl._create_unverified_context() if insecure else ssl.create_default_context()


def _request(url: str, token: str, timeout: int, insecure: bool) -> dict[str, Any]:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(insecure)) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(exc.code, url, body) from exc


def _fetch_member(base: str, token: str, timeout: int, insecure: bool) -> dict[str, Any]:
    # v2 works in this project setup and includes current weight.
    return _request(f"{base}/api/v2/cmx/members/~", token=token, timeout=timeout, insecure=insecure)


def _fetch_day_summary(base: str, token: str, date: str, timeout: int, insecure: bool) -> dict[str, Any]:
    url = f"{base}/api/v4/cmx/operations/composed/members/~/my-day-summary/{date}?noNegativeWeeklies=false&useHTS=true"
    return _request(url, token=token, timeout=timeout, insecure=insecure)


def _meal_feedback(meal_key: str, bucket: dict[str, Any]) -> str:
    protein = float(bucket.get("proteinGrams") or 0)
    veggies = float(bucket.get("vegetableServings") or 0)
    points = float(bucket.get("points") or 0)
    human = {"morning": "Frühstück", "midday": "Mittag", "evening": "Abend", "anytime": "Snacks"}.get(meal_key, meal_key)

    if protein >= 30 and veggies >= 1:
        return f"{human}: stark (viel Eiweiß + Gemüse)."
    if protein >= 25:
        return f"{human}: gut beim Eiweiß, Gemüse ginge noch besser."
    if protein >= 15:
        return f"{human}: okay, aber Eiweiß könnte höher sein."
    if points <= 2 and protein < 12:
        return f"{human}: sehr leicht, aber eiweißarm – evtl. Protein ergänzen."
    return f"{human}: ausbaufähig (mehr Proteinquelle wäre sinnvoll)."


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    base = os.getenv("WW_API_BASE_URL", "").rstrip("/")
    token = os.getenv("WW_API_TOKEN", "")
    timeout = int(os.getenv("WW_API_TIMEOUT", "20"))
    insecure = os.getenv("WW_API_INSECURE", "false").lower() in {"1", "true", "yes"} or args.insecure

    if not base:
        raise ValueError("WW_API_BASE_URL fehlt")
    if not token:
        raise ValueError("WW_API_TOKEN fehlt")

    target_date = args.date or dt.date.today().isoformat()
    target_dt = dt.date.fromisoformat(target_date)

    member = _fetch_member(base, token, timeout, insecure)
    weight = float(member.get("weight") or (member.get("userInfo") or {}).get("weight") or 0)
    protein_goal = round(weight * args.protein_factor, 1) if weight > 0 else None

    day = _fetch_day_summary(base, token, target_date, timeout, insecure)
    tracked = day.get("trackedSummary") or {}

    total_protein = 0.0
    meals_logged: list[str] = []
    meal_notes: list[str] = []
    for key in ["morning", "midday", "evening", "anytime"]:
        bucket = tracked.get(key)
        if not isinstance(bucket, dict):
            continue
        items = bucket.get("items") or []
        if not items:
            continue
        meals_logged.append(key)
        total_protein += float(bucket.get("proteinGrams") or 0)
        meal_notes.append(_meal_feedback(key, bucket))

    # 14-day trend
    daily_protein: list[dict[str, Any]] = []
    for i in range(args.lookback_days):
        d = (target_dt - dt.timedelta(days=i)).isoformat()
        try:
            s = _fetch_day_summary(base, token, d, timeout, insecure)
            ts = s.get("trackedSummary") or {}
            p = 0.0
            for key in ["morning", "midday", "evening", "anytime"]:
                b = ts.get(key)
                if isinstance(b, dict):
                    p += float(b.get("proteinGrams") or 0)
            daily_protein.append({"date": d, "proteinGrams": round(p, 1)})
        except Exception:
            daily_protein.append({"date": d, "proteinGrams": None})

    vals = [x["proteinGrams"] for x in daily_protein if isinstance(x.get("proteinGrams"), (float, int))]
    avg14 = round(sum(vals) / len(vals), 1) if vals else None

    deficit = None
    if protein_goal is not None:
        deficit = round(max(0.0, protein_goal - total_protein), 1)

    recs: list[str] = []
    if deficit is not None and deficit > 0:
        if deficit <= 20:
            recs.append("~250 g Skyr oder 1 kleiner Whey-Shake reichen meist aus.")
        elif deficit <= 40:
            recs.append("1 Whey-Shake + 250 g Skyr (oder 200 g Magerquark) wäre sinnvoll.")
        else:
            recs.append("2 proteinreiche Bausteine einplanen: z. B. Whey + 200 g Hähnchen/Fisch oder Skyr + Eiweißshake.")
    else:
        recs.append("Eiweißziel heute voraussichtlich erreicht oder sehr nah dran.")

    if avg14 is not None and protein_goal is not None and avg14 < protein_goal:
        gap = round(protein_goal - avg14, 1)
        recs.append(f"14-Tage-Schnitt liegt ~{gap} g unter Ziel: plane täglich eine feste Protein-Option am Nachmittag/Abend.")

    out = {
        "date": target_date,
        "weightKg": weight if weight > 0 else None,
        "proteinGoalGrams": protein_goal,
        "proteinConsumedGrams": round(total_protein, 1),
        "proteinDeficitGrams": deficit,
        "mealsLogged": meals_logged,
        "mealFeedback": meal_notes,
        "protein14dAverage": avg14,
        "protein14d": list(reversed(daily_protein)),
        "recommendations": recs,
    }

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"Datum: {out['date']}")
        print(f"Gewicht: {out['weightKg']} kg")
        print(f"Eiweiß: {out['proteinConsumedGrams']} / {out['proteinGoalGrams']} g")
        if deficit is not None:
            print(f"Fehlt: {deficit} g")
        print("Mahlzeiten:")
        for line in meal_notes:
            print(f"- {line}")
        print("Empfehlung:")
        for r in recs:
            print(f"- {r}")

    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="WW daily summary + protein recommendations")
    p.add_argument("--date", help="Datum YYYY-MM-DD (default: heute)")
    p.add_argument("--protein-factor", type=float, default=1.5, help="Protein-Ziel in g/kg Körpergewicht")
    p.add_argument("--lookback-days", type=int, default=14, help="Anzahl Tage für Trendanalyse")
    p.add_argument("--json", action="store_true", help="JSON ausgeben")
    p.add_argument("--insecure", action="store_true", help="TLS verification deaktivieren")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
