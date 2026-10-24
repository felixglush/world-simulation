import json
from dataclasses import replace

from policies import diligent, run

from farm_world import crops
from farm_world.crops import CropState
from farm_world.layout import default_world
from farm_world.weather import Climate, Weather


def warm(day=200, rain=0.0, tmin=22.0):
    return Weather(day, 27.0, tmin, 32.0, rain, 900.0, 0.2)


def test_weather_is_a_pure_function_of_seed_and_day():
    assert Climate(3).weather(120) == Climate(3).weather(120)
    assert Climate(3).weather(120) != Climate(4).weather(120)


def test_the_monsoon_is_wetter_and_warmer_than_winter():
    climate = Climate(1)
    wet = sum(climate.weather(d).rain_mm for d in range(160, 270))
    dry = sum(climate.weather(d).rain_mm for d in range(300, 410))
    assert wet > 3 * dry
    assert climate.weather(200).temp_mean > climate.weather(20).temp_mean + 10


def test_a_paddy_cannot_be_puddled_dry_or_transplanted_unpuddled():
    world = default_world(1)
    assert "30 mm" in world.act("rice_1", "puddle").message
    world.act("rice_1", "sow_nursery")
    assert not world.act("rice_1", "transplant").ok
    world.plots["paddy_1"].water_mm = 60
    world.act("rice_1", "puddle")
    assert "seedlings are 0 days old" in world.act("rice_1", "transplant").message


def test_farmers_cannot_work_each_others_plots():
    world = default_world(1)
    assert "belongs to rice_2" in world.act("rice_1", "weed", plot="paddy_2").message


def test_water_reaches_terraces_by_gravity_through_open_gates_only():
    world = default_world(1, start_day=200)  # monsoon: the river stands high
    for _ in range(3):
        world.end_day()
    assert all(c.last_flow == 0.0 for c in world.channels.values())  # every gate starts closed
    for gate in ("intake", "turnout_1", "carry_1", "turnout_2"):
        world.act("rice_1", "set_gate", channel=gate, open=True)
    for _ in range(5):
        world.end_day()
    assert world.channels["turnout_1"].last_flow > 0 and world.channels["turnout_2"].last_flow > 0
    assert world.channels["turnout_3"].last_flow == 0.0
    assert world.plots["paddy_1"].water_mm > 60


def test_the_winter_river_is_too_low_to_feed_the_canal():
    world = default_world(1, start_day=380)
    for gate in ("intake", "turnout_1"):
        world.act("rice_1", "set_gate", channel=gate, open=True)
    for _ in range(5):
        world.end_day()
    assert world.channels["intake"].last_flow == 0.0


def test_a_hogged_canal_starves_the_terraces_below():
    world = default_world(1, start_day=200)
    world.act("rice_1", "set_gate", channel="intake", open=True)
    world.act("rice_1", "set_gate", channel="turnout_1", open=True)
    for _ in range(30):
        world.end_day()
    assert world.plots["paddy_3"].water_mm < 15


def test_overtopping_spills_downhill_and_breaks_a_bund():
    world = default_world(1, start_day=200)
    world.plots["paddy_1"].water_mm = 100
    world.plots["paddy_1"].bund_mm = 120
    world.plots["paddy_1"].water_mm += 200
    world._overflow(world.plots["paddy_1"])
    assert world.plots["paddy_1"].bund_integrity < 1.0
    assert world.plots["paddy_2"].water_mm > 0
    world.plots["paddy_1"].bund_integrity = 0.0
    assert world.plots["paddy_1"].holds == 20.0


def test_rice_drought_at_flowering_costs_grain():
    def finish(water):
        crop = CropState("rice", 0)
        crop.gdd = 700.0
        for _ in range(12):
            crops.grow(crop, warm(), water, 80.0, 0.0, 0.0)
        return crop

    good, dry = finish(60.0), finish(0.0)
    assert dry.sterility > good.sterility == 0.0
    assert dry.biomass < good.biomass * 0.5


