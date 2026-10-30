# Copied unchanged from FitDesk Pro v1.2.0 (GymERP/services/diet_starter_plans.py.py) so the
# server calculates diet nutrition exactly like the desktop app.

"""Built-in Starter Plans for the Diet Planner.

16 ready-made plans -- one for every goal x diet type:

    goals:      Weight Loss, Muscle Gain, Maintenance, General Fitness
    diet types: Vegetarian, Eggetarian, Non-vegetarian, Vegan

Each plan is meals -> foods with HOUSEHOLD PORTIONS only. There are
deliberately NO calorie/macro numbers here: nutrition totals are
calculated later from the gym's own food library values (manual entry),
and show as "not calculated" until those values exist. Nothing is
estimated.

Each food carries allergen tags so a plan can be checked against a
member's recorded allergies before it is assigned:

    D dairy   G gluten   P peanuts   E egg   F fish   S soy   N tree nuts

Starter plans are read-only. Assigning gives the member a separate copy;
editing saves the gym's own copy ("My Templates"). The built-in plan
itself never changes. This is general guidance, not a medical diet.
"""

import re

GOALS = ["Weight Loss", "Muscle Gain", "Maintenance", "General Fitness"]
DIET_TYPES = ["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan"]

ALLERGENS = {
    "D": "Dairy", "G": "Gluten", "P": "Peanuts", "E": "Egg",
    "F": "Fish", "S": "Soy", "N": "Tree nuts",
}

# Words in a member's free-text "Food allergies" that map to each tag.
ALLERGY_KEYWORDS = {
    "D": ("dairy", "milk", "lactose", "paneer", "curd", "cheese", "casein"),
    "G": ("gluten", "wheat", "celiac", "coeliac", "maida"),
    "P": ("peanut", "groundnut"),
    "E": ("egg",),
    "F": ("fish", "seafood"),
    "S": ("soy", "soya", "tofu"),
    "N": ("tree nut", "almond", "cashew", "walnut", "pistachio"),
}

# What each member diet type can eat (by plan diet type).
DIET_COMPATIBLE = {
    "Vegan": {"Vegan"},
    "Vegetarian": {"Vegetarian", "Vegan"},
    "Eggetarian": {"Vegetarian", "Eggetarian", "Vegan"},
    "Non-vegetarian": set(DIET_TYPES),
}

GOAL_INFO = {
    "Weight Loss": {
        "weeks": 4,
        "note": "Smaller portions, high fibre, protein at every meal",
        "instructions": (
            "Drink 2.5-3 L water through the day. Use minimal oil and no added "
            "sugar. Keep dinner at least 2 hours before sleep. Swap the fruit for "
            "any seasonal fruit of a similar size."
        ),
    },
    "Muscle Gain": {
        "weeks": 8,
        "note": "Larger portions, protein spread across the day",
        "instructions": (
            "Eat every 2.5-3 hours and do not skip the post-workout meal. Drink "
            "3-3.5 L water daily. Increase portions gradually if weight does not "
            "move after two weeks; discuss changes with your trainer."
        ),
    },
    "Maintenance": {
        "weeks": 4,
        "note": "Balanced plate, regular timings",
        "instructions": (
            "Keep meal timings regular. Fill half the plate with vegetables at "
            "lunch and dinner. Drink 2.5-3 L water daily and limit fried and "
            "packaged snacks."
        ),
    },
    "General Fitness": {
        "weeks": 4,
        "note": "Simple everyday eating for active members",
        "instructions": (
            "Eat home-style meals at regular times. Include a protein source and "
            "vegetables in every main meal. Drink 2.5-3 L water daily, more on "
            "workout days."
        ),
    },
}

GENERAL_GUIDANCE = (
    "General guidance only. Members with medical conditions, pregnancy or "
    "special dietary needs should consult a qualified professional."
)

# --------------------------------------------------------------------------
# Plan content: goal -> diet type -> [(meal, "HH:MM", [(food, portion, tags)])]
# --------------------------------------------------------------------------

