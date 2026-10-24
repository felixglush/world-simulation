import json
import re

import pytest

from farm_world.layout import default_world
from farm_world.loop import merged_log, run_farm
from farm_world.router import FarmRouter
from station_control.provider_support import CallBudget
from station_control.society.llm import ChatRequest, FakeChat


def context_of(request: ChatRequest) -> dict:
    """The state a farmer's chat holds at this request: the latest full turn, the world with
    every later change applied, and the farmer's own id."""
    turns = [json.loads(u) for u, _ in request.history] + [json.loads(request.user)]
    world: dict = {}
    latest: dict = {}
    for turn in turns:
        if "world" not in turn:
            continue
        latest = turn
        changes = turn["world"].get("changes_since_last_turn")
        _merge(world, turn["world"] if changes is None else changes)
    me = re.search(r"farmer id is (\w+)", request.system).group(1)
    return {**latest, "world": {**world, "me": {**world.get("me", {}), "id": me}}}


def _merge(base: dict, changes: dict) -> None:
    for key, value in changes.items():
        if value is None:
            base.pop(key, None)
        elif isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def handler(decide):
    """A fake model: never reacts unless asked to, answers decisions with `decide(context)`."""

    def reply(request: ChatRequest) -> dict[str, object]:
        if request.purpose == "score_importance":
            return {"importance": 5}
        if request.purpose == "reflect":
            return {"insights": []}
        if request.purpose == "react_check":
            return {"react": True, "reason": "test"}
        return decide(context_of(request), request)

    return reply


def idle(context, request):
    return {"actions": [{"name": "rest"}], "rationale": "idle"}


def run(decide=idle, days=3, **kwargs):
    chat = FakeChat(handler(decide), **kwargs)
    return run_farm(default_world(1), chat, days=days), chat


def test_a_farmers_reply_changes_the_world_through_validated_actions():
    def decide(context, request):
        if context["world"]["me"]["id"] == "rice_1":
            return {
                "actions": [
                    {"name": "sow_nursery", "kg": 30},
                    {"name": "set_gate", "channel": "intake", "open": True},
                    {"name": "transplant"},
                ],
                "rationale": "start the season",
            }
        return {"actions": [{"name": "rest"}], "rationale": "wait"}

    result, _ = run(decide, days=1)
    world = result.world
    assert world.farmers["rice_1"].nursery is not None
    assert world.channels["intake"].gate_open
    outcomes = [e for e in result.events if e["type"] == "action" and e["name"] == "transplant"]
    assert outcomes and not outcomes[0]["ok"]


def test_each_farmer_sees_only_their_own_bio_and_view():
    _, chat = run(days=1)
    decides = [r for r in chat.requests if r.purpose == "decide"]
    assert len(decides) == 6
    for request in decides:
        context = context_of(request)
        me = context["world"]["me"]["id"]
        others = [n for n in ("Maren", "Dev", "Ines", "Tomas", "Priya", "Oskar")]
        named = [n for n in others if n in request.system]
        assert len(named) == 1
        assert f"farmer id is {me}" in request.system


def test_no_ground_truth_reaches_a_prompt():
    _, chat = run(days=2)
    text = " ".join(r.user + r.system for r in chat.requests)
    for secret in ("vernalization", "sterility", "bund_integrity", 'soil_nitrogen": "rich'):
        assert secret not in text


def test_a_resting_farmer_is_asked_once_a_day_and_never_polled_hourly():
    chat = FakeChat(handler(idle))
    run_farm(default_world(1), chat, days=4)
    decides = [r for r in chat.requests if r.purpose == "decide"]
    assert len(decides) == 6 * 4
    assert not [r for r in chat.requests if r.purpose == "react_check"]


def test_unlisted_or_malformed_actions_are_dropped_not_executed():
    def decide(context, request):
        return {"actions": [{"name": "burn_the_neighbours_field"}], "rationale": "bad"}

    result, _ = run(decide, days=1)
    assert any(e["type"] == "malformed" for e in result.events)
    assert not [e for e in result.events if e["type"] == "action"]


