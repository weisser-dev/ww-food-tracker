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


FALLBACK_QUERY_MAP: dict[str, list[str]] = {
    "tomaten, cocktailtomaten/kirschtomaten/cherrytomaten": ["Cherrytomaten", "Tomaten"],
    "cherrytomaten": ["Tomaten"],
    "kirschtomaten": ["Cherrytomaten", "Tomaten"],
}


def _query_variants(name: str) -> list[str]:
    variants = [name.strip()]
    if "," in name:
        rhs = name.split(",", 1)[1].strip()
        if rhs:
            variants.append(rhs)
    words = name.replace(",", " ").split()
    if len(words) > 3:
        variants.append(" ".join(words[:3]))

    # Deterministic fallbacks for problematic OCR/compound names.
    norm = name.strip().lower()
    variants.extend(FALLBACK_QUERY_MAP.get(norm, []))

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

    # Fast path: WW detail payload usually has `portions` list with `_id` + `name`.
    portions = hit.get("portions")
    if isinstance(portions, list):
        for p in portions:
            if not isinstance(p, dict):
                continue
            pid = p.get("_id") or p.get("id") or p.get("portionId") or p.get("servingId")
            pname = p.get("name") or p.get("_servingDesc") or p.get("displayName") or p.get("portionName")
            if pid is None or pname is None:
                continue
            sid = str(pid)
            if sid in seen:
                continue
            seen.add(sid)
            out.append(
                {
                    "id": sid,
                    "name": str(pname),
                    "isDefault": bool(p.get("isDefault") or p.get("default")),
                }
            )

    def looks_like_portion(node: dict[str, Any]) -> bool:
        # Avoid treating a full food hit as a portion just because it has `portionId`.
        if any(k in node for k in ("versionId", "sourceType", "foodId")):
            return False
        portion_hints = {"portionName", "displayName", "unit", "isDefault", "default", "gramWeight", "measurement", "_servingDesc", "weightType"}
        return any(k in node for k in portion_hints)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            pid = node.get("_id") or node.get("id") or node.get("portionId") or node.get("servingId")
            pname = node.get("name") or node.get("_servingDesc") or node.get("displayName") or node.get("portionName") or node.get("unit")
            if pid is not None and pname is not None and looks_like_portion(node):
                sid = str(pid)
                if sid not in seen:
                    seen.add(sid)
                    out.append({"id": sid, "name": str(pname), "isDefault": bool(node.get("isDefault") or node.get("default"))})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(hit)
    return out


def _normalize_text_token(s: str) -> str:
    s = (s or "").strip().lower()
    s = (
        s.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
    )
    return s


def _canonical_unit(unit: str) -> str:
    u = _normalize_text_token(unit)
    u = re.sub(r"[^a-z0-9]+", "", u)
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
        "scheibe": "scheibe",
        "scheiben": "scheibe",
    }
    return aliases.get(u, u)


def _portion_tokens(name: str) -> set[str]:
    normalized = _normalize_text_token(name)
    tokens = re.findall(r"[a-z0-9]+", normalized)
    out = {_canonical_unit(t) for t in tokens}
    if "stuck" in out:
        out.add("stueck")
    return out


def _contains_gram_hint(name: str) -> bool:
    n = _normalize_text_token(name)
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
    elif u in {"stueck", "scheibe", "el", "tl", "portion", "packung", "kg", "ml", "l"}:
        if u in tokens:
            score += 40

    if is_default:
        # Tiny tie-breaker only. Must never be enough evidence on its own.
        score += 5
    return score


def _has_unit_match_evidence(unit: str, portion_name: str) -> bool:
    u = _canonical_unit(unit)
    if not u:
        return False
    tokens = _portion_tokens(portion_name)
    if u == "g":
        return _contains_gram_hint(portion_name) or ("g" in tokens)
    return u in tokens


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


def _fetch_favorite_list(base_url: str, token: str, timeout: int, insecure: bool) -> dict[str, Any] | None:
    url = f"{base_url}/api/v3/cmx/operations/composed/members/~/lists/favorite"
    try:
        return _request(url=url, token=token, timeout=timeout, insecure=insecure)
    except ApiError as err:
        if err.code in (400, 404):
            return None
        raise


def _load_member_recipe_map(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict) and isinstance(data.get("recipes"), dict):
            return data
    except FileNotFoundError:
        return None
    except Exception:
        return None
    return None


def _member_recipe_hit_to_candidate(hit: dict[str, Any]) -> dict[str, Any] | None:
    """Convert a MEMBERRECIPE list hit into a resolve candidate-like dict."""
    if not isinstance(hit, dict):
        return None
    if str(hit.get("sourceType") or "").upper() != "MEMBERRECIPE":
        return None
    rid = hit.get("_id") or hit.get("id")
    ver = hit.get("versionId")
    if not (rid and ver):
        return None
    # Portion is usually "0" and serving desc contains unit.
    portion_id = hit.get("portionId")
    serving_desc = str(hit.get("_servingDesc") or hit.get("servingDesc") or "Portion(en)")
    portions = [{"id": str(portion_id or "0"), "name": serving_desc, "isDefault": True}]
    return {
        "_id": str(rid),
        "versionId": str(ver),
        "sourceType": "MEMBERRECIPE",
        "name": str(hit.get("_displayName") or hit.get("name") or ""),
        "portions": portions,
        "defaultPortionId": str(portion_id or "0"),
    }