_WL = {
    "Vegetarian": [
        ("Breakfast", "08:00", [("Vegetable poha", "1 bowl (150 g)", "P"),
                                ("Tea, no sugar", "1 cup", "D")]),
        ("Mid-morning", "11:00", [("Seasonal fruit", "1 medium", ""),
                                  ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Mixed veg sabzi", "1 katori (150 g)", ""),
                            ("Cucumber-tomato salad", "1 plate", ""),
                            ("Curd", "1 katori (100 g)", "D")]),
        ("Evening Snack", "17:00", [("Roasted chana", "30 g", ""),
                                    ("Green tea", "1 cup", "")]),
        ("Dinner", "20:30", [("Paneer bhurji", "100 g", "D"), ("Phulka", "1 pc", "G"),
                             ("Sauteed vegetables", "1 katori (150 g)", "")]),
    ],
    "Eggetarian": [
        ("Breakfast", "08:00", [("Vegetable omelette", "2 eggs", "E"),
                                ("Whole-wheat toast", "1 slice", "G")]),
        ("Mid-morning", "11:00", [("Seasonal fruit", "1 medium", ""),
                                  ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Mixed veg sabzi", "1 katori (150 g)", ""),
                            ("Cucumber-tomato salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Boiled egg whites", "2", "E"),
                                    ("Green tea", "1 cup", "")]),
        ("Dinner", "20:30", [("Egg bhurji", "2 eggs", "E"), ("Phulka", "1 pc", "G"),
                             ("Sauteed vegetables", "1 katori (150 g)", "")]),
    ],
    "Non-vegetarian": [
        ("Breakfast", "08:00", [("Boiled eggs", "2", "E"),
                                ("Vegetable upma", "1 bowl (150 g)", "G")]),
        ("Mid-morning", "11:00", [("Seasonal fruit", "1 medium", ""),
                                  ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Lunch", "13:30", [("Brown rice, cooked", "1 katori (100 g)", ""),
                            ("Grilled chicken", "100 g", ""),
                            ("Mixed veg sabzi", "1 katori (150 g)", ""),
                            ("Cucumber-tomato salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Sprouts chaat", "1 bowl (100 g)", ""),
                                    ("Green tea", "1 cup", "")]),
        ("Dinner", "20:30", [("Fish curry", "100 g fish", "F"), ("Phulka", "1 pc", "G"),
                             ("Sauteed vegetables", "1 katori (150 g)", "")]),
    ],
    "Vegan": [
        ("Breakfast", "08:00", [("Besan chilla", "2 pcs", ""),
                                ("Black tea or green tea", "1 cup", "")]),
        ("Mid-morning", "11:00", [("Seasonal fruit", "1 medium", ""),
                                  ("Coconut water", "1 glass (200 ml)", "")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Mixed veg sabzi (no ghee)", "1 katori (150 g)", ""),
                            ("Cucumber-tomato salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Roasted chana", "30 g", ""),
                                    ("Green tea", "1 cup", "")]),
        ("Dinner", "20:30", [("Tofu stir-fry", "100 g tofu", "S"),
                             ("Brown rice, cooked", "1/2 katori (50 g)", ""),
                             ("Sauteed vegetables", "1 katori (150 g)", "")]),
    ],
}