def test_messages_arrive_in_the_hour_they_are_sent_within_and_across_communities():
    router = FarmRouter({"a": "x", "b": "x", "c": "y"})
    near, _ = router.send("a", "b", "hello", 0)
    far, _ = router.send("a", "c", "hello", 0)
    inbox = router.deliver_due(0)
    assert [m.message_id for m in inbox["b"]] == [near.message_id]
    assert [m.message_id for m in inbox["c"]] == [far.message_id]
    assert router.deliver_due(0) == {}


def test_message_rules_are_enforced():
    router = FarmRouter({"a": "x", "b": "x"})
    assert router.send("a", "nobody", "hi", 0)[1] == "unknown_recipient"
    assert router.send("a", "a", "hi", 0)[1] == "unknown_recipient"
    assert router.send("a", "b", "  ", 0)[1] == "empty_message"
    for n in range(8):
        assert router.send("a", "b", f"m{n}", 0)[0]
    assert router.send("a", "b", "too many today", 0)[1] == "rate_limited"


def test_a_message_reaches_the_recipients_next_review():
    def decide(context, request):
        me = context["world"]["me"]["id"]
        if me == "rice_1" and context["tick"] == 0:
            return {
                "actions": [{"name": "rest"}],
                "messages": [{"to": "rice_2", "text": "Can we share the canal turns?"}],
                "rationale": "talk",
            }
        return {"actions": [{"name": "rest"}], "rationale": "ok"}

    result, chat = run(decide, days=3)
    sent = [e for e in result.events if e["type"] == "message_sent"]
    assert sent and sent[0]["to"] == "rice_2"
    later = [
        context_of(r)
        for r in chat.requests
        if r.purpose == "decide" and context_of(r)["world"]["me"]["id"] == "rice_2"
    ]
    assert any("canal turns" in m["text"] for context in later for m in context["inbox"])


def test_the_budget_stops_the_run_cleanly():
    result, chat = run(days=5, budget=CallBudget(4, 1200))
    assert len(chat.requests) == 4
    assert any(e.get("error") == "budget_exhausted" for e in result.events if "error" in e)
    assert result.world.day == 140 + 5


def test_runs_are_reproducible():
    first, _ = run(days=4)
    second, _ = run(days=4)
    assert merged_log(first, 140) == merged_log(second, 140)


def test_the_merged_log_orders_thinking_then_work_then_the_day():
    result, _ = run(days=2)
    log = merged_log(result, 140)
    days = [e["day"] for e in log]
    assert days == sorted(days)
    first = [e for e in log if e["day"] == 140]
    assert first[-1]["type"] == "day"
    assert first[0]["source"] == "agent"


def test_the_sink_streams_every_row_as_the_run_goes_and_matches_the_final_log():
    rows = []
    world = default_world(seed=7, start_day=140)
    chat = FakeChat(handler(idle))
    result = run_farm(world, chat, days=2, sink=rows.append)
    assert rows[0]["type"] == "start" and rows[0]["day"] == 140
    ordered = sorted(rows[1:], key=lambda e: (e["day"], e["type"] == "day", e.get("hour", 0)))
    assert len(rows) - 1 == len(merged_log(result, 140))
    assert [e["type"] for e in rows if e["type"] == "day"] == ["day", "day"]
    assert ordered[0]["day"] == 140


def test_the_cli_refuses_to_run_without_a_finite_budget():
    from farm_world.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--days", "2"])
    assert exit_info.value.code == 2


def test_prompts_carry_only_what_the_farmer_can_read_off_the_plot():
    _, chat = run(days=3)
    for request in chat.requests:
        if request.purpose in ("react_check", "decide"):
            context = context_of(request)
            for line in context["trends"]:
                if line.startswith("health_pct"):
                    assert any(f"{word}" in line for word in ("100", "60", "20"))
                if line.startswith("water_mm"):
                    me = context["world"]["me"]["id"]
                    assert not me.startswith("wheat") or any(v in line for v in ("10", "50", "90"))


