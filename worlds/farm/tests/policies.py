"""Fixed test policies that exercise the physics. They are fixtures, not the product's farmers."""

from __future__ import annotations

from farm_world.world import FarmWorld


def run(world: FarmWorld, days: int, policy) -> None:
    for _ in range(days):
        policy(world)
        world.end_day()


def diligent(world: FarmWorld) -> None:
    doy = world.day % 365
    for farmer in world.farmers.values():
        plot = world.plots[farmer.plot_id]
        if farmer.community == "riverbend":
            _rice(world, farmer, plot, doy)
        else:
            _wheat(world, farmer, plot, doy)


def _rice(world, farmer, plot, doy) -> None:
    i = plot.id[-1]
    act = world.act
    turnout, outlet = f"turnout_{i}", f"outlet_{i}"
    crop = plot.crop
    if farmer.id == "rice_1":
        act(farmer.id, "set_gate", channel="intake", open=doy >= 150)
    if f"carry_{i}" in world.channels:
        act(farmer.id, "set_gate", channel=f"carry_{i}", open=doy >= 150)
    for name in ("intake",):
        if world.channels[name].silt > 0.5:
            act(farmer.id, "clear_canal", channel=name)
    if 150 <= doy <= 200 and not farmer.nursery and crop is None:
        act(farmer.id, "sow_nursery")
    wants_water = doy >= 150 and (crop is None or crop.stage != "mature")
    draining = crop is not None and crop.stage == "grain_fill" and crop.gdd > 1380
    if crop is not None and crop.stage == "mature":
        act(farmer.id, "set_gate", channel=outlet, open=plot.water_mm > 20)
        act(farmer.id, "set_gate", channel=turnout, open=False)
        act(farmer.id, "harvest")
    elif draining:
        act(farmer.id, "set_gate", channel=turnout, open=False)
        act(farmer.id, "set_gate", channel=outlet, open=True)
    else:
        act(farmer.id, "set_gate", channel=outlet, open=False)
        act(farmer.id, "set_gate", channel=turnout, open=wants_water and plot.water_mm < 70)
    if crop is None and 150 <= doy <= 215:
        if farmer.nursery and plot.water_mm >= 30:
            if not plot.puddled:
                act(farmer.id, "puddle")
            act(farmer.id, "transplant")
    if crop is not None:
        if plot.weeds > 0.3:
            act(farmer.id, "weed")
        if doy % 7 == 0:
            res = act(farmer.id, "scout")
            if res.ok and res.details["pests"] in ("heavy", "severe"):
                act(farmer.id, "treat")
        if plot.bund_integrity < 0.5:
            act(farmer.id, "repair_bund")
    act(farmer.id, "dry")
    act(farmer.id, "mill")


def _wheat(world, farmer, plot, doy) -> None:
    act = world.act
    crop = plot.crop
    if crop is None and 290 <= doy <= 310 and plot.tilled is False:
        act(farmer.id, "till")
    if crop is None and plot.tilled:
        act(farmer.id, "sow", variety="winter")
    if crop is not None:
        if crop.stage == "mature":
            act(farmer.id, "harvest")
        elif plot.water_mm < 55 and crop.stage in ("stem_elongation", "heading", "grain_fill"):
            act(farmer.id, "pump", mm=50)
        elif plot.water_mm < 40 and crop.stage in ("emergence", "tillering"):
            act(farmer.id, "pump", mm=30)
        if plot.weeds > 0.3:
            act(farmer.id, "weed")
        if doy % 7 == 0:
            res = act(farmer.id, "scout")
            if res.ok and res.details["pests"] in ("heavy", "severe"):
                act(farmer.id, "treat")
    act(farmer.id, "thresh")
    act(farmer.id, "mill")