_MG = {
    "Vegetarian": [
        ("Breakfast", "07:30", [("Paneer paratha", "2 pcs", "GD"),
                                ("Curd", "1 katori (100 g)", "D")]),
        ("Mid-morning", "10:30", [("Banana", "2", ""), ("Milk", "1 glass (250 ml)", "D"),
                                  ("Almonds", "10 pcs", "N")]),
        ("Lunch", "13:30", [("Rice, cooked", "1.5 katori (150 g)", ""),
                            ("Rajma", "1 katori (150 g)", ""), ("Phulka", "2 pcs", "G"),
                            ("Salad", "1 plate", "")]),
        ("Pre-workout", "16:30", [("Peanut butter toast", "2 slices", "PG")]),
        ("Post-workout", "18:30", [("Soya chunks curry", "1 katori (150 g)", "S"),
                                   ("Curd", "1 katori (100 g)", "D")]),
        ("Dinner", "21:00", [("Phulka", "3 pcs", "G"), ("Paneer curry", "100 g paneer", "D"),
                             ("Dal", "1 katori (150 g)", "")]),
    ],
    "Eggetarian": [
        ("Breakfast", "07:30", [("Omelette", "3 eggs", "E"),
                                ("Whole-wheat toast", "2 slices", "G"),
                                ("Milk", "1 glass (250 ml)", "D")]),
        ("Mid-morning", "10:30", [("Banana", "2", ""), ("Almonds", "10 pcs", "N")]),
        ("Lunch", "13:30", [("Rice, cooked", "1.5 katori (150 g)", ""),
                            ("Rajma", "1 katori (150 g)", ""), ("Phulka", "2 pcs", "G"),
                            ("Salad", "1 plate", "")]),
        ("Pre-workout", "16:30", [("Banana", "1", ""),
                                  ("Peanut butter toast", "1 slice", "PG")]),
        ("Post-workout", "18:30", [("Boiled eggs", "3", "E")]),
        ("Dinner", "21:00", [("Phulka", "3 pcs", "G"), ("Egg curry", "2 eggs", "E"),
                             ("Dal", "1 katori (150 g)", "")]),
    ],
    "Non-vegetarian": [
        ("Breakfast", "07:30", [("Boiled eggs", "3", "E"),
                                ("Oats with milk", "1 bowl (50 g oats)", "D"),
                                ("Banana", "1", "")]),
        ("Mid-morning", "10:30", [("Milk", "1 glass (250 ml)", "D"),
                                  ("Almonds", "10 pcs", "N")]),
        ("Lunch", "13:30", [("Rice, cooked", "1.5 katori (150 g)", ""),
                            ("Chicken curry", "150 g chicken", ""),
                            ("Dal", "1 katori (150 g)", ""), ("Salad", "1 plate", "")]),
        ("Pre-workout", "16:30", [("Banana", "1", ""),
                                  ("Peanut butter toast", "1 slice", "PG")]),
        ("Post-workout", "18:30", [("Grilled chicken", "100 g", "")]),
        ("Dinner", "21:00", [("Phulka", "3 pcs", "G"), ("Fish curry", "150 g fish", "F"),
                             ("Sauteed vegetables", "1 katori (150 g)", "")]),
    ],
    "Vegan": [
        ("Breakfast", "07:30", [("Besan chilla", "3 pcs", ""),
                                ("Soy milk", "1 glass (250 ml)", "S")]),
        ("Mid-morning", "10:30", [("Banana", "2", ""),
                                  ("Roasted peanuts", "30 g", "P")]),
        ("Lunch", "13:30", [("Rice, cooked", "1.5 katori (150 g)", ""),
                            ("Rajma", "1.5 katori (225 g)", ""), ("Salad", "1 plate", "")]),
        ("Pre-workout", "16:30", [("Dates", "3 pcs", ""),
                                  ("Peanut butter toast (vegan bread)", "1 slice", "PG")]),
        ("Post-workout", "18:30", [("Soya chunks curry", "1 katori (150 g)", "S")]),
        ("Dinner", "21:00", [("Phulka", "3 pcs", "G"), ("Tofu bhurji", "150 g", "S"),
                             ("Dal", "1 katori (150 g)", "")]),
    ],
}