def test_stray_fields_are_dropped_and_wrong_types_are_rejected_by_the_world():
    asked = []

    def decide(context, request):
        if context["world"]["me"]["id"] != "rice_1" or asked:
            return {"actions": [{"name": "rest"}], "rationale": "wait"}
        asked.append(1)
        flat = {"plot": "paddy_1", "channel": "intake", "kg": 30, "mm": 5, "open": True}
        return {
            "actions": [
                {"name": "sow_nursery", **flat},
                {"name": "set_gate", **flat, "open": "no"},
            ],
            "rationale": "fill every field",
        }

    result, _ = run(decide, days=1)
    outcomes = {e["name"]: e for e in result.events if e["type"] == "action"}
    assert outcomes["sow_nursery"]["ok"]
    assert not outcomes["set_gate"]["ok"] and "true or false" in outcomes["set_gate"]["message"]
    assert result.world.farmers["rice_1"].nursery is not None


def test_a_plan_may_reach_past_the_old_station_horizon():
    def decide(context, request):
        return {
            "actions": [{"name": "rest"}],
            "plan": [{"due_tick": 400, "intent": "harvest wheat"}],
            "rationale": "long game",
        }

    result, _ = run(decide, days=1)
    plans = [e for e in result.events if e["type"] == "plan"]
    assert plans and plans[0]["steps"][0][0] == 400


def test_the_first_mover_rotates_each_day():
    from farm_world.loop import _order

    agents = {name: None for name in ("a", "b", "c")}
    assert [_order(agents, t)[0] for t in range(4)] == ["a", "b", "c", "a"]


def test_the_day_snapshot_records_what_the_farmer_did_before_the_daily_reset():
    world = default_world()
    farmer = next(iter(world.farmers))
    assert world.act(farmer, "scout", plot=next(iter(world.plots))).ok
    world.end_day()
    seen = world.events[-1]["farmers"][farmer]
    assert seen["activity"] == "scout"
    assert seen["labor"] < 10


def test_channels_reach_every_member_and_farmers_can_start_and_grow_them():
    world = default_world()
    router = FarmRouter({f.id: f.community for f in world.farmers.values()})
    assert set(router.channels_for("rice_1")) == {"#riverbend"}
    message, error = router.send("rice_1", "#riverbend", "water at dawn?", 0)
    assert error is None and message.text.startswith("[#riverbend]")
    inbox = router.deliver_due(0)
    assert set(inbox) == {"rice_2", "rice_3"}
    assert router.send("wheat_1", "#riverbend", "hi", 0)[1] == "not_a_member"
    assert router.send("rice_1", "#Bad name", "hi", 0)[1] == "bad_channel_name"
    router.send("rice_1", "#grain_deal", "mill co-op?", 0, invite=("wheat_2", "nobody"))
    assert set(router.channels_for("wheat_2")) == {"#highfield", "#grain_deal"}
    assert set(router.deliver_due(0)) == {"wheat_2"}
    router.send("wheat_2", "#grain_deal", "yes", 3, leave=True)
    assert "#grain_deal" not in router.channels_for("wheat_2")
    assert router.send("wheat_2", "#grain_deal", "again", 3)[1] == "not_a_member"


