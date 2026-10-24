import json

import pytest

from farm_world.layout import default_world
from farm_world.loop import merged_log, run_farm
from farm_world.router import FarmRouter
from station_control.provider_support import CallBudget
from station_control.society.llm import ChatRequest, FakeChat


def handler(decide):
    """A fake model: never reacts unless asked to, answers decisions with `decide(context)`."""

    def reply(request: ChatRequest) -> dict[str, object]:
        if request.purpose == "score_importance":
            return {"importance": 5}
        if request.purpose == "reflect":
            return {"insights": []}
        if request.purpose == "react_check":
            return {"react": True, "reason": "test"}
        return decide(json.loads(request.user), request)

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
        context = json.loads(request.user)
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


def test_quiet_days_make_no_model_call():
    def reply(request):
        if request.purpose == "react_check":
            return {"react": False, "reason": "nothing new"}
        if request.purpose == "score_importance":
            return {"importance": 3}
        return {"actions": [{"name": "rest"}], "rationale": "r"}

    chat = FakeChat(reply)
    run_farm(default_world(1), chat, days=4)
    assert len([r for r in chat.requests if r.purpose == "react_check"]) == 6  # day 0 only
    assert not [r for r in chat.requests if r.purpose == "decide"]


def test_unlisted_or_malformed_actions_are_dropped_not_executed():
    def decide(context, request):
        return {"actions": [{"name": "burn_the_neighbours_field"}], "rationale": "bad"}

    result, _ = run(decide, days=1)
    assert any(e["type"] == "malformed" for e in result.events)
    assert not [e for e in result.events if e["type"] == "action"]


def test_messages_arrive_after_a_delay_and_cross_community_takes_longer():
    router = FarmRouter({"a": "x", "b": "x", "c": "y"})
    near, _ = router.send("a", "b", "hello", 0)
    far, _ = router.send("a", "c", "hello", 0)
    assert router.deliver_due(0) == {}
    assert [m.message_id for m in router.deliver_due(1)["b"]] == [near.message_id]
    assert [m.message_id for m in router.deliver_due(2)["c"]] == [far.message_id]


def test_message_rules_are_enforced():
    router = FarmRouter({"a": "x", "b": "x"})
    assert router.send("a", "nobody", "hi", 0)[1] == "unknown_recipient"
    assert router.send("a", "a", "hi", 0)[1] == "unknown_recipient"
    assert router.send("a", "b", "  ", 0)[1] == "empty_message"
    assert router.send("a", "b", "one", 0)[0] and router.send("a", "b", "two", 0)[0]
    assert router.send("a", "b", "three", 0)[1] == "rate_limited"


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
        json.loads(r.user)
        for r in chat.requests
        if r.purpose == "decide" and json.loads(r.user)["world"]["me"]["id"] == "rice_2"
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


def test_the_cli_refuses_to_run_without_a_finite_budget():
    from farm_world.cli import main

    with pytest.raises(SystemExit) as exit_info:
        main(["--days", "2"])
    assert exit_info.value.code == 2


def test_prompts_carry_only_what_the_farmer_can_read_off_the_plot():
    _, chat = run(days=3)
    for request in chat.requests:
        if request.purpose in ("react_check", "decide"):
            context = json.loads(request.user)
            for line in context["trends"]:
                if line.startswith("health_pct"):
                    assert any(f"{word}" in line for word in ("100", "60", "20"))
                if line.startswith("water_mm"):
                    me = context["world"]["me"]["id"]
                    assert not me.startswith("wheat") or any(v in line for v in ("10", "50", "90"))


def test_stray_fields_are_dropped_and_wrong_types_are_rejected_by_the_world():
    def decide(context, request):
        if context["world"]["me"]["id"] != "rice_1":
            return {"actions": [{"name": "rest"}], "rationale": "wait"}
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