def _find_member_recipe_candidate(
    name: str,
    recipe_map: dict[str, Any] | None,
    recent_payload: dict[str, Any] | None,
    favorite_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Try to resolve a name to a MEMBERRECIPE via (1) local map, (2) recent, (3) favorites."""
    q = (name or "").strip()
    if not q:
        return None
    q_lc = q.lower()

    # 1) Local mapping (exact / case-insensitive)
    if isinstance(recipe_map, dict):
        recipes = recipe_map.get("recipes")
        if isinstance(recipes, dict):
            # exact key
            entry = recipes.get(q)
            if not entry:
                # case-insensitive match
                for k, v in recipes.items():
                    if isinstance(k, str) and k.strip().lower() == q_lc:
                        entry = v
                        break
            if isinstance(entry, dict) and entry.get("id") and entry.get("versionId"):
                hit = {
                    "_id": entry.get("id"),
                    "versionId": entry.get("versionId"),
                    "sourceType": "MEMBERRECIPE",
                    "_displayName": q,
                    "portionId": entry.get("portionId", "0"),
                    "_servingDesc": entry.get("defaultUnit", "Portion(en)"),
                }
                cand = _member_recipe_hit_to_candidate(hit)
                if cand:
                    return cand

    def scan(payload: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        hits = payload.get("hits")
        if not isinstance(hits, list):
            return None
        # Prefer exact match on displayName, then substring.
        exact: list[dict[str, Any]] = []
        partial: list[dict[str, Any]] = []
        for h in hits:
            if not isinstance(h, dict) or str(h.get("sourceType") or "").upper() != "MEMBERRECIPE":
                continue
            disp = str(h.get("_displayName") or h.get("name") or "").strip()
            if not disp:
                continue
            d_lc = disp.lower()
            if d_lc == q_lc:
                exact.append(h)
            elif q_lc in d_lc or d_lc in q_lc:
                partial.append(h)
        for bucket in (exact, partial):
            for h in bucket:
                cand = _member_recipe_hit_to_candidate(h)
                if cand:
                    return cand
        return None

    return scan(recent_payload) or scan(favorite_payload)


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
            top_name = str(top.get("name") or "")
            top_score = _score_portion_match(unit, top_name, bool(top.get("isDefault")))
            # Default boost must not count as evidence.
            top_score_without_default = top_score - (5 if bool(top.get("isDefault")) else 0)
            if top_score_without_default > 0 and _has_unit_match_evidence(unit, top_name):
                return top["id"]

        # Unit was provided but no evidence-based match found -> fail closed.
        return None

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
    member_recipe_cache: dict[str, Any] | None = None
    favorites_recipe_cache: dict[str, Any] | None = None

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
            # As a fallback, try to resolve as a MEMBERRECIPE (custom user recipe)
            # via local mapping + recent/favorite lists.
            recipe_map_path = os.getenv("WW_MEMBER_RECIPE_MAP", os.path.join(os.path.dirname(__file__), "..", "..", "..", "member_recipes_map.json"))
            recipe_map = _load_member_recipe_map(recipe_map_path)
            if member_recipe_cache is None:
                member_recipe_cache = _fetch_recent_list(base_url=base_url, token=token, timeout=timeout, insecure=insecure) or {}
            if favorites_recipe_cache is None:
                favorites_recipe_cache = _fetch_favorite_list(base_url=base_url, token=token, timeout=timeout, insecure=insecure) or {}
            mr = _find_member_recipe_candidate(food["name"], recipe_map, member_recipe_cache, favorites_recipe_cache)
            if mr:
                portions = _extract_portions(mr)
                items.append(
                    {
                        **food,
                        "status": "resolved",
                        "resolved": {
                            "id": _resolve_id(mr),
                            "versionId": _resolve_version_id(mr),
                            "sourceType": _resolve_source_type(mr) or "MEMBERRECIPE",
                            "displayName": mr.get("name", food["name"]),
                            "portionCandidates": portions,
                            "portionId": _choose_portion_id(food, portions) or (portions[0]["id"] if portions else None),
                            "matchedPath": "/api/v3/cmx/operations/composed/members/~/lists/(recent|favorite)",
                            "matchedQuery": food["name"],
                            "warnings": ["resolved_via_member_recipe_list"],
                        },
                    }
                )
                continue
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
        unit_raw = str(food.get("unit") or "").strip()
        unit_required = unit_raw != ""
        used_server_default = False
        may_use_server_default = allow_server_default and (not unit_required)
        if not rpid and may_use_server_default:
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
