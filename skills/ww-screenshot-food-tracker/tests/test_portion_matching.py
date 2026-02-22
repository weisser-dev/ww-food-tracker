#!/usr/bin/env python3
import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "ww_resolve_foods.py"
spec = importlib.util.spec_from_file_location("ww_resolve_foods", MODULE_PATH)
mod = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(mod)


def test_gram_unit_does_not_fallback_to_default_portion_without_gram_evidence():
    input_food = {"name": "Linsen, rot, trocken", "unit": "g", "portionSize": 90}
    portions = [
        {"id": "p1", "name": "Portion(en)", "isDefault": True},
        {"id": "p2", "name": "Stück", "isDefault": False},
    ]
    assert mod._choose_portion_id(input_food, portions) is None


def test_gram_unit_prefers_gram_portion_when_available():
    input_food = {"name": "Karotten/Möhren", "unit": "g", "portionSize": 100}
    portions = [
        {"id": "p1", "name": "Portion(en)", "isDefault": True},
        {"id": "p2", "name": "100 g", "isDefault": False},
    ]
    assert mod._choose_portion_id(input_food, portions) == "p2"


def test_non_unit_input_may_use_default_portion():
    input_food = {"name": "Banane", "portionSize": 1}
    portions = [
        {"id": "p1", "name": "Portion(en)", "isDefault": True},
        {"id": "p2", "name": "Stück", "isDefault": False},
    ]
    assert mod._choose_portion_id(input_food, portions) == "p1"
