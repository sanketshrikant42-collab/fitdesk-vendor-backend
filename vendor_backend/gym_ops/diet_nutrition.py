# Copied from FitDesk Pro v1.2.0 (GymERP/services/diet_nutrition.py.py) so the server
# calculates diet nutrition like the desktop app. Changes (28 Oct 2026): food names
# match ignoring case and punctuation (built-in food list, gym_ops/food_db.py); an item
# may carry values typed by hand ("manual"); live results include the food used.

"""Diet & Nutrition -- nutrition calculation and plan safety checks.

Calculation basis
-----------------
Every food in the gym's library (diet_foods) has values for ONE serving:
``serving_qty`` ``serving_unit`` -> kcal, protein_g, carbs_g, fat_g, e.g.
"100 g -> 116 kcal" or "1 pc -> 71 kcal". Values are entered by the gym;
a blank value means "not entered" (NULL), never zero.

A meal item's amount is its ``quantity`` + ``unit`` or, for starter-plan
items, read from its portion text ("1 katori (150 g)" -> 150 g,
"2 pcs" -> 2 pc). The item's values are

    value = food value per serving x (item amount / serving amount)

and are only calculated when the item and the food use the same unit
(g/kg and ml/l convert; nothing else does -- there is no guessing of how
many grams a katori holds).

Every item gets a status. Totals add up whatever IS known, and are marked
complete only when every item has all four values; the UI never shows an
incomplete total as complete.

Nothing in this module invents a nutrition value.
"""

import re

NUTRIENTS = ("kcal", "protein_g", "carbs_g", "fat_g")

# Status of one item's calculation.
OK = "ok"
PARTIAL = "partial"            # food has some values missing
NO_FOOD = "no_food"            # food not in the library
NO_VALUES = "no_values"        # food in library, no values entered
NO_QUANTITY = "no_quantity"    # amount could not be read
UNIT_MISMATCH = "unit_mismatch"

STATUS_TEXT = {
    OK: "Calculated",
    PARTIAL: "Some values missing",
    NO_FOOD: "Not in food library",
    NO_VALUES: "No values entered",
    NO_QUANTITY: "Amount not set",
    UNIT_MISMATCH: "Unit differs from library serving",
}

# Plans are flagged for professional review below this many kcal/day
# (only when the day's total is complete). A flag, not a prescription.
LOW_KCAL_REVIEW = 1200
MIN_ADULT_AGE = 18

# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

_UNIT_ALIASES = {
    "g": ("g", 1), "gm": ("g", 1), "gms": ("g", 1), "gram": ("g", 1), "grams": ("g", 1),
    "kg": ("g", 1000), "kgs": ("g", 1000),
    "ml": ("ml", 1), "l": ("ml", 1000), "ltr": ("ml", 1000), "litre": ("ml", 1000),
    "liter": ("ml", 1000), "litres": ("ml", 1000),
    "pc": ("pc", 1), "pcs": ("pc", 1), "piece": ("pc", 1), "pieces": ("pc", 1), "nos": ("pc", 1),
    "no": ("pc", 1),
}
_PLURALS = {"glasses": "glass", "eggs": "egg", "slices": "slice", "cups": "cup",
            "bowls": "bowl", "katoris": "katori", "plates": "plate", "tbsps": "tbsp",
            "tsps": "tsp", "scoops": "scoop"}


def normalize_unit(unit):
    """('g', 1) for "grams"; ('ml', 1000) for "l"; ('cup', 1) for "cups"."""
    u = str(unit or "").strip().lower().rstrip(".")
    if not u:
        return None, 1
    if u in _UNIT_ALIASES:
        return _UNIT_ALIASES[u]
    return _PLURALS.get(u, u), 1


def _number(text):
    text = text.strip()
    if "/" in text:
        a, b = text.split("/", 1)
        return float(a) / float(b)
    return float(text)


_PAREN = re.compile(r"\(\s*(\d+(?:\.\d+)?)\s*(g|gm|kg|ml|l)\b[^)]*\)", re.I)
_LEAD = re.compile(r"^\s*(\d+(?:\.\d+)?(?:/\d+)?)\s*([a-zA-Z]+)?")


