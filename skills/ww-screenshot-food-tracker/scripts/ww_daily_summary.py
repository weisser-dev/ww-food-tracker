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


def _pick_name(item: dict[str, Any]) -> str:
    for k in ("_displayName", "_ingredientName", "portionName"):
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return "Unbekannt"


def _collect_quick_protein_candidates(day_summaries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Aggregate protein-rich, practical foods from last days.
    agg: dict[str, dict[str, float]] = {}
    for s in day_summaries:
        ts = s.get("trackedSummary") or {}
        for mk in ("morning", "midday", "evening", "anytime"):
            b = ts.get(mk)
            if not isinstance(b, dict):
                continue
            for item in b.get("items") or []:
                if not isinstance(item, dict):
                    continue
                name = _pick_name(item)
                protein = float(item.get("proteinGrams") or 0)
                calories = float(item.get("calories") or 0)
                if protein <= 0:
                    continue
                key = name.lower()
                slot = agg.setdefault(key, {"name": name, "protein": 0.0, "calories": 0.0, "count": 0.0})
                slot["protein"] += protein
                slot["calories"] += calories
                slot["count"] += 1

    rows: list[dict[str, Any]] = []
    for v in agg.values():
        count = max(v["count"], 1.0)
        avg_p = v["protein"] / count
        avg_k = v["calories"] / count if v["calories"] > 0 else 0.0
        # Prioritize practical high-protein items.
        score = avg_p - (avg_k / 120.0)
        rows.append({"name": v["name"], "avgProtein": round(avg_p, 1), "avgCalories": round(avg_k, 1), "score": score})

    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:8]


def _protein_ideas(deficit: float | None, candidates: list[dict[str, Any]]) -> list[str]:
    if deficit is None or deficit <= 0:
        return []

    quick_keywords = (
        "skyr",
        "quark",
        "magerquark",
        "shake",
        "whey",
        "joghurt",
        "frischkäse fitline",
        "hüttenkäse",
        "protein pudding",
    )

    options: list[dict[str, Any]] = []

    # From 14-day history: only quick/practical foods.
    for c in candidates:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        if not any(k in name.lower() for k in quick_keywords):
            continue
        p = float(c.get("avgProtein") or 0)
        k = float(c.get("avgCalories") or 0)
        if p < 8:
            continue
        options.append({"label": f"1x {name}", "protein": p, "kcal": k})

    # Reliable quick fallbacks (always available).
    options.extend(
        [
            {"label": "1x Proteinshake mit Wasser", "protein": 24.0, "kcal": 120.0},
            {"label": "250 g Magerquark", "protein": 30.0, "kcal": 170.0},
            {"label": "250 g Skyr", "protein": 25.0, "kcal": 160.0},
        ]
    )

    # de-duplicate by label
    dedup: dict[str, dict[str, Any]] = {}
    for o in options:
        dedup[o["label"]] = o
    options = list(dedup.values())

    # Rank by closeness to deficit, prefer slight over-coverage.
    def rank(o: dict[str, Any]) -> tuple[float, float]:
        p = float(o["protein"])
        diff = p - float(deficit)
        penalty = abs(diff)
        if diff < 0:
            penalty += 0.8  # slight bias towards meeting target in one shot
        return (penalty, -p)

    options.sort(key=rank)

    ideas: list[str] = []
    for o in options[:3]:
        p = round(float(o.get("protein") or 0))
        k = round(float(o.get("kcal") or 0))
        ideas.append(f"{o['label']} (~{k} kcal / ~{p} g Protein)")

    return ideas


def _meal_labels(meals_logged: list[str]) -> str:
    mapping = {"morning": "Frühstück", "midday": "Mittag", "evening": "Abend", "anytime": "Snacks"}
    labels = [mapping.get(x, x) for x in meals_logged]
    return ", ".join(labels) if labels else "noch nichts"


def _missing_meals_text(meals_logged: list[str]) -> str:
    wanted = [("morning", "Frühstück"), ("midday", "Mittag"), ("evening", "Abend")]
    existing = set(meals_logged)
    missing = [label for key, label in wanted if key not in existing]
    return ", ".join(missing) if missing else "keine"


def _build_message_13(*, missing_meals: str, protein_now: float, protein_min_13: float, kcal_now: float, kcal_target: float | None) -> str:
    kcal_part = f"Kalorien bisher: {kcal_now:.0f}/{kcal_target:.0f} kcal." if kcal_target is not None else f"Kalorien bisher: {kcal_now:.0f} kcal."
    return (
        "Hey 👋 wie es aussieht fehlt heute noch dein Essen-Tracking.\n"
        f"Aktuell fehlen z. B.: {missing_meals}.\n"
        f"Bis 13 Uhr wären ca. {protein_min_13:.1f} g Protein sinnvoll (3/8 Ziel), aktuell: {protein_now:.1f} g.\n"
        f"{kcal_part}"
    )


