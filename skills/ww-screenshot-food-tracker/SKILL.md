---
name: ww-screenshot-food-tracker
description: Analyze meal screenshots, identify foods and portions, authenticate against WeightWatchers web endpoints, resolve foods through WeightWatchers APIs, and create tracked food entries. Use when a user asks to log food from screenshots, photos, or meal captures into WeightWatchers with minimal manual entry.
---

# WW Screenshot Food Tracker

Use this skill to convert meal screenshots into tracked WeightWatchers entries.

## Workflow

1. Analyze screenshot(s) and extract foods and estimated portions.
2. Build normalized raw JSON.
3. Acquire JWT (`Bearer`) if needed.
4. Resolve foods via search endpoint into resolved JSON.
5. Track resolved items.
6. Confirm tracked items and list ambiguous/failed items.

## Step 1: Analyze Screenshot

Extract foods conservatively. For each item include:

- `name`: user-friendly food name
- `portionSize`: numeric estimate (default `1` when unknown)
- `mealTime`: `MORNING`, `MIDDAY`, `EVENING`, or `ANYTIME` (or lowercase source text)
- `date`: `YYYY-MM-DD` (default today)
- `portionId` (optional): explicit portion identifier, if known

If confidence is low, keep the item and mention uncertainty in the final summary.

For WW list screenshots like the provided example, apply this parsing pattern:

1. Identify meal section from header (`Fruehstueck`, `Mittag`, `Abendessen`, `Snack`) and map to `mealTime`.
2. Treat each visible food label line block as one item name (brand + product text).
3. Read the quantity/unit from the gray line directly below the item (examples: `1 Stueck`, `30 g`, `1 1/2 EL`, `1/4 Packung(en)`).
4. Ignore the points column (`X Punkte`) for tracking payload generation.
5. If item title is truncated (`...`), keep best visible name and mark in summary as potentially ambiguous.

## Step 2: Build Raw JSON

Create a JSON array and save it to a temporary file.

Example:

```json
[
  {
    "name": "scrambled eggs",
    "portionSize": 2,
    "mealTime": "MORNING",
    "date": "2026-02-21"
  },
  {
    "name": "whole wheat toast",
    "portionSize": 1,
    "mealTime": "MORNING",
    "date": "2026-02-21"
  }
]
```

For unit conversion from screenshot quantities:

- `X g` -> set `portionSize` to numeric grams; prefer gram-based `portionId` if available.
- `X Stueck` / `X Portion(en)` / `X Packung(en)` -> set `portionSize` to numeric amount.
- Fractions (`1/4`, `1 1/2`) -> convert to decimal (`0.25`, `1.5`).

## Step 3: JWT Acquisition

If `WW_API_TOKEN` is missing, generate a token from account credentials:

```bash
scripts/ww_auth_jwt.py --username '<email>' --password '<password>' --tld de --raw
```

Use output as:

```bash
export WW_API_TOKEN='<jwt>'
```

Do not persist credentials or JWT in source files.

## Step 3b: Endpoint Discovery (Optional)

Probe known WW read endpoints (`v2/v3/v4`) and food-search variants:

```bash
scripts/ww_discover_endpoints.py --base-url https://cmx.weightwatchers.de --date 2026-02-21 --query ei
```

This returns status codes and response shape so you can pick stable paths for your account.

## Step 4: Resolve Foods Through API

Dry run:

```bash
scripts/ww_resolve_foods.py --input /tmp/foods_raw.json --output /tmp/foods_resolved.json --dry-run
```

Live resolve:

```bash
scripts/ww_resolve_foods.py --input /tmp/foods_raw.json --output /tmp/foods_resolved.json
```

## Step 5: Track Resolved Foods

Dry run first:

```bash
scripts/ww_track_resolved.py --input /tmp/foods_resolved.json --dry-run
```

Live run:

```bash
scripts/ww_track_resolved.py --input /tmp/foods_resolved.json
```

Live run with v4 day-summary verification:

```bash
scripts/ww_track_resolved.py --input /tmp/foods_resolved.json --verify-summary --summary-date 2026-02-21
```

Required env vars for live mode:

- `WW_API_BASE_URL` (for example `https://cmx.weightwatchers.de`)
- `WW_API_TOKEN`

Optional env vars:

- `WW_API_SEARCH_PATH` (default `/api/v3/search/foods`)
- `WW_API_TRACK_PATH` (default `/api/v4/cmx/members/~/trackedFoods/{date}`)
- `WW_API_SEARCH_QUERY_PARAM` (default `query`)
- `WW_API_SEARCH_EXTRA_PARAMS` (optional query string, e.g. `program=PersonalPoints&hitsPerPage=20&page=0`)
- `WW_API_TIMEOUT` (default `20`)
- `WW_API_SUMMARY_PATH_TEMPLATE` (default `/api/v4/cmx/operations/composed/members/~/my-day-summary/{date}`)
- `WW_API_SUMMARY_QUERY` (default `noNegativeWeeklies=false&useHTS=true`)

API details and endpoint notes: `references/ww-api-mapping.md`.

## Step 6: Return Confirmation

Report:

- Successfully tracked foods
- Not found/not trackable foods
- Assumptions (portion sizes, meal time, date)

If tracking partially fails, list failed items for manual correction.