def parse_portion(text):
    """Amount written in a portion: (quantity, unit) in normalized units,
    or (None, None). A weight/volume in brackets wins:
    "1 katori (150 g)" -> (150, "g"); "2 pcs" -> (2, "pc");
    "100 g fish" -> (100, "g"); "1 medium" -> (1, "medium"); "2" -> (2, "pc")."""
    text = str(text or "")
    m = _PAREN.search(text)
    if m:
        unit, factor = normalize_unit(m.group(2))
        return float(m.group(1)) * factor, unit
    m = _LEAD.match(text)
    if not m:
        return None, None
    try:
        qty = _number(m.group(1))
    except (ValueError, ZeroDivisionError):
        return None, None
    unit, factor = normalize_unit(m.group(2) or "pc")
    return qty * factor, unit


def item_amount(item):
    """(quantity, unit) for a meal item: explicit quantity+unit first, else
    read from its portion text."""
    qty = item.get("quantity")
    if qty not in (None, ""):
        unit, factor = normalize_unit(item.get("unit") or "pc")
        try:
            return float(qty) * factor, unit
        except (TypeError, ValueError):
            return None, None
    return parse_portion(item.get("portion"))


# --------------------------------------------------------------------------
# Calculation
# --------------------------------------------------------------------------

def _food_for(item, foods_by_id, foods_by_name):
    from gym_ops.food_db import norm
    food = foods_by_id.get(item.get("food_id")) if item.get("food_id") else None
    name = item.get("food_name") or item.get("food") or ""
    if food is not None and norm(food.get("name")) != norm(name):
        food = None                      # the food was changed in the plan; look it up again by name
    if food is None:
        food = foods_by_name.get(norm(name)) or foods_by_name.get(name.strip().lower())
    return food


def manual_values(item):
    """Values typed by hand for this item (its whole amount), or None."""
    if not item.get("manual"):
        return None
    return {n: (None if item.get(n) in (None, "") else float(item.get(n))) for n in NUTRIENTS}


def calc_item(item, food):
    """{status, kcal, protein_g, carbs_g, fat_g} for one item and its
    library food (None when not in the library)."""
    out = {n: None for n in NUTRIENTS}
    typed = manual_values(item)
    if typed is not None:
        known = [v for v in typed.values() if v is not None]
        out.update({n: (None if v is None else round(v, 1)) for n, v in typed.items()})
        out["status"] = OK if len(known) == 4 else PARTIAL if known else NO_VALUES
        out["manual"] = True
        return out
    if food is None:
        out["status"] = NO_FOOD
        return out
    values = [food.get(n) for n in NUTRIENTS]
    if all(v is None for v in values):
        out["status"] = NO_VALUES
        return out
    qty, unit = item_amount(item)
    if qty is None:
        out["status"] = NO_QUANTITY
        return out
    serving_unit, factor = normalize_unit(food.get("serving_unit"))
    serving_qty = food.get("serving_qty")
    if not serving_qty or serving_unit != unit:
        out["status"] = UNIT_MISMATCH
        return out
    ratio = qty / (float(serving_qty) * factor)
    for n, v in zip(NUTRIENTS, values):
        out[n] = None if v is None else round(v * ratio, 1)
    out["status"] = OK if all(v is not None for v in values) else PARTIAL
    return out


def _empty_totals():
    return {n: 0.0 for n in NUTRIENTS}


def _summarize(meal_results):
    day = _empty_totals()
    issues, total = 0, 0
    for meal in meal_results:
        for n in NUTRIENTS:
            day[n] = round(day[n] + meal["totals"][n], 1)
        issues += meal["issues"]
        total += len(meal["items"])
    return {"meals": meal_results, "day": day, "complete": issues == 0 and total > 0,
            "issues": issues, "items": total, "counted": total - issues}


