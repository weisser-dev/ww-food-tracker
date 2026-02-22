#!/usr/bin/env python3
"""Probe WW trackedFoods time field/value combinations."""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from typing import Any


def _post(url: str, token: str, payload: Any, insecure: bool) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=url,
        method="POST",
        data=data,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _message(body: str) -> str:
    try:
        return str(json.loads(body).get("message", ""))
    except Exception:
        return body[:200]


def run(args: argparse.Namespace) -> int:
    base = os.getenv("WW_API_BASE_URL", "").rstrip("/")
    token = os.getenv("WW_API_TOKEN", "")
    if not base:
        raise ValueError("WW_API_BASE_URL missing")
    if not token:
        raise ValueError("WW_API_TOKEN missing")

    url = f"{base}/api/v4/cmx/members/~/trackedFoods/{args.date}"
    item_base = {
        "_id": args.food_id,
        "versionId": args.version_id,
        "sourceType": args.source_type,
        "portionSize": args.portion_size,
        "portionId": args.portion_id,
        "mealId": None,
        "mealVersionId": None,
        "mealType": None,
        "entryId": None,
        "isQuickAdd": False,
    }

    field_candidates = ["timeOfDay", "mealTime", "meal_time", "time_of_day"]
    value_candidates = [
        "MORNING",
        "MIDDAY",
        "EVENING",
        "ANYTIME",
        "UNKNOWN",
        "BREAKFAST",
        "LUNCH",
        "DINNER",
        "SNACK",
        "morning",
        "midday",
        "evening",
        "anytime",
        "breakfast",
        "lunch",
        "dinner",
        "snack",
    ]

    out: list[dict[str, Any]] = []
    for field in field_candidates:
        for value in value_candidates:
            payload = [dict(item_base)]
            payload[0][field] = value
            status, body = _post(url=url, token=token, payload=payload, insecure=args.insecure)
            msg = _message(body)
            row = {"field": field, "value": value, "status": status, "message": msg}
            out.append(row)
            if status < 300:
                print(json.dumps({"success": row, "url": url}, indent=2, ensure_ascii=True))
                return 0

    # Print compact summary first, then full result.
    summary = {
        "total_attempts": len(out),
        "invalid_time_of_day": sum(1 for r in out if "INVALID_TIME_OF_DAY" in r["message"]),
        "other_errors": [r for r in out if "INVALID_TIME_OF_DAY" not in r["message"]][:10],
    }
    print(json.dumps({"url": url, "summary": summary, "results": out}, indent=2, ensure_ascii=True))
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Probe trackedFoods time field/value variants")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--food-id", required=True)
    p.add_argument("--version-id", required=True)
    p.add_argument("--source-type", default="WWFOOD")
    p.add_argument("--portion-size", type=float, default=1.0)
    p.add_argument("--portion-id")
    p.add_argument("--insecure", action="store_true")
    return p


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)

