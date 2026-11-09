import random

from bakery_world.basics import Ingredient, NeighborhoodId
from bakery_world.catalog import CATALOG, STARTING_MENU
from bakery_world.exogenous import (
    BASE_CENTS_PER_KG,
    MeanRevertingPrices,
    SeasonalFootTraffic,
    SeasonalWeather,
    SpikeWindow,
    TrafficShock,
)
from bakery_world.params import ELASTICITY, SCALARS, TASTE, draw_hidden_params


def test_weather_is_independent_of_query_order():
    a, b = SeasonalWeather(3), SeasonalWeather(3)
    forward = [a.on(d) for d in range(0, 400, 7)]
    backward = [b.on(d) for d in reversed(range(0, 400, 7))][::-1]
    assert forward == backward


def test_weather_is_warmer_in_summer_than_winter():
    w = SeasonalWeather(1)
    summer = sum(w.on(d)[0] for d in range(170, 200)) / 30
    winter = sum(w.on(d)[0] for d in range(352, 365)) / 13
    assert summer > winter + 8


def test_prices_start_near_base_and_stay_positive():
    p = MeanRevertingPrices(5, horizon_days=800)
    for day in (0, 400, 799):
        for ing, cents in p.on(day).items():
            assert 0.4 * BASE_CENTS_PER_KG[ing] < cents < 2.5 * BASE_CENTS_PER_KG[ing]


def test_spike_window_multiplies_only_its_ingredient_and_days():
    plain = MeanRevertingPrices(5, horizon_days=100)
    spiked = MeanRevertingPrices(
        5, horizon_days=100, spikes=(SpikeWindow(Ingredient.BUTTER, 10, 20, 2.0),)
    )
    assert abs(spiked.on(15)[Ingredient.BUTTER] - 2 * plain.on(15)[Ingredient.BUTTER]) <= 1
    assert spiked.on(25) == plain.on(25)
    assert spiked.on(15)[Ingredient.FLOUR] == plain.on(15)[Ingredient.FLOUR]


def test_prices_differ_between_seeds():
    assert MeanRevertingPrices(1, 50).on(30) != MeanRevertingPrices(2, 50).on(30)


def test_foot_traffic_weekend_is_lower_and_shock_applies():
    n, other = NeighborhoodId("a"), NeighborhoodId("b")
    plain = SeasonalFootTraffic()
    shocked = SeasonalFootTraffic((TrafficShock(n, 50, 60, 0.5),))
    assert plain.multiplier(n, 6) < plain.multiplier(n, 4)
    assert shocked.multiplier(n, 52) == 0.5 * plain.multiplier(n, 52)
    assert shocked.multiplier(other, 52) == plain.multiplier(other, 52)
    assert shocked.multiplier(n, 61) == plain.multiplier(n, 61)


def test_hidden_params_are_seeded_and_inside_ranges():
    a = draw_hidden_params(random.Random(9))
    assert a == draw_hidden_params(random.Random(9))
    assert a != draw_hidden_params(random.Random(10))
    for seg, (lo, hi) in ELASTICITY.items():
        assert lo <= a.elasticity[seg] <= hi
    for seg, cats in TASTE.items():
        for cat, (lo, hi) in cats.items():
            assert lo <= a.taste[seg][cat] <= hi
    lo, hi = SCALARS["quit_hazard"]
    assert lo <= a.quit_hazard <= hi
    assert set(a.item_appeal) == set(CATALOG)


def test_catalog_has_25_recipes_and_a_14_item_starting_menu():
    assert len(CATALOG) == 25
    assert len(STARTING_MENU) == 14
    assert set(STARTING_MENU) <= set(CATALOG)
    for r in CATALOG.values():
        assert r.grams and r.typical_price_cents > 0
        assert r.made_to_order == (r.oven_minutes == 0)
