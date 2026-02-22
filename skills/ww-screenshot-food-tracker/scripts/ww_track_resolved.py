#!/usr/bin/env python3
"""Track previously resolved food entries to WW v4 trackedFoods endpoint."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from collections import Counter, defaultdict
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


def _request(url: str, token: str, method: str, payload: Any, timeout: int, insecure: bool) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        url=url,
        method=method,
        data=data,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(insecure)) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def _normalize_time(value: str) -> str:
    meal = value.strip().lower()
    mapping = {
        "breakfast": "morning",
        "lunch": "midday",
        "dinner": "evening",
        "snack": "anytime",
        "morning": "morning",
        "midday": "midday",
        "evening": "evening",
        "anytime": "anytime",
        "unknown": "anytime",
    }
    if value.isupper() and value in {"MORNING", "MIDDAY", "EVENING", "ANYTIME", "UNKNOWN"}:
        return value.lower() if value != "UNKNOWN" else "anytime"
    return mapping.get(meal, "anytime")


def _time_variants(value: str) -> list[str]:
    base = _normalize_time(value)
    variants = {
        "morning": ["morning", "MORNING", "breakfast", "BREAKFAST"],
        "midday": ["midday", "MIDDAY", "lunch", "LUNCH"],
        "evening": ["evening", "EVENING", "dinner", "DINNER"],
        "anytime": ["anytime", "ANYTIME", "snack", "SNACK"],
    }.get(base, [base, "anytime", "ANYTIME"])
    out: list[str] = []
    seen: set[str] = set()
    for v in variants:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def _is_invalid_time_of_day(err: Exception) -> bool:
    if not isinstance(err, ApiError):
        return False
    body = (err.body or "").upper()
    return "INVALID_TIME_OF_DAY" in body


def _build_summary_url(base: str, summary_path_template: str, summary_query: str, date_key: str) -> str:
    spath = summary_path_template.format(date=date_key)
    surl = f"{base}{spath}"
    if summary_query:
        surl = f"{surl}?{summary_query}"
    return surl


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _signature(item: dict[str, Any], time_of_day_hint: str | None = None, ignore_portion_id: bool = False) -> tuple[str, str, str, float | None, str]:
    item_id = item.get("_id") or item.get("itemId") or item.get("id")
    version_id = item.get("versionId")
    portion_id = "" if ignore_portion_id else str(item.get("portionId") or "")
    portion_size = _to_float_or_none(item.get("portionSize"))
    portion_size = round(portion_size, 4) if portion_size is not None else None
    tod_value = time_of_day_hint if time_of_day_hint is not None else str(item.get("timeOfDay") or "anytime")
    tod = _normalize_time(tod_value)
    return (str(item_id or ""), str(version_id or ""), portion_id, portion_size, tod)


def _existing_signature_counters(summary_resp: dict[str, Any]) -> tuple[Counter[tuple[str, str, str, float | None, str]], Counter[tuple[str, str, str, float | None, str]]]:
    full: Counter[tuple[str, str, str, float | None, str]] = Counter()
    relaxed: Counter[tuple[str, str, str, float | None, str]] = Counter()
    tracked = summary_resp.get("trackedSummary")
    if not isinstance(tracked, dict):
        return full, relaxed
    for tod_key, bucket in tracked.items():
        if not isinstance(bucket, dict):
            continue
        items = bucket.get("items")
        if not isinstance(items, list):
            continue
        for row in items:
            if not isinstance(row, dict):
                continue
            full[_signature(row, time_of_day_hint=str(tod_key), ignore_portion_id=False)] += 1
            relaxed[_signature(row, time_of_day_hint=str(tod_key), ignore_portion_id=True)] += 1
    return full, relaxed


def _remove_already_tracked(
    req_items: list[dict[str, Any]],
    existing_full: Counter[tuple[str, str, str, float | None, str]],
    existing_relaxed: Counter[tuple[str, str, str, float | None, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    to_track: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for req in req_items:
        s_full = _signature(req, ignore_portion_id=False)
        s_relaxed = _signature(req, ignore_portion_id=True)
        if existing_full[s_full] > 0:
            existing_full[s_full] -= 1
            skipped.append({"reason": "already_tracked_full_match", "item": req})
            continue
        if existing_relaxed[s_relaxed] > 0:
            existing_relaxed[s_relaxed] -= 1
            skipped.append({"reason": "already_tracked_relaxed_match", "item": req})
            continue
        to_track.append(req)
    return to_track, skipped


def _load_resolved(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict):
        items = data.get("items")
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("resolved input must be a JSON list or object with 'items' list")
    out: list[dict[str, Any]] = []
    today = dt.date.today().isoformat()
    for row in items:
        if not isinstance(row, dict):
            continue
        resolved = row.get("resolved")
        if row.get("status") != "resolved" or not isinstance(resolved, dict):
            continue
        rid = resolved.get("id")
        ver = resolved.get("versionId")
        src = resolved.get("sourceType") or "WWFOOD"
        pid = resolved.get("portionId")
        if not (rid and ver):
            continue
        portion_id = str(pid) if pid is not None else None
        req: dict[str, Any] = {
            "_id": str(rid),
            "versionId": str(ver),
            "sourceType": str(src),
            "timeOfDay": _normalize_time(str(row.get("mealTime", "ANYTIME"))),
            "portionSize": float(row.get("portionSize", 1)),
        }
        if portion_id:
            req["portionId"] = portion_id
        out.append({"name": row.get("name"), "date": str(row.get("date", today)), "request": req})
    return out


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    items = _load_resolved(args.input)

    base = os.getenv("WW_API_BASE_URL", "").rstrip("/")
    token = os.getenv("WW_API_TOKEN", "")
    track_template = os.getenv("WW_API_TRACK_PATH", "/api/v4/cmx/members/~/trackedFoods/{date}")
    summary_path_template = os.getenv(
        "WW_API_SUMMARY_PATH_TEMPLATE",
        "/api/v4/cmx/operations/composed/members/~/my-day-summary/{date}",
    )
    summary_query = os.getenv("WW_API_SUMMARY_QUERY", "noNegativeWeeklies=false&useHTS=true")
    timeout = int(os.getenv("WW_API_TIMEOUT", "20"))
    insecure = os.getenv("WW_API_INSECURE", "false").lower() in {"1", "true", "yes"} or args.insecure

    if not args.dry_run:
        if not base:
            raise ValueError("WW_API_BASE_URL required unless --dry-run")
        if not token:
            raise ValueError("WW_API_TOKEN required unless --dry-run")

    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for i in items:
        by_date[i["date"]].append(i["request"])

    actions: list[dict[str, Any]] = []
    for date_key in sorted(by_date.keys()):
        req_items = by_date[date_key]
        path = track_template.format(date=date_key)
        url = f"{base}{path}" if base else path
        if args.dry_run:
            actions.append({"status": "would_track", "date": date_key, "url": url, "count": len(req_items), "payload": req_items})
            continue

        dedupe_skipped: list[dict[str, Any]] = []
        if not args.allow_duplicates:
            summary_url = _build_summary_url(base=base, summary_path_template=summary_path_template, summary_query=summary_query, date_key=date_key)
            summary_resp = _request(url=summary_url, token=token, method="GET", payload=None, timeout=timeout, insecure=insecure)
            existing_full, existing_relaxed = _existing_signature_counters(summary_resp)
            req_items, dedupe_skipped = _remove_already_tracked(req_items, existing_full, existing_relaxed)
            if not req_items:
                actions.append(
                    {
                        "status": "skipped_all_already_tracked",
                        "date": date_key,
                        "url": url,
                        "count": 0,
                        "skipped_count": len(dedupe_skipped),
                        "skipped": dedupe_skipped,
                    }
                )
                continue

        try:
            resp = _request(url=url, token=token, method="POST", payload=req_items, timeout=timeout, insecure=insecure)
            actions.append(
                {
                    "status": "tracked",
                    "date": date_key,
                    "url": url,
                    "count": len(req_items),
                    "skipped_count": len(dedupe_skipped),
                    "skipped": dedupe_skipped,
                    "response": resp,
                }
            )
        except Exception as exc:
            if not _is_invalid_time_of_day(exc):
                raise
            # Fallback: retry per item with alternative time-of-day representations.
            fallback_actions: list[dict[str, Any]] = []
            for item in req_items:
                original = str(item.get("timeOfDay") or "ANYTIME")
                last_error: str | None = None
                tracked = False
                for candidate in _time_variants(original):
                    req = dict(item)
                    req["timeOfDay"] = candidate
                    try:
                        resp = _request(url=url, token=token, method="POST", payload=[req], timeout=timeout, insecure=insecure)
                        fallback_actions.append(
                            {
                                "status": "tracked_fallback",
                                "date": date_key,
                                "url": url,
                                "id": req.get("_id"),
                                "timeOfDay_original": original,
                                "timeOfDay_used": candidate,
                                "response": resp,
                            }
                        )
                        tracked = True
                        break
                    except Exception as inner_exc:
                        last_error = str(inner_exc)
                        continue
                if not tracked:
                    fallback_actions.append(
                        {
                            "status": "failed_fallback",
                            "date": date_key,
                            "url": url,
                            "id": item.get("_id"),
                            "timeOfDay_original": original,
                            "error": last_error,
                        }
                    )
            actions.append(
                {
                    "status": "tracked_with_fallback",
                    "date": date_key,
                    "url": url,
                    "count": len(req_items),
                    "skipped_count": len(dedupe_skipped),
                    "skipped": dedupe_skipped,
                    "initial_error": str(exc),
                    "items": fallback_actions,
                }
            )

    output: dict[str, Any] = {
        "summary": {"eligible_items": len(items), "dates": len(by_date), "actions": len(actions)},
        "actions": actions,
    }

    if args.verify_summary and not args.dry_run:
        verify_date = args.summary_date or (sorted(by_date.keys())[0] if by_date else dt.date.today().isoformat())
        spath = summary_path_template.format(date=verify_date)
        surl = f"{base}{spath}"
        if summary_query:
            surl = f"{surl}?{summary_query}"
        sresp = _request(url=surl, token=token, method="GET", payload=None, timeout=timeout, insecure=insecure)
        output["summary_check"] = {"date": verify_date, "url": surl, "response": sresp}

    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Track WW foods from resolved JSON")
    p.add_argument("--input", required=True, help="Path to resolved JSON (ww_resolve_foods output)")
    p.add_argument("--dry-run", action="store_true", help="Do not send writes")
    p.add_argument("--verify-summary", action="store_true", help="Fetch v4 day summary after write")
    p.add_argument("--summary-date", help="Date for summary verification (YYYY-MM-DD)")
    p.add_argument("--allow-duplicates", action="store_true", help="Disable idempotency dedupe check against my-day-summary")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only)")
    return p


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
