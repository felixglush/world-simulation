from farm_world.layout import default_world
from farm_world.perception import Perception


def stock(world, item):
    """Everything of one good anywhere: barns, offers held in escrow, and goods in transit."""
    total = sum(f.has(item) for f in world.farmers.values())
    total += sum(
        o.give[1] for o in world.economy.offers.values() if o.status == "open" and o.give[0] == item
    )
    return total + sum(s.qty for s in world.economy.shipments if s.item == item)


def test_buildings_need_their_materials_and_riverbend_cannot_mill_alone():
    world = default_world(1)
    rejected = world.act("rice_1", "build", kind="mill")
    assert not rejected.ok and "stone" in rejected.message
    built = world.act("wheat_1", "build", kind="mill")
    assert built.ok
    assert world.farmers["wheat_1"].has("stone") == 4
    assert world.farmers["wheat_1"].has("timber") == 0


def test_a_community_builds_each_structure_once_and_everyone_in_it_benefits():
    world = default_world(1)
    world.farmers["wheat_1"].labor = 10
    assert world.act("wheat_1", "build", kind="mill").ok
    assert "already has a mill" in world.act("wheat_2", "build", kind="mill").message
    for who in ("wheat_2", "rice_1"):
        world.farmers[who].add("paddy_dry", 3000)
        world.farmers[who].labor = 10
    assert world.act("wheat_2", "mill", item="paddy_dry").ok
    assert world.farmers["rice_1"].has("rice") == 100  # nothing milled yet
    assert world.act("rice_1", "mill", item="paddy_dry").ok
    # Highfield's mill does 3000 kg for 10 labor; Riverbend, without one, does 1000 kg.
    assert world.farmers["wheat_2"].has("paddy_dry") == 0
    assert world.farmers["rice_1"].has("paddy_dry") == 2000


def test_a_mill_triples_what_a_days_labor_can_mill():
    def milled(with_mill):
        world = default_world(1)
        if with_mill:
            world.farmers["wheat_1"].labor = 10
            world.act("wheat_1", "build", kind="mill")
        farmer = world.farmers["wheat_2"]
        farmer.add("wheat_grain", 5000)
        farmer.labor = 10
        world.act("wheat_2", "mill", item="wheat_grain")
        return farmer.has("flour") - 100

    assert milled(True) == 3 * milled(False)


def test_stone_from_the_uplands_lets_the_valley_build_its_mill():
    world = default_world(1)
    assert world.act("wheat_1", "give", to="rice_1", item="stone", qty=6).ok
    assert world.farmers["rice_1"].has("stone") == 2
    world.end_day()
    world.end_day()
    assert world.farmers["rice_1"].has("stone") == 8
    assert world.act("rice_1", "build", kind="mill").ok


def test_gifts_take_longer_between_communities():
    world = default_world(1)
    world.act("rice_1", "give", to="rice_2", item="coin", qty=10)
    world.act("rice_1", "give", to="wheat_1", item="coin", qty=10)
    world.end_day()
    assert world.farmers["rice_2"].has("coin") == 60
    assert world.farmers["wheat_1"].has("coin") == 50
    world.end_day()
    assert world.farmers["wheat_1"].has("coin") == 60


def test_an_offer_holds_the_goods_and_a_swap_moves_both_sides():
    world = default_world(1)
    made = world.act(
        "wheat_1",
        "offer",
        to="rice_1",
        give_item="stone",
        give_qty=6,
        want_item="rice",
        want_qty=100,
    )
    assert made.ok and world.farmers["wheat_1"].has("stone") == 6
    offer_id = made.details["offer_id"]
    assert not world.act("wheat_2", "accept", offer_id=offer_id).ok
    assert world.act("rice_1", "accept", offer_id=offer_id).ok
    assert not world.act("rice_1", "accept", offer_id=offer_id).ok
    world.end_day()
    world.end_day()
    assert world.farmers["rice_1"].has("stone") == 8
    assert world.farmers["wheat_1"].has("rice") == 100


def test_nothing_is_created_or_lost_by_offers_gifts_and_expiry():
    world = default_world(1)
    before = {item: stock(world, item) for item in ("stone", "timber", "coin", "rice")}
    first = world.act(
        "wheat_1",
        "offer",
        to="rice_1",
        give_item="stone",
        give_qty=5,
        want_item="coin",
        want_qty=20,
    )
    second = world.act(
        "rice_2", "offer", to="rice_3", give_item="timber", give_qty=7, want_item="coin", want_qty=5
    )
    world.act("rice_3", "accept", offer_id=second.details["offer_id"])
    world.act("wheat_2", "give", to="rice_2", item="coin", qty=11)
    for _ in range(7):
        world.end_day()
    assert world.economy.offers[first.details["offer_id"]].status == "expired"
    assert world.farmers["wheat_1"].has("stone") == 12
    after = {item: stock(world, item) for item in before}
    consumed = after["rice"] - before["rice"]
    assert {k: v for k, v in after.items() if k != "rice"} == {
        k: v for k, v in before.items() if k != "rice"
    }
    assert consumed < 0