def test_queued_work_runs_hour_by_hour_and_a_message_wakes_a_busy_farmer_the_same_day():
    calls = {"rice_2": 0}

    def decide(context, request):
        me = context["world"]["me"]["id"]
        if me == "rice_1" and context["tick"] == 0 and not context["inbox"]:
            return {
                "actions": [{"name": "rest"}],
                "messages": [{"to": "rice_2", "text": "gate at noon?"}],
                "rationale": "talk",
            }
        if me == "rice_2" and calls["rice_2"] == 0:
            calls["rice_2"] += 1
            return {"actions": [{"name": "weed"}] * 3, "rationale": "work"}
        return {"actions": [{"name": "rest"}], "rationale": "ok"}

    result, chat = run(decide, days=1)
    weeds = [
        e for e in result.world.events if e.get("action") == "weed" and e["farmer"] == "rice_2"
    ]
    assert [e["hour"] for e in weeds[:1]] == [0]
    woken = [
        context_of(r)
        for r in chat.requests
        if r.purpose == "decide" and context_of(r)["world"]["me"]["id"] == "rice_2"
    ]
    mid_shift = [c for c in woken if c["inbox"]]
    assert mid_shift
    assert any("gate at noon" in m["text"] for c in mid_shift for m in c["inbox"])
    assert mid_shift[0]["world"]["queued_actions"] == ["weed", "weed"]


def test_each_farmer_keeps_one_running_chat_whose_earlier_turns_are_never_rewritten():
    _, chat = run(days=3)
    mine = [
        r
        for r in chat.requests
        if "farmer id is rice_1" in r.system and r.purpose != "score_importance"
    ]
    assert len(mine) >= 3
    first_full = json.loads(mine[0].user)
    assert "map" in first_full["world"] and "changes_since_last_turn" not in first_full["world"]
    grew = 0
    for before, after in zip(mine, mine[1:], strict=False):
        if len(after.history) > len(before.history):
            grew += 1
            assert after.history[: len(before.history)] == before.history  # prefix: cacheable
    assert grew >= 2
    later = [json.loads(r.user) for r in mine if r.history]
    assert later and all("changes_since_last_turn" in t["world"] for t in later)
    assert len({r.system for r in mine}) == 1  # one system prompt for every turn


def test_a_chat_that_outgrows_its_bound_starts_over_from_the_full_state():
    from station_control.society import agents

    agents.CHAT_CHAR_LIMIT, old = 600, agents.CHAT_CHAR_LIMIT
    try:
        _, chat = run(days=3)
    finally:
        agents.CHAT_CHAR_LIMIT = old
    mine = [r for r in chat.requests if "farmer id is rice_1" in r.system and r.purpose == "decide"]
    fulls = [r for r in mine if "changes_since_last_turn" not in json.loads(r.user)["world"]]
    assert len(fulls) >= 2 and not fulls[1].history


def test_a_chatty_channel_cannot_wake_a_farmer_more_than_four_times_a_day():
    def decide(context, request):
        me = context["world"]["me"]["id"]
        channel = "#riverbend" if me.startswith("rice") else "#highfield"
        return {
            "actions": [{"name": "rest"}],
            "messages": [{"to": channel, "text": f"{me} here"}],
            "rationale": "chat",
        }

    _, chat = run(decide, days=1)
    per_farmer: dict[str, int] = {}
    for request in chat.requests:
        if request.purpose in ("decide", "react_check") and "world" in json.loads(request.user):
            me = re.search(r"farmer id is (\w+)", request.system).group(1)
            per_farmer[me] = per_farmer.get(me, 0) + 1
    assert per_farmer and max(per_farmer.values()) <= 4 * 2
    assert not [r for r in chat.requests if r.purpose == "score_importance"]


def test_farmers_are_told_to_keep_their_farm_healthy_and_see_what_they_just_tried():
    first = {}

    def decide(context, request):
        me = context["world"]["me"]["id"]
        first.setdefault(me, []).append(context.get("your_recent_results"))
        return {"actions": [{"name": "scout"}], "rationale": "look"}

    _, chat = run(decide, days=2)
    assert all("keep your farm healthy" in r.system for r in chat.requests if r.purpose == "decide")
    assert any(results for runs in first.values() for results in runs if results)
    assert all(
        "keep your farm healthy" in r.system
        for r in chat.requests
        if r.purpose in ("decide", "react_check")
    )
