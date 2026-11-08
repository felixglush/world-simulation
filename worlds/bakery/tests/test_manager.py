import json

import pytest

from bakery_world.llm import CallBudget, Caller, FakeChatModel, Reply, ToolError, tool_call
from bakery_world.management import load, manage
from bakery_world.manager import (
    CAPS,
    OBJECTIVE,
    SYSTEM,
    Budget,
    LLMManager,
    Turn,
    TurnContext,
    TurnKind,
    turn_messages,
)
from bakery_world.plan import ActionRecord, Experiment, PlanItem, PlanState
from bakery_world.town import HOME_LOCATION

ANSWER = Reply("Croissant sales fell 9% last year. SQL: SELECT ...")


def step(name: str, /, **args) -> Reply:
    return Reply(None, (tool_call(name, **args),))


def kinds(lines: list[str], kind: str) -> list[dict]:
    return [r for r in map(json.loads, lines) if r["kind"] == kind]


def manager(script, budget: int = 500, analyst=None, **kw) -> LLMManager:
    return LLMManager(
        Caller(CallBudget(budget)),
        FakeChatModel(script, name="fake-manager"),
        analyst or FakeChatModel(lambda m, t: ANSWER, name="fake-analyst"),
        **kw,
    )


def test_a_scripted_manager_acts_asks_and_ends_the_week(seed1_history) -> None:
    script = [
        step("end_week"),
        step("set_price", location=HOME_LOCATION, item="croissant", price_cents=455),
        step("ask_analyst", question="How have croissant sales moved?"),
        step("set_price", location=HOME_LOCATION, item="unicorn", price_cents=400),
        step("end_week"),
    ]
    policy = manager(script, records=seed1_history.records)
    result = manage(seed1_history.handoff, policy, weeks=1)
    menu = result.world.world.business.locations[HOME_LOCATION].menu
    assert menu["croissant"].price_cents == 455

    turns = kinds(result.log, "turn")
    assert [(t["turn"], t["phase"]) for t in turns[:4]] == [
        ("onboarding", "start"),
        ("onboarding", "end"),
        ("weekly", "start"),
        ("weekly", "end"),
    ]
    assert turns[3]["reason"] == "end_week" and turns[3]["calls"] == 4
    tools = [(t["tool"], t["result"]) for t in kinds(result.log, "tool_call")]
    assert tools[1][0] == "set_price" and json.loads(tools[1][1])["ok"]
    assert tools[3][1].startswith("refused: item must be one of")
    asked = kinds(result.log, "analyst")
    assert asked == [asked[0]] and asked[0]["answer"] == ANSWER.content
    roles = [c["role"] for c in kinds(result.log, "model_call")]
    assert roles[:4] == ["manager", "manager", "manager", "analyst"]
    assert kinds(result.log, "action")[0]["action"] == "set_price"

    seen = policy.manager_model.seen
    refusal = seen[4][0][-1]
    assert refusal["role"] == "tool" and refusal["content"].startswith("refused: ")
    assert seen[3][0][-1]["content"] == ANSWER.content


FORBIDDEN = (
    "profit",
    "net worth",
    "score",
    "mistake",
    "habit",
    "force",
    "hidden",
    "decay",
    "planted",
    "elasticity",
    "morale",
    "reputation",
    "awareness",
)


@pytest.mark.parametrize("kind", list(TurnKind))
def test_the_objective_system_and_turn_templates_give_nothing_away(kind: TurnKind) -> None:
    ctx = TurnContext(
        kind,
        "2026-01-12",
        1,
        52,
        (),
        (),
        "",
        event={"event": "oven"} if kind is TurnKind.INTERRUPT else None,
        day=1102,
        budget=Budget(25, 4, 900),
        plan=(PlanItem("Look at bake plans", PlanState.DOING),),
        experiments=(Experiment("latte", "h", "c", "m", 2, 1095),),
        actions=(ActionRecord(1095, "set_price", {"item": "latte"}, "price set", False),),
    )
    text = (OBJECTIVE + SYSTEM + json.dumps(turn_messages(ctx))).lower()
    assert [word for word in FORBIDDEN if word in text] == []
    assert "make the bakery successful" in OBJECTIVE and "retired" in OBJECTIVE


def test_the_turn_header_shows_what_is_left_of_the_budget(seed1_history) -> None:
    ctx = TurnContext(TurnKind.WEEKLY, "2026-01-12", 1, 52, (), (), "", budget=Budget(25, 4, 1234))
    head = turn_messages(ctx)[1]["content"].split("\n\n")[1]
    assert head.startswith(
        "Budget: 25 model calls left this turn, 4 analyst questions left this turn, and 1,234 "
        "model calls left in the whole run."
    )
    policy = manager([step("end_week")] * 2, budget=300, records=seed1_history.records)
    manage(seed1_history.handoff, policy, weeks=1)
    onboarding, weekly = (m[1]["content"] for m, _ in policy.manager_model.seen)
    assert "80 model calls left this turn, 20 analyst questions left this turn, and 300" in (
        onboarding
    )
    assert "25 model calls left this turn, 4 analyst questions left this turn, and 299" in weekly