def _build_message_19(*, meals_text: str, protein_now: float, protein_goal: float, kcal_now: float, kcal_target: float | None, remaining_kcal: float | None, ideas: list[str]) -> str:
    kcal_line = f"Heute: {kcal_now:.0f}/{kcal_target:.0f} kcal" if kcal_target is not None else f"Heute: {kcal_now:.0f} kcal"
    if remaining_kcal is not None:
        kcal_line += f" (offen: {remaining_kcal:.0f} kcal)."
    else:
        kcal_line += "."

    status = "unter" if protein_now < protein_goal else "über"
    ideas_text = " oder ".join(ideas[:2]) if ideas else "1x Proteinshake mit Wasser (~120 kcal / ~24 g Protein)"

    return (
        "Kurzer Check-in ✅\n"
        f"Heute getrackt: {meals_text}. Passt das so?\n"
        f"Du bist noch {status} deinem Proteinziel ({protein_now:.1f}/{protein_goal:.1f} g).\n"
        f"{kcal_line}\n"
        f"Proteine sind wichtig in der Diät 💪 Vorschlag: {ideas_text}."
    )


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
    points = day.get("pointsDetails") or {}

    total_protein = float(points.get("proteinGrams") or 0.0)
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
        meal_notes.append(_meal_feedback(key, bucket))

    # kcal status from targets (prefer midpoint if range)
    calories_consumed = float(points.get("calories") or 0.0)
    targets = points.get("targets") or {}
    cal_target = None
    cal_range = targets.get("calories") if isinstance(targets, dict) else None
    if isinstance(cal_range, dict):
        cmin = cal_range.get("min")
        cmax = cal_range.get("max")
        if isinstance(cmin, (int, float)) and isinstance(cmax, (int, float)):
            cal_target = round((float(cmin) + float(cmax)) / 2.0)
        elif isinstance(cmax, (int, float)):
            cal_target = round(float(cmax))
        elif isinstance(cmin, (int, float)):
            cal_target = round(float(cmin))
    remaining_calories = round(cal_target - calories_consumed, 1) if cal_target is not None else None

    # 14-day trend
    daily_protein: list[dict[str, Any]] = []
    day_summaries: list[dict[str, Any]] = []
    for i in range(args.lookback_days):
        d = (target_dt - dt.timedelta(days=i)).isoformat()
        try:
            s = _fetch_day_summary(base, token, d, timeout, insecure)
            day_summaries.append(s)
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
    protein_ideas = _protein_ideas(deficit, _collect_quick_protein_candidates(day_summaries))
    if deficit is not None and deficit > 0:
        if remaining_calories is not None and remaining_calories > 0:
            recs.append(f"Du hast noch ca. {remaining_calories:.0f} kcal offen und es fehlen ~{deficit:.1f} g Protein.")
        else:
            recs.append(f"Es fehlen heute noch ~{deficit:.1f} g Protein.")
        recs.extend(protein_ideas)
    else:
        recs.append("Eiweißziel heute erreicht oder sehr nah dran.")

    if avg14 is not None and protein_goal is not None and avg14 < protein_goal:
        gap = round(protein_goal - avg14, 1)
        recs.append(f"14-Tage-Schnitt liegt ~{gap} g unter Ziel: plane täglich eine feste Protein-Option am Nachmittag/Abend.")

    protein_min_by_13 = round((protein_goal or 0) * 3.0 / 8.0, 1) if protein_goal is not None else None
    meals_text = _meal_labels(meals_logged)
    missing_meals = _missing_meals_text(meals_logged)

    msg13 = _build_message_13(
        missing_meals=missing_meals,
        protein_now=round(total_protein, 1),
        protein_min_13=protein_min_by_13 or 0.0,
        kcal_now=round(calories_consumed, 1),
        kcal_target=float(cal_target) if cal_target is not None else None,
    )
    msg19 = _build_message_19(
        meals_text=meals_text,
        protein_now=round(total_protein, 1),
        protein_goal=float(protein_goal or 0.0),
        kcal_now=round(calories_consumed, 1),
        kcal_target=float(cal_target) if cal_target is not None else None,
        remaining_kcal=remaining_calories,
        ideas=protein_ideas,
    )

    out = {
        "date": target_date,
        "weightKg": weight if weight > 0 else None,
        "proteinGoalGrams": protein_goal,
        "proteinConsumedGrams": round(total_protein, 1),
        "proteinDeficitGrams": deficit,
        "proteinMinimumBy13": protein_min_by_13,
        "caloriesConsumed": round(calories_consumed, 1),
        "caloriesTarget": cal_target,
        "remainingCalories": remaining_calories,
        "mealsLogged": meals_logged,
        "mealsLoggedText": meals_text,
        "missingMealsText": missing_meals,
        "mealFeedback": meal_notes,
        "protein14dAverage": avg14,
        "protein14d": list(reversed(daily_protein)),
        "quickProteinIdeas": protein_ideas,
        "recommendations": recs,
        "messages": {
            "13": msg13,
            "19": msg19,
        },
    }

    if args.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"Datum: {out['date']}")
        print(f"Gewicht: {out['weightKg']} kg")
        print(f"Eiweiß: {out['proteinConsumedGrams']} / {out['proteinGoalGrams']} g")
        if out.get("caloriesTarget") is not None:
            print(f"Kalorien: {out['caloriesConsumed']} / {out['caloriesTarget']} kcal")
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
