# WW OpenAPI Specs (Generated from Decompiled APK)

Generated on: 2026-02-21
Source: `com.weightwatchers.mobile.apk_Decompiler.com/sources`

## Files

- Full extracted spec (all Retrofit namespaces found, including third-party):
  - `/Users/erikweisser/Documents/New project/skills/ww-screenshot-food-tracker/references/ww-undocumented-openapi.json`
- WeightWatchers-focused spec (source files under `com/weightwatchers/` + `defpackage/`):
  - `/Users/erikweisser/Documents/New project/skills/ww-screenshot-food-tracker/references/ww-cmx-openapi.json`

## Counts

- Full: 309 paths, 359 operations
- WW-focused: 275 paths, 316 operations

## Swagger import

1. Open Swagger Editor / Postman / Insomnia.
2. Import one of the JSON specs above.
3. Set server URL to your tenant, e.g. `https://cmx.weightwatchers.de`.
4. Configure bearer auth with a fresh JWT token.

## Known high-value endpoints

- Search:
  - `GET /api/v3/search/foods` (uses query-map style params; `query` works in your tenant)
- Tracking write:
  - `POST /api/v4/cmx/members/~/trackedFoods/{date}` (list body of TrackItemRequest)
- Day summary:
  - `GET /api/v4/cmx/operations/composed/members/~/my-day-summary/{date}`

## Notes

- These specs are reverse-engineered, not official.
- Some operations are environment- or feature-flag-dependent.
- Request/response schemas are intentionally generic unless confirmed from decompiled request models.
- Validate write endpoints carefully on a test day/account state.
