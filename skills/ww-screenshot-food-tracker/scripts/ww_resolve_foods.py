#!/usr/bin/env python3
"""Resolve screenshot food entries against WW search API and save enriched JSON."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from env_loader import load_dotenv


class ApiError(RuntimeError):
    def __init__(self, code: int, url: str, body: str):
        super().__init__(f"HTTP {code} for {url}: {body}")
        self.code = code
        self.url = url
        self.body = body


def _request(url: str, token: str, timeout: int, insecure: bool) -> dict[str, Any]:
    req = urllib.request.Request(
        url=url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
    )
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
        raise ValueError("input must be a JSON list")

    today = dt.date.today().isoformat()
    out: list[dict[str, Any]] = []
    for i, row in enumerate(data):
        if not isinstance(row, dict):
            raise ValueError(f"item #{i} must be object")
        name = str(row.get("name", "")).strip()
        if not name:
            raise ValueError(f"item #{i} missing 'name'")
        out.append(
            {
                "name": name,
                "portionSize": float(row.get("portionSize", row.get("quantity", 1))),
                "mealTime": str(row.get("mealTime", row.get("meal", "ANYTIME"))),
                "date": str(row.get("date", today)),
                "portionId": row.get("portionId"),
                "unit": row.get("unit"),
            }
        )
    return out


def _query_variants(name: str) -> list[str]:
    variants = [name.strip()]
    if "," in name:
        rhs = name.split(",", 1)[1].strip()
        if rhs:
            variants.append(rhs)
    words = name.replace(",", " ").split()
    if len(words) > 3:
        variants.append(" ".join(words[:3]))

    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        k = v.lower()
        if v and k not in seen:
            seen.add(k)
            out.append(v)
    return out


def _extract_candidates(resp: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for key in ["hits", "results", "foods", "items", "data"]:
        value = resp.get(key)
        if isinstance(value, list):
            for x in value:
                if isinstance(x, dict):
                    candidates.append(x)
        if isinstance(value, dict):
            for nested_key in ["hits", "results", "foods", "items", "data"]:
                nested = value.get(nested_key)
                if isinstance(nested, list):
                    for x in nested:
                        if isinstance(x, dict):
                            candidates.append(x)
    if not candidates and isinstance(resp.get("id"), (str, int)):
        candidates.append(resp)
    return candidates


def _resolve_id(hit: dict[str, Any]) -> str | None:
    value = hit.get("_id") or hit.get("id") or hit.get("foodId") or hit.get("uuid") or hit.get("objectID")
    if value is None and isinstance(hit.get("food"), dict):
        nested = hit["food"]
        value = nested.get("_id") or nested.get("id") or nested.get("foodId")
    return str(value) if value is not None else None


def _resolve_version_id(hit: dict[str, Any]) -> str | None:
    value = hit.get("versionId") or hit.get("version_id")
    if value is None and isinstance(hit.get("food"), dict):
        nested = hit["food"]
        value = nested.get("versionId") or nested.get("version_id")
    return str(value) if value is not None else None


def _resolve_source_type(hit: dict[str, Any]) -> str:
    value = hit.get("sourceType")
    if value is None and isinstance(hit.get("food"), dict):
        value = hit["food"].get("sourceType")
    if isinstance(value, str) and value.strip():
        return value
    return "WWFOOD"


def _extract_portions(hit: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def looks_like_portion(node: dict[str, Any]) -> bool:
        # Avoid treating a full food hit as a portion just because it has `portionId`.
        if any(k in node for k in ("versionId", "sourceType", "points", "pointValue", "foodId")):
            return False
        portion_hints = {"portionName", "displayName", "unit", "isDefault", "gramWeight", "measurement"}
        return any(k in node for k in portion_hints)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pid = node.get("id") or node.get("portionId") or node.get("servingId")
            pname = node.get("name") or node.get("displayName") or node.get("portionName") or node.get("unit")
            if pid is not None and pname is not None and looks_like_portion(node):
                sid = str(pid)
                if sid not in seen:
                    seen.add(sid)
                    out.append({"id": sid, "name": str(pname), "isDefault": bool(node.get("isDefault"))})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(hit)
    return out


def _canonical_unit(unit: str) -> str:
    u = unit.strip().lower()
    aliases = {
        "gram": "g",
        "grams": "g",
        "gramm": "g",
        "gramme": "g",
        "g": "g",
        "kg": "kg",
        "ml": "ml",
        "l": "l",
        "el": "el",
        "essloeffel": "el",
        "tbsp": "el",
        "tl": "tl",
        "teeloeffel": "tl",
        "tsp": "tl",
        "stueck": "stueck",
        "stück": "stueck",
        "stk": "stueck",
        "piece": "stueck",
        "portion": "portion",
        "portionen": "portion",
        "packung": "packung",
        "packungen": "packung",
    }
    return aliases.get(u, u)


def _portion_tokens(name: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    out = {_canonical_unit(t) for t in tokens}
    if "stuck" in out:
        out.add("stueck")
    return out


def _contains_gram_hint(name: str) -> bool:
    n = name.lower()
    if re.search(r"(^|[^a-z0-9])g([^a-z0-9]|$)", n):
        return True
    if re.search(r"\bgramm?\b", n):
        return True
    if re.search(r"\b\d+(?:[.,]\d+)?\s*g\b", n):
        return True
    return False


def _score_portion_match(unit: str, portion_name: str, is_default: bool) -> int:
    tokens = _portion_tokens(portion_name)
    has_gram = _contains_gram_hint(portion_name) or ("g" in tokens)
    u = _canonical_unit(unit)
    score = 0

    if u in tokens:
        score += 100

    if u == "g":
        if has_gram:
            score += 120
        # Strongly penalize typical non-gram units when input asks for grams.
        if tokens.intersection({"stueck", "el", "tl", "portion", "packung"}):
            score -= 120
    elif u in {"stueck", "el", "tl", "portion", "packung"}:
        if u in tokens:
            score += 40

    if is_default:
        score += 5
    return score


def _fetch_food_details(
    base_url: str,
    token: str,
    food_id: str,
    version_id: str | None,
    program: str | None,
    timeout: int,
    insecure: bool,
) -> dict[str, Any] | None:
    paths: list[str] = []
    if version_id:
        paths.append(f"/api/v3/public/foods/{food_id}/{version_id}")
    paths.append(f"/api/v3/public/foods/{food_id}")
    paths.append(f"/api/v3/cmx/operations/members/~/foods/{food_id}")

    for path in paths:
        url = f"{base_url}{path}"
        if program:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}program={urllib.parse.quote(program)}"
        try:
            return _request(url=url, token=token, timeout=timeout, insecure=insecure)
        except ApiError as err:
            if err.code in (400, 404, 500):
                continue
            raise
    return None


def _fetch_recent_list(base_url: str, token: str, timeout: int, insecure: bool) -> dict[str, Any] | None:
    url = f"{base_url}/api/v3/cmx/operations/composed/members/~/lists/recent"
    try:
        return _request(url=url, token=token, timeout=timeout, insecure=insecure)
    except ApiError as err:
        if err.code in (400, 404):
            return None
        raise


def _find_portions_in_payload(payload: Any, food_id: str, version_id: str | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    fid = str(food_id)
    fver = str(version_id) if version_id is not None else None

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            node_id = node.get("_id") or node.get("id") or node.get("itemId") or node.get("foodId")
            node_ver = node.get("versionId") or node.get("itemVersionId")
            pid = node.get("portionId")
            pname = node.get("portionName") or node.get("_servingDesc") or node.get("servingDesc")
            if node_id is not None and str(node_id) == fid and pid is not None:
                if (fver is None) or (node_ver is None) or (str(node_ver) == fver):
                    spid = str(pid)
                    if spid not in seen:
                        seen.add(spid)
                        out.append({"id": spid, "name": str(pname or ""), "isDefault": False})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(payload)
    return out


def _choose_portion_id(input_food: dict[str, Any], portions: list[dict[str, Any]]) -> str | None:
    provided = str(input_food["portionId"]) if input_food.get("portionId") else None
    unit = _canonical_unit(str(input_food.get("unit") or ""))

    if provided:
        for p in portions:
            if p["id"] == provided:
                return provided

    if unit:
        ranked = sorted(
            portions,
            key=lambda p: _score_portion_match(unit, str(p.get("name") or ""), bool(p.get("isDefault"))),
            reverse=True,
        )
        if ranked:
            top = ranked[0]
            top_score = _score_portion_match(unit, str(top.get("name") or ""), bool(top.get("isDefault")))
            # Require positive evidence when a concrete unit is provided.
            if top_score > 0:
                return top["id"]

    for p in portions:
        if p.get("isDefault"):
            return p["id"]

    return portions[0]["id"] if portions else provided


def run(args: argparse.Namespace) -> int:
    load_dotenv()
    foods = _load_foods(args.input)

    base_url = os.getenv("WW_API_BASE_URL", "").rstrip("/")
    token = os.getenv("WW_API_TOKEN", "")
    search_path = os.getenv("WW_API_SEARCH_PATH", "/api/v3/search/foods")
    search_paths = [
        p.strip()
        for p in os.getenv(
            "WW_API_SEARCH_PATHS",
            f"{search_path},/api/v3/public/foods/search",
        ).split(",")
        if p.strip()
    ]
    query_key = os.getenv("WW_API_SEARCH_QUERY_PARAM", "query")
    extra_raw = os.getenv("WW_API_SEARCH_EXTRA_PARAMS", "")
    allow_server_default = os.getenv("WW_RESOLVE_ALLOW_SERVER_DEFAULT", "true").lower() in {"1", "true", "yes"}
    program = os.getenv("WW_API_PROGRAM", "maxpointsSimple").strip()
    timeout = int(os.getenv("WW_API_TIMEOUT", "20"))
    insecure = os.getenv("WW_API_INSECURE", "false").lower() in {"1", "true", "yes"} or args.insecure

    if not args.dry_run:
        if not base_url:
            raise ValueError("WW_API_BASE_URL required unless --dry-run")
        if not token:
            raise ValueError("WW_API_TOKEN required unless --dry-run")

    extra_params = dict(urllib.parse.parse_qsl(extra_raw, keep_blank_values=False)) if extra_raw.strip() else {}
    recent_cache: dict[str, Any] | None = None

    items: list[dict[str, Any]] = []
    for food in foods:
        if args.dry_run:
            hit = {
                "_id": f"dryrun-{food['name'].lower().replace(' ', '-')}",
                "versionId": "dryrun-v1",
                "sourceType": "WWFOOD",
                "name": food["name"],
                "portions": [{"id": food.get("portionId") or "dryrun-portion", "name": "default", "isDefault": True}],
            }
            portions = _extract_portions(hit)
            items.append(
                {
                    **food,
                    "status": "resolved",
                    "resolved": {
                        "id": _resolve_id(hit),
                        "versionId": _resolve_version_id(hit),
                        "sourceType": _resolve_source_type(hit),
                        "displayName": hit.get("name", food["name"]),
                        "portionCandidates": portions,
                        "portionId": _choose_portion_id(food, portions),
                        "matchedPath": search_path,
                        "matchedQuery": food["name"],
                    },
                }
            )
            continue

        resolved_hit: dict[str, Any] | None = None
        matched_path = ""
        matched_query = ""
        for query_value in _query_variants(food["name"]):
            params = dict(extra_params)
            params[query_key] = query_value
            query = urllib.parse.urlencode(params)
            for path in search_paths:
                url = f"{base_url}{path}?{query}"
                try:
                    resp = _request(url=url, token=token, timeout=timeout, insecure=insecure)
                    candidates = _extract_candidates(resp)
                    for c in candidates:
                        cid = _resolve_id(c)
                        cver = _resolve_version_id(c)
                        if cid and cver:
                            resolved_hit = c
                            matched_path = path
                            matched_query = query_value
                            break
                    if resolved_hit:
                        break
                except ApiError as err:
                    if err.code in (400, 404):
                        continue
                    raise
            if resolved_hit:
                break

        if not resolved_hit:
            items.append({**food, "status": "not_found", "resolved": None})
            continue

        portions = _extract_portions(resolved_hit)
        rid = _resolve_id(resolved_hit)
        rver = _resolve_version_id(resolved_hit)
        if rid and not portions:
            details = _fetch_food_details(
                base_url=base_url,
                token=token,
                food_id=rid,
                version_id=rver,
                program=program or None,
                timeout=timeout,
                insecure=insecure,
            )
            if isinstance(details, dict):
                # Prefer detail payload for portion extraction.
                details_portions = _extract_portions(details)
                if details_portions:
                    portions = details_portions
                # Also use potential defaults from details payload.
                if isinstance(details.get("defaultPortionId"), (str, int)) and not resolved_hit.get("defaultPortionId"):
                    resolved_hit["defaultPortionId"] = details.get("defaultPortionId")
                if isinstance(details.get("portionId"), (str, int)) and not resolved_hit.get("portionId"):
                    resolved_hit["portionId"] = details.get("portionId")
        if rid and not portions:
            if recent_cache is None:
                recent_cache = _fetch_recent_list(base_url=base_url, token=token, timeout=timeout, insecure=insecure)
            if isinstance(recent_cache, dict):
                portions = _find_portions_in_payload(recent_cache, food_id=rid, version_id=rver)
        rpid = _choose_portion_id(food, portions)
        unit_required = str(food.get("unit") or "").strip() != ""
        used_server_default = False
        if not rpid and (allow_server_default or not unit_required):
            hit_pid = resolved_hit.get("portionId") or resolved_hit.get("defaultPortionId")
            if isinstance(hit_pid, (str, int)):
                rpid = str(hit_pid)
                used_server_default = True
        if not rid or not rver:
            items.append(
                {
                    **food,
                    "status": "not_resolved",
                    "resolved": {
                        "id": rid,
                        "versionId": rver,
                        "sourceType": _resolve_source_type(resolved_hit),
                        "displayName": resolved_hit.get("name") or food["name"],
                        "portionCandidates": portions,
                        "portionId": rpid,
                        "matchedPath": matched_path,
                        "matchedQuery": matched_query,
                    },
                }
            )
            continue

        warnings: list[str] = []
        if not rpid:
            warnings.append("portionId_missing")
        if portions == [] and unit_required and used_server_default:
            warnings.append("portion_candidates_missing_used_server_default")
        if portions:
            warnings.append("portion_candidates_recovered")

        if unit_required and not rpid:
            items.append(
                {
                    **food,
                    "status": "not_resolved",
                    "resolved": {
                        "id": rid,
                        "versionId": rver,
                        "sourceType": _resolve_source_type(resolved_hit),
                        "displayName": resolved_hit.get("name") or food["name"],
                        "portionCandidates": portions,
                        "portionId": rpid,
                        "matchedPath": matched_path,
                        "matchedQuery": matched_query,
                        "warnings": warnings + ["unit_required_but_no_matching_portion"],
                    },
                }
            )
            continue

        items.append(
            {
                **food,
                "status": "resolved",
                "resolved": {
                    "id": rid,
                    "versionId": rver,
                    "sourceType": _resolve_source_type(resolved_hit),
                    "displayName": resolved_hit.get("name") or food["name"],
                    "portionCandidates": portions,
                    "portionId": rpid,
                    "matchedPath": matched_path,
                    "matchedQuery": matched_query,
                    "warnings": warnings,
                },
            }
        )

    summary = {
        "total": len(items),
        "resolved": sum(1 for i in items if i["status"] == "resolved"),
        "not_found": sum(1 for i in items if i["status"] == "not_found"),
        "not_resolved": sum(1 for i in items if i["status"] == "not_resolved"),
    }

    output = {"summary": summary, "items": items}
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(output, fh, indent=2, ensure_ascii=True)
    print(json.dumps({"written": args.output, "summary": summary}, indent=2, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Resolve foods against WW search API and write resolved JSON")
    p.add_argument("--input", required=True, help="Path to raw foods JSON list")
    p.add_argument("--output", required=True, help="Path to output resolved JSON")
    p.add_argument("--dry-run", action="store_true", help="Skip API calls and emit mock-resolved items")
    p.add_argument("--insecure", action="store_true", help="Disable TLS verification (testing only)")
    return p


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
