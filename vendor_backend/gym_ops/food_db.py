"""Built-in food list: common Indian foods with values for one serving, so
diet plans show calories, protein, carbs and fat from day one.

Values are approximate, from standard food tables (ICMR-NIN Indian Food
Composition Tables 2017, and USDA FoodData Central for packaged/Western
items), for typical home-style recipes. Home cooking varies (oil, ghee,
portion), so a gym can override any food by adding one with the same name
to its own food library, and any item in a plan can be entered by hand.

Each row: name, other names, serving amount, serving unit, kcal,
protein g, carbs g, fat g, allergen tags (D dairy, G gluten, P peanuts,
E egg, F fish, S soy, N tree nuts), note.

Serving units match how the built-in starter plans write portions
("1 katori (150 g)" -> grams, "2 pcs" -> pc, "2 eggs" -> egg, "1 plate").
"""
import re

SOURCE = "Built-in (approx.; ICMR-NIN IFCT 2017 / USDA)"

FOODS = [
    # --- rice, grains --------------------------------------------------------------------------
    ("Rice, cooked", ["rice", "white rice", "steamed rice", "plain rice", "boiled rice"], 100, "g", 130, 2.7, 28, 0.3, "", ""),
    ("Brown rice, cooked", ["brown rice"], 100, "g", 112, 2.6, 23.5, 0.9, "", ""),
    ("Jeera rice", [], 100, "g", 160, 3, 28, 4, "", ""),
    ("Veg pulao", ["vegetable pulao", "pulao"], 100, "g", 150, 3, 25, 4.5, "", ""),
    ("Lemon rice", [], 100, "g", 170, 3, 28, 5, "", ""),
    ("Curd rice", [], 100, "g", 120, 3, 18, 3.5, "D", ""),
    ("Veg biryani", ["vegetable biryani"], 100, "g", 160, 3.5, 25, 5, "", ""),
    ("Chicken biryani", ["biryani"], 100, "g", 180, 9, 20, 7, "", ""),
    ("Vegetable khichdi", ["khichdi", "dal khichdi"], 100, "g", 120, 4, 19, 3, "", ""),
    ("Pongal", ["ven pongal"], 100, "g", 150, 4, 22, 5, "", ""),
    ("Oats, dry", ["oats", "rolled oats"], 100, "g", 380, 13, 66, 7, "G", ""),
    ("Oats with milk", ["oats porridge"], 50, "g", 308, 12.7, 42.4, 9.5, "DG", "50 g oats cooked in 200 ml toned milk"),
    ("Vegetable poha", ["poha", "kanda poha"], 100, "g", 130, 2.5, 22, 3.5, "", ""),
    ("Vegetable upma", ["upma", "rava upma"], 100, "g", 140, 3.5, 21, 4.5, "G", ""),
    ("Sabudana khichdi", [], 100, "g", 190, 2.5, 30, 7, "P", ""),
    # --- breads --------------------------------------------------------------------------------
    ("Phulka", ["roti without oil", "plain roti"], 1, "pc", 75, 2.7, 15, 0.4, "G", "about 30 g, no ghee"),
    ("Chapati", ["roti", "chapatti"], 1, "pc", 100, 3, 18, 2, "G", "about 40 g, a little oil"),
    ("Roti with ghee", ["ghee roti"], 1, "pc", 115, 3, 15, 4.5, "DG", ""),
    ("Plain paratha", ["paratha"], 1, "pc", 180, 4, 24, 7.5, "G", ""),
    ("Aloo paratha", [], 1, "pc", 260, 5.5, 34, 11, "G", ""),
    ("Paneer paratha", [], 1, "pc", 280, 10, 30, 13, "DG", ""),
    ("Naan", [], 1, "pc", 260, 8, 45, 5, "DG", ""),
    ("Jowar bhakri", ["jowar roti", "jowar bhakri"], 1, "pc", 110, 3.2, 22, 1, "", ""),
    ("Bajra bhakri", ["bajra roti"], 1, "pc", 120, 3.5, 21, 2.2, "", ""),
    ("Whole-wheat toast", ["brown bread", "whole wheat bread", "wheat bread", "brown bread toast"], 1, "slice", 75, 3.5, 13, 1, "G", ""),
    ("White bread", ["bread", "white bread toast"], 1, "slice", 70, 2.3, 13, 0.9, "G", ""),
    ("Peanut butter toast", ["peanut butter toast (vegan bread)", "pb toast"], 1, "slice", 170, 7, 15, 9, "PG", "1 slice + 1 tbsp peanut butter"),
    # --- breakfast / South Indian / snacks -------------------------------------------------------
    ("Idli", [], 1, "pc", 58, 2, 12, 0.2, "", "about 40 g"),
    ("Plain dosa", ["dosa"], 1, "pc", 133, 3, 20, 4.5, "", ""),
    ("Masala dosa", [], 1, "pc", 280, 6, 40, 10.5, "", ""),
    ("Uttapam", [], 1, "pc", 200, 5, 30, 6.5, "", ""),
    ("Sambar", [], 100, "g", 65, 3, 9, 2, "", ""),
    ("Coconut chutney", [], 1, "tbsp", 35, 0.5, 1.5, 3, "", ""),
    ("Besan chilla", ["besan cheela", "besan chila"], 1, "pc", 120, 5.5, 13, 5, "", ""),
    ("Moong dal chilla", ["moong chilla", "moong dal cheela"], 1, "pc", 110, 6, 14, 3.5, "", ""),
    ("Dhokla", ["khaman dhokla"], 1, "pc", 50, 2, 8, 1, "", "about 30 g"),
    ("Sprouts chaat", ["sprouts", "moong sprouts", "sprouts salad"], 100, "g", 90, 6, 14, 1.5, "", ""),
    ("Roasted chana", ["roasted gram", "chana"], 100, "g", 370, 22, 58, 5, "", ""),
    ("Roasted peanuts", ["peanuts"], 100, "g", 585, 24, 21, 49, "P", ""),
    ("Makhana, roasted", ["makhana", "fox nuts", "lotus seeds"], 100, "g", 350, 9.7, 76, 0.1, "", ""),
    ("Samosa", [], 1, "pc", 260, 4, 28, 15, "G", ""),
    ("Vada pav", [], 1, "pc", 290, 6, 40, 12, "G", ""),
    ("Pav bhaji", [], 1, "plate", 400, 10, 55, 16, "DG", "2 pav + bhaji"),
    ("Marie biscuit", ["biscuit"], 1, "pc", 22, 0.4, 3.8, 0.6, "G", ""),
    # --- dals, curries (home style) ---------------------------------------------------------------
    ("Dal", ["toor dal", "moong dal", "yellow dal", "dal fry"], 100, "g", 105, 6, 15, 2.5, "", "cooked, little oil"),
    ("Dal tadka", [], 100, "g", 130, 6, 15, 5, "D", ""),
    ("Dal makhani", [], 100, "g", 160, 6, 15, 8.5, "D", ""),
    ("Rajma", ["rajma curry", "rajma masala"], 100, "g", 125, 6, 17, 3.5, "", ""),
    ("Chana masala", ["chole", "chickpea curry"], 100, "g", 150, 7, 19, 5, "", ""),
    ("Kadhi", ["kadhi pakora"], 100, "g", 90, 3, 7, 5.5, "D", ""),
    ("Mixed veg sabzi", ["mix veg", "seasonal sabzi", "sabzi", "vegetable sabzi", "veg curry"], 100, "g", 85, 2, 9, 4.5, "", ""),
    ("Mixed veg sabzi (no ghee)", ["seasonal sabzi (no ghee)", "sabzi (no ghee)", "boiled vegetables"], 100, "g", 55, 2, 9, 1.3, "", ""),
    ("Aloo gobi", [], 100, "g", 110, 2.5, 12, 6, "", ""),
    ("Sauteed vegetables", ["stir-fried vegetables", "stir fry vegetables"], 100, "g", 70, 2, 8, 3.5, "", ""),
    ("Palak paneer", [], 100, "g", 140, 7, 6, 10, "D", ""),
    ("Paneer butter masala", [], 100, "g", 220, 8, 8, 18, "D", ""),
    ("Paneer", ["raw paneer", "cottage cheese"], 100, "g", 265, 18, 3.6, 20.8, "D", ""),
    ("Paneer bhurji", [], 100, "g", 250, 15, 5, 19, "D", ""),
    ("Paneer curry", [], 100, "g", 330, 19, 9, 24, "D", "per 100 g paneer, with gravy"),
    ("Paneer sabzi", [], 100, "g", 320, 19, 8, 23.5, "D", "per 100 g paneer, with vegetables"),
    ("Soya chunks, dry", ["soya chunks", "soya nuggets"], 100, "g", 345, 52, 33, 0.5, "S", ""),
    ("Soya chunks curry", [], 100, "g", 120, 10, 9, 5, "S", ""),
    ("Tofu", [], 100, "g", 76, 8, 1.9, 4.8, "S", ""),
    ("Tofu bhurji", [], 100, "g", 130, 10, 4, 8.5, "S", ""),
    ("Tofu stir-fry", [], 100, "g", 160, 11, 8, 9.5, "S", "per 100 g tofu, with vegetables"),
    # --- eggs, meat, fish -------------------------------------------------------------------------
    ("Boiled egg", ["boiled eggs", "egg", "eggs", "whole egg"], 1, "pc", 78, 6.3, 0.6, 5.3, "E", ""),
    ("Boiled egg whites", ["egg white", "egg whites"], 1, "pc", 17, 3.6, 0.2, 0.1, "E", "white of 1 egg"),
    ("Omelette", ["plain omelette"], 1, "egg", 95, 6.5, 0.6, 7.3, "E", "per egg, with oil"),
    ("Masala omelette", ["vegetable omelette", "veg omelette"], 1, "egg", 105, 6.7, 1.5, 8, "E", "per egg, with oil"),
    ("Egg bhurji", ["scrambled eggs"], 1, "egg", 110, 6.8, 2, 8.2, "E", "per egg"),
    ("Egg curry", [], 1, "egg", 150, 7.5, 4.5, 11.5, "E", "per egg, with gravy"),
    ("Grilled chicken", ["chicken breast", "chicken breast grilled", "tandoori chicken"], 100, "g", 165, 31, 0, 3.6, "", ""),
    ("Chicken curry", [], 100, "g", 240, 25, 6, 13, "", "per 100 g chicken, with gravy"),
    ("Grilled fish", ["fish"], 100, "g", 140, 22, 0, 5.5, "F", ""),
    ("Fish curry", [], 100, "g", 190, 20, 5, 10, "F", "per 100 g fish, with gravy"),
    ("Mutton curry", [], 100, "g", 230, 18, 4, 16, "", ""),
    ("Prawn curry", ["prawns", "prawns curry"], 100, "g", 150, 16, 5, 7, "F", ""),
    # --- dairy, drinks ----------------------------------------------------------------------------------
    ("Milk", ["toned milk"], 100, "ml", 59, 3.1, 4.7, 3, "D", "toned milk"),
    ("Skimmed milk", ["skim milk"], 100, "ml", 35, 3.4, 5, 0.1, "D", ""),
    ("Full-cream milk", ["whole milk"], 100, "ml", 62, 3.2, 4.8, 3.3, "D", ""),
    ("Soy milk", ["soya milk"], 100, "ml", 43, 3.3, 2.8, 1.8, "S", "unsweetened"),
    ("Curd", ["dahi", "yogurt", "plain curd"], 100, "g", 60, 3.1, 4.7, 3.1, "D", ""),
    ("Greek yogurt", ["hung curd"], 100, "g", 73, 10, 3.6, 2, "D", "low fat"),
    ("Buttermilk", ["chaas", "chhaas", "mattha"], 100, "ml", 20, 1.2, 2, 0.8, "D", ""),
    ("Sweet lassi", ["lassi"], 100, "ml", 90, 3, 14, 2.5, "D", ""),
    ("Tea", ["chai", "masala chai", "tea with sugar"], 1, "cup", 60, 1.5, 8.5, 2, "D", "150 ml, milk + 1 tsp sugar"),
    ("Tea, no sugar", ["tea without sugar", "chai without sugar"], 1, "cup", 30, 1.5, 2.5, 1.5, "D", "150 ml, with milk"),
    ("Black tea or green tea", ["green tea", "black tea", "lemon tea"], 1, "cup", 2, 0, 0.5, 0, "", "no sugar"),
    ("Coffee with milk", ["coffee", "filter coffee"], 1, "cup", 70, 2, 9, 2.5, "D", "with sugar"),
    ("Black coffee", [], 1, "cup", 2, 0.3, 0, 0, "", "no sugar"),
    ("Coconut water", ["tender coconut water"], 100, "ml", 19, 0.7, 3.7, 0.2, "", ""),
    ("Whey protein", ["whey", "protein shake", "whey protein (water)"], 1, "scoop", 120, 24, 3, 1.5, "D", "30 g scoop, in water"),
    # --- fruit, nuts -------------------------------------------------------------------------------
    ("Seasonal fruit", ["fruit", "seasonal fruits", "fruits"], 1, "medium", 80, 1, 19, 0.3, "", "average medium fruit"),
    ("Banana", [], 1, "pc", 105, 1.3, 27, 0.4, "", "medium"),
    ("Apple", [], 1, "pc", 95, 0.5, 25, 0.3, "", "medium"),
    ("Orange", [], 1, "pc", 62, 1.2, 15.4, 0.2, "", "medium"),
    ("Papaya", [], 100, "g", 43, 0.5, 11, 0.3, "", ""),
    ("Guava", [], 100, "g", 68, 2.6, 14, 1, "", ""),
    ("Watermelon", [], 100, "g", 30, 0.6, 7.6, 0.2, "", ""),
    ("Pomegranate", [], 100, "g", 83, 1.7, 18.7, 1.2, "", ""),
    ("Grapes", [], 100, "g", 69, 0.7, 18, 0.2, "", ""),
    ("Mango", [], 100, "g", 60, 0.8, 15, 0.4, "", ""),
    ("Dates", ["khajur"], 1, "pc", 23, 0.2, 6, 0, "", "about 8 g"),
    ("Almonds", ["badam"], 1, "pc", 7, 0.25, 0.25, 0.6, "N", ""),
    ("Cashews", ["kaju"], 1, "pc", 9, 0.3, 0.5, 0.7, "N", ""),
    ("Walnuts", ["akhrot"], 100, "g", 654, 15, 14, 65, "N", ""),
    ("Raisins", ["kishmish"], 100, "g", 299, 3.1, 79, 0.5, "", ""),
    ("Peanut butter", [], 1, "tbsp", 94, 4, 3.2, 8, "P", "16 g"),
    # --- vegetables, salad -------------------------------------------------------------------------------
    ("Cucumber-tomato salad", ["salad", "green salad", "kachumber", "cucumber salad"], 1, "plate", 30, 1.3, 6, 0.3, "", "about 150 g, no dressing"),
    ("Boiled potato", ["potato"], 100, "g", 87, 1.9, 20, 0.1, "", ""),
    ("Sweet potato, boiled", ["sweet potato", "shakarkand"], 100, "g", 86, 1.6, 20, 0.1, "", ""),
    # --- fats, sweeteners ---------------------------------------------------------------------------------
    ("Ghee", [], 1, "tsp", 45, 0, 0, 5, "D", "5 g"),
    ("Butter", [], 1, "tsp", 36, 0, 0, 4, "D", "5 g"),
    ("Cooking oil", ["oil"], 1, "tsp", 45, 0, 0, 5, "", "5 ml"),
    ("Sugar", [], 1, "tsp", 20, 0, 5, 0, "", "5 g"),
    ("Honey", [], 1, "tsp", 21, 0, 5.7, 0, "", "7 g"),
    ("Jaggery", ["gur"], 1, "tsp", 19, 0, 4.9, 0, "", "5 g"),
]


def norm(name):
    """'Tea, no sugar' and 'tea no-sugar' match; case and punctuation don't matter."""
    return re.sub(r"[^a-z0-9]+", " ", str(name or "").lower()).strip()


def _slug(name):
    return norm(name).replace(" ", "-")


def builtin_foods():
    out = []
    for name, aliases, qty, unit, kcal, p, c, f, allergens, note in FOODS:
        out.append({"id": f"std:{_slug(name)}", "name": name, "aliases": aliases, "serving_qty": qty,
                    "serving_unit": unit, "kcal": kcal, "protein_g": p, "carbs_g": c, "fat_g": f,
                    "allergens": allergens, "source": "builtin", "source_note": note or SOURCE,
                    "is_active": True, "builtin": True})
    return out
