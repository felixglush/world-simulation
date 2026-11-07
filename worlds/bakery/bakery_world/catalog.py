from bakery_world.basics import Category, Cents, Ingredient, ItemId
from bakery_world.model import Recipe

F, B, E, S, D, C, H, R = (
    Ingredient.FLOUR,
    Ingredient.BUTTER,
    Ingredient.EGGS,
    Ingredient.SUGAR,
    Ingredient.DAIRY,
    Ingredient.COFFEE,
    Ingredient.CHOCOLATE,
    Ingredient.FRUIT,
)
BREAD, PASTRY, CAKE, DRINK, SAVORY = (
    Category.BREAD,
    Category.PASTRY,
    Category.CAKE,
    Category.DRINK,
    Category.SAVORY,
)

# item id, name, category, grams, labor min/unit, oven min/batch, batch size, shelf days, price
_ROWS = [
    ("sourdough", "Sourdough loaf", BREAD, {F: 500}, 4.0, 45, 6, 2, 750),
    ("baguette", "Baguette", BREAD, {F: 250}, 1.5, 25, 10, 1, 400),
    ("rye", "Rye loaf", BREAD, {F: 450, S: 10}, 4.0, 50, 6, 2, 800),
    ("wholewheat", "Whole wheat loaf", BREAD, {F: 480}, 3.5, 45, 6, 2, 700),
    ("focaccia", "Focaccia slab", BREAD, {F: 300}, 3.0, 30, 6, 1, 650),
    ("brioche", "Brioche loaf", BREAD, {F: 300, B: 80, E: 60, S: 30}, 4.0, 40, 4, 3, 900),
    ("croissant", "Croissant", PASTRY, {F: 60, B: 35}, 1.5, 20, 12, 1, 375),
    ("painchoc", "Pain au chocolat", PASTRY, {F: 60, B: 35, H: 20}, 1.5, 20, 12, 1, 425),
    ("cinnamon", "Cinnamon roll", PASTRY, {F: 80, B: 20, S: 25, D: 20}, 1.5, 25, 12, 1, 450),
    ("danish", "Fruit danish", PASTRY, {F: 60, B: 30, R: 40, S: 10}, 1.5, 22, 12, 1, 425),
    ("scone", "Scone", PASTRY, {F: 70, B: 30, D: 25, S: 10, R: 15}, 1.0, 18, 12, 2, 350),
    ("muffin", "Muffin", PASTRY, {F: 60, S: 30, E: 25, D: 40, R: 20}, 0.8, 22, 12, 2, 350),
    ("cookie", "Chocolate cookie", PASTRY, {F: 40, B: 25, S: 30, H: 25}, 0.5, 12, 24, 3, 275),
    ("eclair", "Eclair", PASTRY, {F: 30, B: 20, E: 40, D: 50, H: 20}, 2.0, 30, 12, 1, 450),
    (
        "choccake",
        "Chocolate cake slice",
        CAKE,
        {F: 50, S: 50, B: 40, E: 40, H: 40},
        2.0,
        40,
        8,
        3,
        550,
    ),
    ("cheesecake", "Cheesecake slice", CAKE, {D: 100, E: 40, S: 40, F: 20}, 2.0, 50, 8, 3, 600),
    ("lemontart", "Lemon tart", CAKE, {F: 50, B: 40, E: 40, S: 40, R: 30}, 2.5, 35, 8, 2, 500),
    (
        "carrotcake",
        "Carrot cake slice",
        CAKE,
        {F: 50, S: 40, E: 40, B: 30, R: 30},
        2.0,
        45,
        8,
        3,
        525,
    ),
    ("espresso", "Espresso", DRINK, {C: 9}, 0, 0, 0, 0, 300),
    ("latte", "Latte", DRINK, {C: 9, D: 200}, 0, 0, 0, 0, 450),
    ("drip", "Drip coffee", DRINK, {C: 14}, 0, 0, 0, 0, 275),
    ("hotchoc", "Hot chocolate", DRINK, {H: 30, D: 200}, 0, 0, 0, 0, 450),
    ("quiche", "Quiche slice", SAVORY, {F: 50, B: 30, E: 80, D: 60}, 2.5, 40, 8, 1, 650),
    ("cheesetwist", "Cheese twist", SAVORY, {F: 60, B: 30, D: 40}, 1.0, 20, 16, 1, 400),
    ("spinachpie", "Spinach pie", SAVORY, {F: 70, B: 25, E: 40, D: 40}, 2.0, 35, 8, 1, 600),
]

# the labor column is single-unit pace; working in batches cuts hands-on time
BATCH_LABOR_FACTOR = 0.6

CATALOG: dict[ItemId, Recipe] = {
    ItemId(i): Recipe(
        ItemId(i), n, c, g, round(lab * BATCH_LABOR_FACTOR, 2), oven, batch, shelf, Cents(p)
    )
    for i, n, c, g, lab, oven, batch, shelf, p in _ROWS
}

STARTING_MENU: tuple[ItemId, ...] = tuple(
    ItemId(i)
    for i in (
        "sourdough baguette croissant painchoc cinnamon danish muffin cookie "
        "choccake espresso latte drip quiche cheesetwist"
    ).split()
)
OPENING_MENU: tuple[ItemId, ...] = tuple(
    i for i in STARTING_MENU if i not in ("cinnamon", "choccake")
)