def test_rice_seedlings_drown_under_deep_water():
    crop = CropState("rice", 0)
    for _ in range(5):
        crops.grow(crop, warm(), 200.0, 80.0, 0.0, 0.0)
    assert crop.health < 0.8


def test_winter_wheat_stalls_without_cold_and_heads_after_vernalizing():
    mild = Weather(100, 15.0, 10.0, 20.0, 0.0, 600.0, 0.2)
    cold = Weather(10, 4.0, 0.0, 8.0, 0.0, 600.0, 0.2)
    winter, spring = (
        CropState("wheat", 0, variety="winter"),
        CropState("wheat", 0, variety="spring"),
    )
    for _ in range(200):
        crops.grow(winter, mild, 80.0, 80.0, 0.0, 0.0)
        crops.grow(spring, mild, 80.0, 80.0, 0.0, 0.0)
    assert winter.stage == "stem_elongation"
    assert spring.stage in ("heading", "grain_fill", "mature")
    for _ in range(45):
        crops.grow(winter, cold, 80.0, 80.0, 0.0, 0.0)
    for _ in range(60):
        crops.grow(winter, mild, 80.0, 80.0, 0.0, 0.0)
    assert winter.stage in ("heading", "grain_fill", "mature")


def test_waterlogging_hurts_wheat_but_a_moist_field_does_not():
    soaked, moist = CropState("wheat", 0), CropState("wheat", 0)
    for _ in range(10):
        crops.grow(soaked, warm(), 170.0, 80.0, 0.0, 0.0)
        crops.grow(moist, warm(), 80.0, 80.0, 0.0, 0.0)
    assert soaked.health < 0.7 and moist.health == 1.0


def test_wheat_shatters_when_harvest_is_late():
    crop = CropState("wheat", 0)
    crop.biomass, crop.stage = 8000.0, "mature"
    fresh = crops.grain_yield(crop)
    crop.days_mature = 30
    assert crops.grain_yield(crop) < fresh * 0.8


def test_wet_paddy_cannot_dry_in_the_rain_and_spoils_if_left():
    world = default_world(1, start_day=200)
    farmer = world.farmers["rice_1"]
    farmer.add("paddy_wet", 400)
    world.weather = replace(world.weather, rain_mm=10.0)
    assert not world.act("rice_1", "dry").ok
    for _ in range(10):
        world.end_day()
    assert farmer.has("paddy_wet") < 400 * 0.85


def test_harvest_carries_over_between_days():
    world = default_world(1, start_day=200)
    plot = world.plots["paddy_1"]
    plot.crop = CropState("rice", 0)
    plot.crop.stage, plot.crop.biomass = "mature", 9000.0
    plot.water_mm = 0.0
    farmer = world.farmers["rice_1"]
    farmer.labor = 5.0
    first = world.act("rice_1", "harvest")
    assert first.ok and "done" in first.message and plot.crop is not None
    farmer.labor = 10.0
    assert "harvested" in world.act("rice_1", "harvest").message
    assert farmer.has("paddy_wet") > 2000


def test_view_hides_ground_truth():
    world = default_world(1)
    text = json.dumps(world.view("rice_1"))
    for secret in ('nitrogen":', 'pest":', "vernalization", "sterility", "biomass"):
        assert secret not in text
    assert world.view("rice_1")["plots"]["paddy_2"].get("weeds_visible") is None


def test_a_diligent_year_feeds_both_communities():
    world = default_world(1)
    run(world, 480, diligent)
    harvests = [e for e in world.events if e["type"] == "action" and "harvested" in e["message"]]
    assert len([e for e in harvests if e["farmer"].startswith("rice")]) >= 3
    assert len([e for e in harvests if e["farmer"].startswith("wheat")]) >= 3
    area = 0.6
    for e in harvests:
        kg_per_ha = float(e["message"].split()[1]) / area
        assert 2000 < kg_per_ha < 7000, e
    assert all(f.hunger_days == 0 for f in world.farmers.values())
