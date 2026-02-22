#!/usr/bin/env python3
"""Search and track foods through a WeightWatchers-compatible API."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    def __init__(self, code: int, url: str, body: str):
        super().__init__(f"HTTP {code} for {url}: {body}")
        self.code = code
        self.url = url
        self.body = body


def _request(
    url: str,
    token: str,
    method: str = "GET",
    payload: Any = None,
    timeout: int = 20,
    insecure: bool = False,
) -> dict[str, Any]:
    data = None
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url=url, data=data, method=method, headers=headers)
    ctx = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ApiError(exc.code, url, body) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error for {url}: {exc.reason}") from exc


def _load_foods(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        raise ValueError("foods input must be a JSON list")

    today = dt.date.today().isoformat()
    out: list[dict[str, Any]] = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"item #{idx} must be an object")

        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError(f"item #{idx} missing non-empty 'name'")

        out.append(
            {
                "name": name,
                "portionSize": row.get("portionSize", row.get("quantity", 1)),
                "mealTime": str(row.get("mealTime", row.get("meal", "SNACK"))),
                "date": str(row.get("date", today)),
                "portionId": row.get("portionId"),
                "unit": row.get("unit"),
            }
        )
    return out


def _find_first_result(resp: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []

    for key in ["results", "foods", "items", "data", "hits"]:
        value = resp.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        if isinstance(value, dict):
            for nested_key in ["results", "foods", "items", "data", "hits"]:
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    candidates.extend(nested)

    for item in candidates:
        if isinstance(item, dict):
            return item

    if isinstance(resp.get("id"), (str, int)):
        return resp
    return None


def _resolve_food_id(resolved: dict[str, Any]) -> str | None:
    value = (
        resolved.get("_id")
        or resolved.get("id")
        or resolved.get("foodId")
        or resolved.get("uuid")
        or resolved.get("objectID")
        or resolved.get("food_id")
    )
    if value is None and isinstance(resolved.get("food"), dict):
        nested = resolved["food"]
        value = nested.get("_id") or nested.get("id") or nested.get("foodId") or nested.get("uuid")
    return str(value) if value is not None else None


def _resolve_version_id(resolved: dict[str, Any]) -> str | None:
    value = resolved.get("versionId") or resolved.get("version_id")
    if value is None and isinstance(resolved.get("food"), dict):
        nested = resolved["food"]
        value = nested.get("versionId") or nested.get("version_id")
    return str(value) if value is not None else None


def _resolve_source_type(resolved: dict[str, Any]) -> str:
    value = resolved.get("sourceType")
    if value is None and isinstance(resolved.get("food"), dict):
        value = resolved["food"].get("sourceType")
    if isinstance(value, str) and value.strip():
        return value
    return "WWFOOD"


def _extract_portions(resolved: dict[str, Any]) -> list[dict[str, Any]]:
    portions: list[dict[str, Any]] = []
    for candidate in [resolved.get("portions"), resolved.get("food", {}).get("portions") if isinstance(resolved.get("food"), dict) else None]:
        if isinstance(candidate, list):
            for item in candidate:
                if isinstance(item, dict):
                    pid = item.get("id") or item.get("portionId") or item.get("servingId")
                    if pid is None:
                        continue
                    portions.append(
                        {
                            "id": str(pid),
                            "name": str(item.get("name") or item.get("displayName") or item.get("portionName") or ""),
                            "isDefault": bool(item.get("isDefault")),
                        }
                    )
    return portions


def _resolve_portion_id(food: dict[str, Any], resolved: dict[str, Any], dry_run: bool) -> str | None:
    provided = str(food["portionId"]) if food.get("portionId") else None
    unit = str(food.get("unit") or "").strip().lower()

    portions = _extract_portions(resolved)
    if portions:
        if provided:
            for p in portions:
                if p["id"] == provided:
                    return provided
        if unit:
            for p in portions:
                pname = p["name"].lower()
                if unit in pname or (unit in {"g", "gram", "grams"} and "g" in pname):
                    return p["id"]
        for p in portions:
            if p["isDefault"]:
                return p["id"]
        return portions[0]["id"]

    if provided:
        return provided

    portions = resolved.get("portions")
    if isinstance(portions, list) and portions and isinstance(portions[0], dict):
        for key in ["id", "portionId", "servingId"]:
            if portions[0].get(key):
                return str(portions[0][key])
    if isinstance(resolved.get("food"), dict):
        nested = resolved["food"]
        for key in ["defaultPortionId", "portionId", "servingId"]:
            if nested.get(key):
                return str(nested[key])
        nested_portions = nested.get("portions")
        if isinstance(nested_portions, list) and nested_portions and isinstance(nested_portions[0], dict):
            for key in ["id", "portionId", "servingId"]:
                if nested_portions[0].get(key):
                    return str(nested_portions[0][key])

    if dry_run:
        return "dryrun-portion"
    return None


def _normalize_meal_time(value: str) -> str:
    meal = value.strip().lower()
    mapping = {
        "breakfast": "MORNING",
        "lunch": "MIDDAY",
        "dinner": "EVENING",
        "snack": "ANYTIME",
        "morning": "MORNING",
        "midday": "MIDDAY",
        "evening": "EVENING",
        "anytime": "ANYTIME",
    }
    if value.isupper() and value in {"MORNING", "MIDDAY", "EVENING", "ANYTIME", "UNKNOWN"}:
        return value
    return mapping.get(meal, "ANYTIME")


def _query_variants(name: str) -> list[str]:
    variants = [name.strip()]
    if "," in name:
        # Try product part without brand prefix.
        right = name.split(",", 1)[1].strip()
        if right:
            variants.append(right)
    words = name.replace(",", " ").split()
    if len(words) > 3:
        variants.append(" ".join(words[:3]))
    # de-duplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _search_food(
    base_url: str,
    token: str,
    search_paths: list[str],
    search_query_param: str,
    search_extra_params: dict[str, str],
    food_name: str,
    timeout: int,
    insecure: bool,
) -> dict[str, Any] | None:
    last_error: ApiError | None = None
    for query_value in _query_variants(food_name):
        params = dict(search_extra_params)
        params[search_query_param] = query_value
        query = urllib.parse.urlencode(params)
        for path in search_paths:
            search_url = f"{base_url}{path}?{query}" if base_url else f"{path}?{query}"
            try:
                search_resp = _request(search_url, token=token, timeout=timeout, insecure=insecure)
                resolved = _find_first_result(search_resp)
                if resolved:
                    return resolved
            except ApiError as err:
                # 404 is common for misses on undocumented endpoints; try next variant/path.
                if err.code in (400, 404):
                    last_error = err
                    continue
                raise
    if last_error:
        return None
    return None


def run(args: argparse.Namespace) -> int:
    foods = _load_foods(args.input)

    base_url = os.getenv("WW_API_BASE_URL", "").rstrip("/")
    token = os.getenv("WW_API_TOKEN", "")
    search_path = os.getenv("WW_API_SEARCH_PATH", "/api/v3/search/foods")
    search_paths = [
        p.strip()
        for p in os.getenv(
            "WW_API_SEARCH_PATHS",
            f"{search_path},/api/v3/public/foods/search,/api/v3/cmx/foods/search,/api/v2/cmx/foods/search",
        ).split(",")
        if p.strip()
    ]
    track_path = os.getenv("WW_API_TRACK_PATH", "/api/v4/cmx/members/~/trackedFoods/{date}")
    search_query_param = os.getenv("WW_API_SEARCH_QUERY_PARAM", "query")
    search_extra_params_raw = os.getenv("WW_API_SEARCH_EXTRA_PARAMS", "")
    search_extra_params: dict[str, str] = {}
    if search_extra_params_raw.strip():
        parsed = urllib.parse.parse_qsl(search_extra_params_raw, keep_blank_values=False)
        search_extra_params = {k: v for k, v in parsed if k and v}
    summary_path_template = os.getenv(
        "WW_API_SUMMARY_PATH_TEMPLATE",
        "/api/v4/cmx/operations/composed/members/~/my-day-summary/{date}",
    )
    summary_query = os.getenv("WW_API_SUMMARY_QUERY", "noNegativeWeeklies=false&useHTS=true")
    timeout = int(os.getenv("WW_API_TIMEOUT", "20"))
    insecure = os.getenv("WW_API_INSECURE", "false").lower() in {"1", "true", "yes"} or args.insecure

    if not args.dry_run:
        if not base_url:
            raise ValueError("WW_API_BASE_URL is required unless --dry-run is set")
        if not token:
            raise ValueError("WW_API_TOKEN is required unless --dry-run is set")

    actions: list[dict[str, Any]] = []
    for food in foods:
        if args.dry_run:
            resolved = {
                "_id": f"dryrun-{food['name'].lower().replace(' ', '-')}",
                "versionId": "dryrun-v1",
                "sourceType": "WWFOOD",
                "name": food["name"],
                "defaultPortionId": "dryrun-portion",
            }
        else:
            resolved = _search_food(
                base_url=base_url,
                token=token,
                search_paths=search_paths,
                search_query_param=search_query_param,
                search_extra_params=search_extra_params,
                food_name=food["name"],
                timeout=timeout,
                insecure=insecure,
            )
            if not resolved:
                actions.append({"status": "not_found", "food": food})
                continue

        food_id = _resolve_food_id(resolved)
        version_id = _resolve_version_id(resolved)
        source_type = _resolve_source_type(resolved)
        portion_id = _resolve_portion_id(food, resolved, args.dry_run)
        if not food_id or not version_id or not portion_id:
            actions.append(
                {
                    "status": "not_trackable",
                    "food": food,
                    "match": resolved,
                    "missing": {
                        "foodId": not bool(food_id),
                        "versionId": not bool(version_id),
                        "portionId": not bool(portion_id),
                    },
                }
            )
            continue

        request_item = {
            "_id": food_id,
            "versionId": version_id,
            "sourceType": source_type,
            "timeOfDay": _normalize_meal_time(food["mealTime"]),
            "portionSize": float(food["portionSize"]),
            "portionId": portion_id,
            "mealId": None,
            "mealVersionId": None,
            "mealType": None,
            "entryId": None,
            "isQuickAdd": False,
        }

        dated_track_path = track_path.format(date=food["date"])
        track_url = f"{base_url}{dated_track_path}" if base_url else dated_track_path
        if args.dry_run:
            actions.append({"status": "tracked", "food": food, "payload": [request_item], "track_url": track_url, "dry_run": True})
        else:
            track_resp = _request(
                track_url,
                token=token,
                method="POST",
                payload=[request_item],
                timeout=timeout,
                insecure=insecure,
            )
            actions.append({"status": "tracked", "food": food, "payload": [request_item], "track_url": track_url, "response": track_resp})

    output: dict[str, Any] = {"actions": actions}
    if args.verify_summary and not args.dry_run:
        verify_date = args.summary_date or (foods[0]["date"] if foods else dt.date.today().isoformat())
        summary_path = summary_path_template.format(date=verify_date)
        summary_url = f"{base_url}{summary_path}"
        if summary_query:
            summary_url = f"{summary_url}?{summary_query}"
        summary = _request(summary_url, token=token, timeout=timeout, insecure=insecure)
        output["summary"] = {
            "date": verify_date,
            "url": summary_url,
            "response": summary,
        }

    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Resolve and track foods through a WeightWatchers-compatible API")
    parser.add_argument("--input", required=True, help="Path to JSON array of detected foods")
    parser.add_argument("--dry-run", action="store_true", help="Skip network calls and print what would be tracked")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only)")
    parser.add_argument(
        "--verify-summary",
        action="store_true",
        help="After tracking, fetch v4 my-day-summary for validation",
    )
    parser.add_argument(
        "--summary-date",
        help="Summary date (YYYY-MM-DD). Defaults to first food date.",
    )
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:  # pragma: no cover
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
