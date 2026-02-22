# Fallback query words (resolve)

If a food name is not resolved reliably, the resolver tries these alternatives.

- `Tomaten, Cocktailtomaten/Kirschtomaten/Cherrytomaten` -> `Cherrytomaten`, `Tomaten`
- `Cherrytomaten` -> `Tomaten`
- `Kirschtomaten` -> `Cherrytomaten`, `Tomaten`

Implementation: `scripts/ww_resolve_foods.py` (`FALLBACK_QUERY_MAP`).