def deep_agent(messages, tools) -> Reply:
    head = messages[1]["content"]
    n = sum(m["role"] == "assistant" for m in messages)
    if "onboarding" in head:
        return [
            step("plan_update", items=[{"text": "Test a latte price rise", "state": "doing"}]),
            step(
                "experiment_start",
                name="latte price",
                hypothesis="Latte buyers will pay 25 cents more",
                change="latte 450 to 475 cents",
                metric="latte units and revenue against the same weeks last year",
                review_after_weeks=2,
            ),
            step("set_price", location=HOME_LOCATION, item="latte", price_cents=475),
            step("calculate", expression="pct_change(450, 475)"),
            step("end_week"),
        ][n]
    if "start of week 3 of" in head and n == 0:
        return step(
            "experiment_conclude",
            name="latte price",
            result="units flat, revenue up 5%",
            decision="keep 475",
        )
    return step("end_week")


def test_a_deep_agent_plans_runs_an_experiment_and_concludes_it(seed1_history) -> None:
    policy = manager(deep_agent, records=seed1_history.records)
    result = manage(seed1_history.handoff, policy, weeks=3)
    seen = policy.manager_model.seen
    week = {
        w: next(m[1]["content"] for m, _ in seen if f"start of week {w} of" in m[1]["content"])
        for w in (2, 3)
    }
    for text in week.values():
        assert "1. [doing] Test a latte price rise" in text
        assert 'set_price {"item": "latte", "location": "shop1", "price_cents": 475}' in text
    assert "latte price [running]: started 2026-01-01, 1 of 2 weeks run" in week[2]
    assert "latte price [due for review]: started 2026-01-01, 2 of 2 weeks run" in week[3]
    tools = {t["tool"]: t["result"] for t in kinds(result.log, "tool_call")}
    assert tools["calculate"] == "5.555555556"
    assert tools["experiment_conclude"] == "experiment 'latte price' concluded after 2 weeks"
    (latte,) = policy.experiments
    assert latte.decision == "keep 475" and latte.concluded_day == latte.start_day + 14


def test_plan_and_experiment_arguments_are_checked_at_the_tool(seed1_history) -> None:
    mw = load(seed1_history.handoff)
    policy = manager([], records=seed1_history.records)
    tools = policy.tools(mw, Turn(TurnKind.WEEKLY, 4))
    for items in ("do things", [{"text": "x", "state": "maybe"}], [{"text": "", "state": "todo"}]):
        with pytest.raises(ToolError):
            tools["plan_update"].handler({"items": items})
    start = {"name": "a", "hypothesis": "h", "change": "c", "metric": "m"}
    with pytest.raises(ToolError):
        tools["experiment_start"].handler(start | {"review_after_weeks": 0})
    tools["experiment_start"].handler(start | {"review_after_weeks": 3})
    with pytest.raises(ToolError):
        tools["experiment_start"].handler(start | {"review_after_weeks": 3})
    with pytest.raises(ToolError):
        tools["experiment_conclude"].handler({"name": "b", "result": "r", "decision": "d"})


def test_context_is_fresh_each_week_but_notes_carry_over(seed1_history) -> None:
    script = [
        step("notes_write", text="check croissant waste"),
        step("end_week"),
        step("end_week"),
        step("end_week"),
    ]
    policy = manager(script, records=seed1_history.records)
    manage(seed1_history.handoff, policy, weeks=2)
    second_week = [m for m, _ in policy.manager_model.seen if "week 2 of 52" in m[1]["content"]]
    messages = second_week[0]
    assert len(messages) == 2 and "check croissant waste" in messages[1]["content"]
    assert "set_price" not in json.dumps(messages)


def loop_forever(messages, tools) -> Reply:
    return step("view_dashboard")


@pytest.mark.parametrize("kind", list(TurnKind))
def test_turns_stop_at_their_call_caps(seed1_history, kind: TurnKind) -> None:
    mw = load(seed1_history.handoff)
    policy = manager(loop_forever, records=seed1_history.records, onboarded=True)
    if kind is TurnKind.ONBOARDING:
        policy.onboarded = False
        policy.weekly(mw, 0)
    elif kind is TurnKind.WEEKLY:
        policy.weekly(mw, 0)
    else:
        policy.interrupt(mw, mw.curveballs[0])
    ends = [t for t in kinds(mw.log.lines, "turn") if t["phase"] == "end"]
    assert ends[0]["turn"] == kind and ends[0]["calls"] == CAPS[kind].calls
    assert ends[0]["reason"].startswith("call cap")


def test_at_most_four_analyst_questions_per_week(seed1_history) -> None:
    asks = tuple(tool_call("ask_analyst", question=f"Question {n}?") for n in range(6))
    script = [Reply(None, asks), step("end_week")]
    mw = load(seed1_history.handoff)
    policy = manager(script, records=seed1_history.records, onboarded=True)
    policy.weekly(mw, 0)
    assert len(kinds(mw.log.lines, "analyst")) == CAPS[TurnKind.WEEKLY].questions == 4
    results = [t["result"] for t in kinds(mw.log.lines, "tool_call") if t["role"] == "manager"]
    assert [r.startswith("refused") for r in results[:6]] == [False] * 4 + [True] * 2


def test_the_call_budget_stops_the_run_cleanly(seed1_history) -> None:
    policy = manager(loop_forever, budget=30, records=seed1_history.records)
    result = manage(seed1_history.handoff, policy, weeks=52)
    assert result.weeks == 1 and not result.bankrupt
    assert policy.caller.budget.remaining == 0
    stopped = kinds(result.log, "stopped")
    assert stopped == [stopped[0]] and stopped[0]["reason"] == "call budget exhausted"
    assert kinds(result.log, "finish")
