#!/usr/bin/env python3
"""Probe undocumented WW endpoints with a JWT token (read-only)."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _ctx(insecure: bool) -> ssl.SSLContext:
    return ssl._create_unverified_context() if insecure else ssl.create_default_context()


def _request(url: str, token: str, timeout: int, insecure: bool) -> tuple[int, dict[str, Any] | None, str | None]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ctx(insecure)) as resp:
            status = resp.getcode()
            raw = resp.read().decode("utf-8", errors="replace")
            if not raw:
                return status, {}, None
            try:
                return status, json.loads(raw), None
            except json.JSONDecodeError:
                return status, None, raw[:500]
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
            return exc.code, parsed, None
        except json.JSONDecodeError:
            return exc.code, None, body[:500]


def _shape(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"type": "non-object"}
    keys = sorted(payload.keys())
    shape: dict[str, Any] = {"keys": keys[:25]}
    for list_key in ["data", "results", "foods", "items", "trackedSummary"]:
        value = payload.get(list_key)
        if isinstance(value, list):
            shape[f"{list_key}_len"] = len(value)
            if value and isinstance(value[0], dict):
                shape[f"{list_key}_item_keys"] = sorted(value[0].keys())[:20]
        if isinstance(value, dict):
            shape[f"{list_key}_keys"] = sorted(value.keys())[:20]
    return shape


def run(args: argparse.Namespace) -> int:
    token = args.token or os.getenv("WW_API_TOKEN", "")
    if not token:
        raise SystemExit("error: missing token, set --token or WW_API_TOKEN")

    base = args.base_url.rstrip("/")
    date = args.date or dt.date.today().isoformat()
    q = urllib.parse.quote(args.query)

    date_digits = date.replace("-", "")
    epoch_seconds = int(time.time())
    epoch_millis = epoch_seconds * 1000

    endpoints = [
        # Confirmed useful read endpoint (user-provided)
        f"/api/v4/cmx/operations/composed/members/~/my-day-summary/{date}?noNegativeWeeklies=false&useHTS=true",
        # APK-discovered candidates around day views
        f"/api/v3/cmx/operations/composed/members/~/my-day/{date}",
        f"/api/v3/cmx/operations/composed/members/~/my-day-log/{date}",
        f"/api/v3/cmx/operations/composed/members/~/my-day-log/{date}/MIDDAY?program=PersonalPoints",
        # Tracking collections (GET may list, POST may write)
        "/api/v2/cmx/members/~/trackedFoods",
        "/api/v3/cmx/members/~/trackedFoods",
        "/api/v4/cmx/members/~/trackedFoods",
        f"/api/v4/cmx/members/~/trackedFoods/{date}",
        # Food search variants seen in prior probing / APK namespaces
        f"/api/v3/public/foods/search?q={q}",
        f"/api/v3/search/foods?q={q}",
        f"/api/v3/cmx/foods/search?q={q}",
        f"/api/v2/cmx/foods/search?q={q}",
        f"/api/v4/cmx/foods/search?q={q}",
        # Food catalog detail list roots from APK strings
        "/api/v3/public/foods/",
        "/api/v3/public/meals/",
        "/api/v3/public/recipes/",
        # Lists / recents from APK strings
        "/api/v3/cmx/operations/composed/members/~/lists/recent",
        "/api/v3/cmx/operations/composed/members/~/lists/favorite",
        # Member profile/settings endpoints from APK strings
        "/api/v4/cmx/members/~/profile",
        "/api/v3/cmx/members/~/settings/food",
        "/api/v3/cmx/members/~/settings/weight",
        # Param discovery: v3 search endpoint exists but "q" is rejected.
        f"/api/v3/search/foods?query={q}",
        f"/api/v3/search/foods?query={q}&program=PersonalPoints",
        f"/api/v3/search/foods?query={q}&hitsPerPage=20&page=0",
        f"/api/v3/search/foods?query={q}&attributesToRetrieve=_id,versionId,sourceType,name,portionId,portionName",
        f"/api/v3/search/foods?term={q}",
        f"/api/v3/search/foods?searchTerm={q}",
        f"/api/v3/search/foods?text={q}",
        # Param discovery: tracked foods endpoint appears to require date as int.
        f"/api/v2/cmx/members/~/trackedFoods?date={date}",
        f"/api/v2/cmx/members/~/trackedFoods?date={date_digits}",
        f"/api/v2/cmx/members/~/trackedFoods?date={epoch_seconds}",
        f"/api/v2/cmx/members/~/trackedFoods?date={epoch_millis}",
    ]

    rows: list[dict[str, Any]] = []
    for path in endpoints:
        url = f"{base}{path}"
        status, payload, text = _request(url, token=token, timeout=args.timeout, insecure=args.insecure)
        row: dict[str, Any] = {"path": path, "status": status}
        if payload is not None:
            row["shape"] = _shape(payload)
            if isinstance(payload, dict) and payload.get("message"):
                row["message"] = payload.get("message")
        if text:
            row["text_preview"] = text
        rows.append(row)

    status_buckets: dict[str, list[str]] = {"2xx": [], "3xx": [], "4xx": [], "5xx": []}
    for row in rows:
        code = int(row["status"])
        key = f"{code // 100}xx"
        if key in status_buckets:
            status_buckets[key].append(row["path"])

    hints = {
        "working_read_endpoints": [],
        "existing_but_param_issue": [],
        "likely_write_endpoints_from_apk": [
            "/api/v4/cmx/members/~/trackedFoods/{date} (POST list of TrackItemRequest)",
            "/api/v3/cmx/members/~/trackedFoods/{date} (PATCH/DELETE list of TrackItemRequest)",
        ],
        "apk_evidence_files": [
            "sources/com/weightwatchers/search/retrofit/FoodSearchService.java",
            "sources/com/weightwatchers/food/common/service/FoodTrackingService.java",
            "sources/com/weightwatchers/food/common/requests/TrackItemRequestJsonAdapter.java",
        ],
    }
    for row in rows:
        code = int(row["status"])
        msg = str(row.get("message", "")).lower()
        if 200 <= code < 300:
            hints["working_read_endpoints"].append(row["path"])
        if "unknown parameter" in msg or "cannot parse parameter" in msg:
            hints["existing_but_param_issue"].append({"path": row["path"], "message": row.get("message", "")})

    print(
        json.dumps(
            {"base_url": base, "date": date, "status_buckets": status_buckets, "hints": hints, "results": rows},
            indent=2,
            ensure_ascii=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Probe known WW endpoints and report status/shape")
    p.add_argument("--base-url", default=os.getenv("WW_API_BASE_URL", "https://cmx.weightwatchers.de"))
    p.add_argument("--token", help="JWT token (or set WW_API_TOKEN)")
    p.add_argument("--date", help="Date for day endpoints (YYYY-MM-DD), default today")
    p.add_argument("--query", default="egg", help="Search term for food-search endpoints")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only)")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