def calculate(meals, foods_by_id=None, foods_by_name=None):
    """Live calculation from the food library (templates, drafts, starter
    plans). Returns {meals: [{name, totals, complete, issues, items:[...]}],
    day: totals, complete, issues, items, counted}."""
    foods_by_id = foods_by_id or {}
    foods_by_name = foods_by_name or {}
    results = []
    for meal in meals or []:
        totals, rows, issues = _empty_totals(), [], 0
        for item in meal.get("items") or []:
            calc = calc_item(item, _food_for(item, foods_by_id, foods_by_name))
            for n in NUTRIENTS:
                if calc[n] is not None:
                    totals[n] = round(totals[n] + calc[n], 1)
            if calc["status"] != OK:
                issues += 1
            food = None if calc.get("manual") else _food_for(item, foods_by_id, foods_by_name)
            rows.append({**item, "calc": calc,
                         "food": {"id": food.get("id"), "name": food.get("name"), "serving_qty": food.get("serving_qty"),
                                  "serving_unit": food.get("serving_unit"), "kcal": food.get("kcal"),
                                  "builtin": bool(food.get("builtin"))} if food else None})
        results.append({"name": meal.get("name"), "time": meal.get("time"), "totals": totals,
                        "issues": issues, "complete": issues == 0 and bool(rows), "items": rows})
    return _summarize(results)


def from_snapshot(meals):
    """Totals from the values saved WITH a plan version (what the member
    was given). An item missing any value counts as an issue."""
    results = []
    for meal in meals or []:
        totals, rows, issues = _empty_totals(), [], 0
        for item in meal.get("items") or []:
            values = {n: item.get(n) for n in NUTRIENTS}
            known = [v for v in values.values() if v is not None]
            status = OK if len(known) == 4 else PARTIAL if known else NO_VALUES
            for n, v in values.items():
                if v is not None:
                    totals[n] = round(totals[n] + v, 1)
            if status != OK:
                issues += 1
            rows.append({**item, "calc": {**values, "status": status}})
        results.append({"name": meal.get("name"), "time": meal.get("time"), "totals": totals,
                        "issues": issues, "complete": issues == 0 and bool(rows), "items": rows})
    return _summarize(results)


def macro_check(kcal, protein, carbs, fat, tolerance=0.2):
    """A warning when entered calories are far from P x 4 + C x 4 + F x 9
    (labels round, and fibre/alcohol differ, hence the tolerance)."""
    if None in (kcal, protein, carbs, fat) or not kcal:
        return None
    estimate = protein * 4 + carbs * 4 + fat * 9
    if abs(estimate - kcal) > max(tolerance * kcal, 15):
        return (f"Check the values: protein, carbs and fat add up to about {estimate:.0f} kcal "
                f"(P×4 + C×4 + F×9), but calories say {kcal:g}.")
    return None


# --------------------------------------------------------------------------
# Safety checks (flags only -- never prescriptions)
# --------------------------------------------------------------------------

def review_flags(meals, profile=None, age=None, plan_diet_type=None, result=None,
                 foods_by_id=None, foods_by_name=None):
    """Reasons this plan should be checked by a qualified professional
    before the member follows it. Returns a list of plain-language strings."""
    from gym_ops import diet_starter_plans as sp  # (only change from v1.2.0: import path)

    flags = []
    profile = profile or {}
    allergy_text = profile.get("food_allergies")
    if allergy_text:
        tags = sp._allergy_tags(allergy_text)
        hits = {}
        for meal in meals or []:
            for item in meal.get("items") or []:
                food = _food_for(item, foods_by_id or {}, foods_by_name or {})
                item_tags = set(item.get("allergens") or "") | set((food or {}).get("allergens") or "")
                for tag in item_tags & tags:
                    name = item.get("food_name") or item.get("food")
                    hits.setdefault(sp.ALLERGENS[tag], [])
                    if name not in hits[sp.ALLERGENS[tag]]:
                        hits[sp.ALLERGENS[tag]].append(name)
        for label, names in hits.items():
            flags.append(f"Allergy: the member's profile lists {label}; this plan has "
                         f"{', '.join(names)}.")
    member_diet = profile.get("diet_type")
    if plan_diet_type and member_diet:
        allowed = sp.DIET_COMPATIBLE.get(member_diet)
        if allowed is not None and plan_diet_type not in allowed:
            flags.append(f"Diet type: this is a {plan_diet_type} plan; the member's profile "
                         f"says {member_diet}.")
    try:
        if age not in (None, "") and int(age) < MIN_ADULT_AGE:
            flags.append(f"Age: the member is under {MIN_ADULT_AGE}.")
    except (TypeError, ValueError):
        pass
    if result and result["complete"] and result["day"]["kcal"] < LOW_KCAL_REVIEW:
        flags.append(f"Calories: the day adds up to {result['day']['kcal']:.0f} kcal, "
                     f"below {LOW_KCAL_REVIEW} kcal.")
    return flags