def test_offers_cannot_be_overdrawn_or_stacked():
    world = default_world(1)
    assert not world.act(
        "rice_1", "offer", to="rice_2", give_item="coin", give_qty=51, want_item="rice", want_qty=1
    ).ok
    for n in range(3):
        assert world.act(
            "rice_1",
            "offer",
            to="rice_2",
            give_item="coin",
            give_qty=1,
            want_item="rice",
            want_qty=1,
        ).ok
    assert (
        "3 open offers"
        in world.act(
            "rice_1",
            "offer",
            to="rice_2",
            give_item="coin",
            give_qty=1,
            want_item="rice",
            want_qty=1,
        ).message
    )
    assert not world.act(
        "rice_1", "offer", to="rice_1", give_item="coin", give_qty=1, want_item="rice", want_qty=1
    ).ok
    assert not world.act("rice_1", "give", to="rice_2", item="gold", qty=1).ok


def test_the_taker_must_hold_what_is_asked():
    world = default_world(1)
    offer = world.act(
        "rice_1", "offer", to="rice_2", give_item="coin", give_qty=10, want_item="stone", want_qty=5
    )
    assert (
        "only 2 stone" in world.act("rice_2", "accept", offer_id=offer.details["offer_id"]).message
    )
    assert world.act("rice_1", "withdraw", offer_id=offer.details["offer_id"]).ok
    assert world.farmers["rice_1"].has("coin") == 50


def test_a_granary_slows_the_spoilage_of_stored_rice():
    def rice_left(granary):
        world = default_world(1)
        if granary:
            world.farmers["rice_1"].labor = 10
            world.act("rice_1", "build", kind="granary")
        world.farmers["rice_2"].inventory["rice"] = 100000.0
        for _ in range(60):
            world.end_day()
        return world.farmers["rice_2"].has("rice")

    assert rice_left(True) > rice_left(False)


def test_farmers_see_offers_made_to_them_and_the_world_shows_buildings():
    world = default_world(1)
    perception = Perception()
    perception.view(0, world.view("rice_2"), ())
    world.act(
        "rice_1", "offer", to="rice_2", give_item="coin", give_qty=5, want_item="rice", want_qty=20
    )
    world.end_day()
    view = perception.view(1, world.view("rice_2"), ())
    assert [i.code for i in view.items if i.code == "offer"]
    assert world.view("rice_2")["offers_to_me"][0]["from"] == "rice_1"
    assert "offers_to_me" in world.view("rice_1") and not world.view("rice_1")["offers_to_me"]
    world.farmers["rice_1"].labor = 10
    world.act("rice_1", "build", kind="threshing_floor")
    world.end_day()
    assert [s["kind"] for s in world.snapshot()["structures"]] == ["threshing_floor"]
    assert world.view("wheat_1")["buildings"][0]["community"] == "riverbend"


def test_the_actions_document_lists_every_action():
    from pathlib import Path

    from farm_world.world import ACTION_PARAMS

    text = (Path(__file__).resolve().parents[1] / "ACTIONS.md").read_text()
    missing = [name for name in ACTION_PARAMS if name != "rest" and f"`{name}`" not in text]
    assert not missing, f"ACTIONS.md does not mention {missing}"
    assert "`rest`" in text


def test_the_run_log_orders_trades_and_gifts_between_thinking_and_the_day_snapshot():
    from farm_world.loop import FarmResult, merged_log

    world = default_world(1)
    world.act("wheat_1", "give", to="wheat_2", item="coin", qty=5)
    offer = world.act(
        "rice_1", "offer", to="rice_2", give_item="coin", give_qty=5, want_item="rice", want_qty=1
    )
    world.act("rice_2", "accept", offer_id=offer.details["offer_id"])
    world.end_day()
    events = ({"tick": 0, "agent": "rice_1", "type": "gate"},)
    log = merged_log(FarmResult(world, events, (), ()), 140)
    kinds = [e["type"] for e in log]
    assert kinds[0] == "gate" and kinds[-1] == "day"
    assert {"gift", "trade"} <= set(kinds)