_MT = {
    "Vegetarian": [
        ("Breakfast", "08:00", [("Idli", "3 pcs", ""), ("Sambar", "1 katori (150 g)", ""),
                                ("Coconut chutney", "2 tbsp", "")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi", "1 katori (150 g)", ""),
                            ("Curd", "1 katori (100 g)", "D"), ("Salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Seasonal fruit", "1 medium", ""),
                                    ("Tea", "1 cup", "D")]),
        ("Dinner", "20:30", [("Vegetable khichdi", "1.5 katori (225 g)", ""),
                             ("Curd", "1 katori (100 g)", "D")]),
    ],
    "Eggetarian": [
        ("Breakfast", "08:00", [("Masala omelette", "2 eggs", "E"),
                                ("Whole-wheat toast", "2 slices", "G"), ("Tea", "1 cup", "D")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi", "1 katori (150 g)", ""),
                            ("Curd", "1 katori (100 g)", "D"), ("Salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Seasonal fruit", "1 medium", ""),
                                    ("Boiled egg", "1", "E")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Egg curry", "2 eggs", "E"),
                             ("Seasonal sabzi", "1 katori (150 g)", "")]),
    ],
    "Non-vegetarian": [
        ("Breakfast", "08:00", [("Idli", "3 pcs", ""), ("Sambar", "1 katori (150 g)", ""),
                                ("Boiled eggs", "2", "E")]),
        ("Lunch", "13:30", [("Rice, cooked", "1 katori (100 g)", ""),
                            ("Chicken curry", "100 g chicken", ""),
                            ("Dal", "1 katori (150 g)", ""), ("Salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Seasonal fruit", "1 medium", ""),
                                    ("Tea", "1 cup", "D")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Fish curry", "100 g fish", "F"),
                             ("Seasonal sabzi", "1 katori (150 g)", "")]),
    ],
    "Vegan": [
        ("Breakfast", "08:00", [("Idli", "3 pcs", ""), ("Sambar", "1 katori (150 g)", ""),
                                ("Coconut chutney", "2 tbsp", "")]),
        ("Lunch", "13:30", [("Phulka", "2 pcs", "G"), ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi (no ghee)", "1 katori (150 g)", ""),
                            ("Salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Seasonal fruit", "1 medium", ""),
                                    ("Roasted chana", "30 g", "")]),
        ("Dinner", "20:30", [("Vegetable khichdi", "1.5 katori (225 g)", ""),
                             ("Tofu stir-fry", "75 g tofu", "S")]),
    ],
}

_GF = {
    "Vegetarian": [
        ("Breakfast", "08:00", [("Vegetable upma", "1 bowl (150 g)", "G"),
                                ("Milk", "1 glass (200 ml)", "D")]),
        ("Lunch", "13:30", [("Rice, cooked", "1 katori (100 g)", ""),
                            ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi", "1 katori (150 g)", ""),
                            ("Curd", "1 katori (100 g)", "D")]),
        ("Evening Snack", "17:00", [("Sprouts chaat", "1 bowl (100 g)", ""),
                                    ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Paneer sabzi", "75 g paneer", "D"),
                             ("Salad", "1 plate", "")]),
    ],
    "Eggetarian": [
        ("Breakfast", "08:00", [("Boiled eggs", "2", "E"),
                                ("Vegetable upma", "1 bowl (150 g)", "G")]),
        ("Lunch", "13:30", [("Rice, cooked", "1 katori (100 g)", ""),
                            ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi", "1 katori (150 g)", ""),
                            ("Curd", "1 katori (100 g)", "D")]),
        ("Evening Snack", "17:00", [("Sprouts chaat", "1 bowl (100 g)", ""),
                                    ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Egg bhurji", "2 eggs", "E"),
                             ("Salad", "1 plate", "")]),
    ],
    "Non-vegetarian": [
        ("Breakfast", "08:00", [("Boiled eggs", "2", "E"),
                                ("Vegetable poha", "1 bowl (150 g)", "P")]),
        ("Lunch", "13:30", [("Rice, cooked", "1 katori (100 g)", ""),
                            ("Chicken curry", "100 g chicken", ""),
                            ("Seasonal sabzi", "1 katori (150 g)", ""),
                            ("Curd", "1 katori (100 g)", "D")]),
        ("Evening Snack", "17:00", [("Seasonal fruit", "1 medium", ""),
                                    ("Buttermilk", "1 glass (200 ml)", "D")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Grilled fish", "100 g", "F"),
                             ("Salad", "1 plate", "")]),
    ],
    "Vegan": [
        ("Breakfast", "08:00", [("Vegetable upma", "1 bowl (150 g)", "G"),
                                ("Soy milk", "1 glass (200 ml)", "S")]),
        ("Lunch", "13:30", [("Rice, cooked", "1 katori (100 g)", ""),
                            ("Dal", "1 katori (150 g)", ""),
                            ("Seasonal sabzi (no ghee)", "1 katori (150 g)", ""),
                            ("Salad", "1 plate", "")]),
        ("Evening Snack", "17:00", [("Sprouts chaat", "1 bowl (100 g)", ""),
                                    ("Coconut water", "1 glass (200 ml)", "")]),
        ("Dinner", "20:30", [("Phulka", "2 pcs", "G"), ("Chana masala", "1 katori (150 g)", ""),
                             ("Salad", "1 plate", "")]),
    ],
}