def test_the_farmer_tool_schema_can_express_every_action_parameter():
    from farm_world.roles import FARMER_SCHEMA
    from farm_world.world import ACTION_PARAMS

    props = FARMER_SCHEMA["properties"]["actions"]["items"]["properties"]
    for params in ACTION_PARAMS.values():
        assert set(params) <= set(props)
    for good in ("timber", "stone", "coin", "flour"):
        assert good in props["item"]["enum"]
        assert good in props["give_item"]["enum"]
    assert props["kind"]["type"] == "string"


def test_malformed_parameter_types_are_rejected_not_raised():
    world = default_world()
    for bad in (["mill"], {"a": 1}, 3, None):
        assert not world.act("rice_1", "build", kind=bad).ok
    assert not world.act("rice_1", "give", to="rice_2", item=["rice"], qty=1).ok


def test_any_named_structure_costs_what_the_farmer_puts_in_and_has_no_effect():
    world = default_world()
    farmer = world.farmers["rice_1"]
    timber, labor = farmer.has("timber"), farmer.labor
    result = world.act("rice_1", "build", kind="drying rack", timber=3, labor=2)
    assert result.ok, result.message
    assert farmer.has("timber") == timber - 3
    assert farmer.labor == labor - 2
    assert [s.kind for s in world.economy.structures] == ["drying rack"]
    # open kinds are not limited to one per community and do not unlock the real effects
    assert world.act("rice_1", "build", kind="drying rack", timber=1, labor=1).ok
    assert not world.economy.has("Riverbend", "mill")
    assert not world.act("rice_1", "build", kind="Bad Name!", timber=1).ok
    assert not world.act("rice_1", "build", kind="shed", timber=500).ok


def test_a_dug_channel_joins_the_shared_network_and_carries_water_when_opened():
    world = default_world()
    result = world.act("rice_1", "dig", src="canal_1", dst="paddy_2", labor=3)
    assert result.ok, result.message
    channel = world.channels["dug_1"]
    assert channel.built_by == "rice_1" and not channel.gate_open
    assert channel.capacity_mm == 30
    assert not world.act("rice_1", "dig", src="canal_1", dst="paddy_2").ok
    assert not world.act("rice_1", "dig", src="paddy_2", dst="paddy_2").ok
    assert not world.act("rice_1", "dig", src="drain", dst="paddy_1").ok
    assert not world.act("rice_1", "dig", src="nowhere", dst="paddy_1").ok
    assert world.act("rice_2", "set_gate", channel="dug_1", open=True).ok
    view = world.view("rice_2")["channels"]["dug_1"]
    assert view["open"] and view["dug_by"] == "rice_1"
    assert world.snapshot()["channels"]["dug_1"]["path"]


def test_a_farmer_sees_where_the_land_and_their_neighbors_are():
    world = default_world()
    world.act("rice_2", "weed", plot="paddy_2")
    view = world.view("rice_1")
    assert view["map"]["plots"]["paddy_2"] and view["map"]["wells"]
    assert view["people_nearby"]["rice_2"] == {"at": "on paddy_2", "doing": "weed"}
    assert "wheat_1" not in view["people_nearby"]
    assert view["you_are_at"]


def test_young_rice_in_deep_water_looks_drowned_from_the_road():
    from farm_world.crops import CropState

    world = default_world()
    plot = world.plots["paddy_1"]
    plot.crop = CropState("rice", world.day)
    plot.water_mm = 120
    assert world.view("rice_2")["plots"]["paddy_1"]["seedlings"] == "mostly under water"
    plot.water_mm = 60
    assert "seedlings" not in world.view("rice_2")["plots"]["paddy_1"]
    plot.water_mm, plot.crop.gdd = 120, 400
    assert "seedlings" not in world.view("rice_2")["plots"]["paddy_1"]


def test_a_channel_is_not_silenced_by_a_per_pair_cap():
    from farm_world.router import FarmRouter

    router = FarmRouter({"a": "x", "b": "x", "c": "x"})
    for day in range(40):
        assert router.send("a", "#x", f"post {day}", day)[1] is None


def test_farmers_see_what_each_shared_building_costs_and_what_they_lack():
    world = default_world()
    rice = world.view("rice_1")["could_build"]
    assert rice["mill"]["you_lack"] == {"stone": 6.0}
    assert rice["granary"]["you_lack"] == {}
    wheat = world.view("wheat_1")["could_build"]
    assert wheat["mill"]["you_lack"] == {}
    assert wheat["granary"]["you_lack"] == {}