_CONTENT = {"Weight Loss": _WL, "Muscle Gain": _MG, "Maintenance": _MT, "General Fitness": _GF}


def _slug(text):
    return "".join(c if c.isalnum() else "_" for c in text.lower()).strip("_")


def _build():
    plans = []
    for goal in GOALS:
        for diet in DIET_TYPES:
            meals = [
                {"name": name, "time": time,
                 "items": [{"food": f, "portion": p, "allergens": tuple(tags)} for f, p, tags in foods]}
                for name, time, foods in _CONTENT[goal][diet]
            ]
            plans.append({
                "key": f"starter_{_slug(goal)}_{_slug(diet)}",
                "name": f"{goal} — {diet}",
                "goal": goal,
                "diet_type": diet,
                "meals_per_day": len(meals),
                "weeks": GOAL_INFO[goal]["weeks"],
                "note": GOAL_INFO[goal]["note"],
                "instructions": GOAL_INFO[goal]["instructions"],
                "meals": meals,
                "is_starter": True,
            })
    return plans


STARTER_PLANS = _build()
_BY_KEY = {p["key"]: p for p in STARTER_PLANS}


# --------------------------------------------------------------------------
# Lookups / helpers
# --------------------------------------------------------------------------

def list_starter_plans(goal=None, diet_type=None):
    return [p for p in STARTER_PLANS
            if (goal is None or p["goal"] == goal) and (diet_type is None or p["diet_type"] == diet_type)]


def get_starter_plan(key):
    return _BY_KEY.get(key)


def _food_name(item):
    """Starter items use "food"; saved templates/plans use "food_name"."""
    return item.get("food") or item.get("food_name") or ""


def food_count(plan):
    return sum(len(m["items"]) for m in plan["meals"])


def plan_allergens(plan):
    """{tag: [food, ...]} for every allergen the plan contains, in ALLERGENS order."""
    found = {}
    for meal in plan["meals"]:
        for item in meal["items"]:
            food = _food_name(item)
            for tag in item.get("allergens") or "":
                foods = found.setdefault(tag, [])
                if food not in foods:
                    foods.append(food)
    return {tag: found[tag] for tag in ALLERGENS if tag in found}


def summary_line(plan, limit=5):
    """A short "what's in it" line for template cards: main foods, no drinks."""
    skip = ("tea", "coffee", "water", "buttermilk")
    names = []
    for meal in plan["meals"]:
        for item in meal["items"]:
            name = _food_name(item).split(",")[0].split(" (")[0]
            if any(name.lower().startswith(s) or name.lower().endswith(s) for s in skip):
                continue
            if name not in names:
                names.append(name)
    return " · ".join(names[:limit])


def _allergy_tags(allergy_text):
    """Allergen tags named in free text. Whole words only, so "veggies"
    is not "egg"; plurals ("eggs", "peanuts") still match."""
    text = (allergy_text or "").lower()
    if not text.strip():
        return set()

    def has(word):
        return re.search(rf"\b{re.escape(word)}(s|es)?\b", text) is not None

    tags = {tag for tag, words in ALLERGY_KEYWORDS.items() if any(has(w) for w in words)}
    if re.search(r"(?<!tree )\bnuts?\b", text):   # bare "nut(s)" = peanuts and tree nuts
        tags |= {"P", "N"}
    return tags


def allergy_conflicts(plan, allergy_text):
    """[(allergen label, [foods])] where the member's recorded allergies
    match foods in the plan. Free-text matching: a check, not a guarantee."""
    contained = plan_allergens(plan)
    return [(ALLERGENS[tag], contained[tag])
            for tag in ALLERGENS if tag in contained and tag in _allergy_tags(allergy_text)]


def diet_conflict(plan, member_diet_type):
    """A message when the plan's diet type does not suit the member's, else None."""
    allowed = DIET_COMPATIBLE.get((member_diet_type or "").strip())
    if allowed is None or plan["diet_type"] in allowed:
        return None
    return (f"This is a {plan['diet_type']} plan, but the member's profile says "
            f"{member_diet_type}.")
