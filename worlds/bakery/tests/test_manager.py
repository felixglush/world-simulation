import json

import pytest

from bakery_world.llm import CallBudget, Caller, FakeChatModel, Reply, ToolError, tool_call
from bakery_world.management import load, manage
from bakery_world.manager import (
    OBJECTIVE,
    SYSTEM,
    TURNS,
    Budget,
    LLMManager,
    Turn,
    TurnContext,
    TurnKind,
    turn_messages,
)
from bakery_world.memory import Handover
from bakery_world.plan import ActionRecord, Experiment, PlanItem, PlanState
from bakery_world.town import HOME_LOCATION

ANSWER = Reply("Croissant sales fell 9% last year. SQL: SELECT ...")


def step(name: str, /, **args) -> Reply:
    return Reply(None, (tool_call(name, **args),))


def end(handover: str = "Nothing new; keep the plan.") -> Reply:
    return step("end_week", handover=handover)


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
        end(),
        step("set_price", location=HOME_LOCATION, item="croissant", price_cents=455),
        step("ask_analyst", question="How have croissant sales moved?"),
        step("set_price", location=HOME_LOCATION, item="unicorn", price_cents=400),
        end(),
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
        saved_answers=1,
        recent_questions=("How have latte sales moved?",),
        handovers=(Handover("2026-01-05", 0, "weekly", "Raised the latte price."),),
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
    policy = manager([end()] * 2, budget=300, records=seed1_history.records)
    manage(seed1_history.handoff, policy, weeks=1)
    onboarding, weekly = (m[1]["content"] for m, _ in policy.manager_model.seen)
    assert "80 model calls left this turn, 20 analyst questions left this turn, and 300" in (
        onboarding
    )
    assert "25 model calls left this turn, 4 analyst questions left this turn, and 299" in weekly


def deep_agent(messages, tools) -> Reply:
    head = messages[1]["content"]
    n = sum(m["role"] == "assistant" for m in messages)
    if "This is your onboarding" in head:
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
            end(),
        ][n]
    if "start of week 3 of" in head and n == 0:
        return step(
            "experiment_conclude",
            name="latte price",
            result="units flat, revenue up 5%",
            decision="keep 475",
        )
    return end()


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
        end(),
        end(),
        end(),
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
    elif kind is TurnKind.REVIEW:
        policy.weekly(mw, 4)
    else:
        policy.interrupt(mw, mw.curveballs[0])
    ends = [t for t in kinds(mw.log.lines, "turn") if t["phase"] == "end"]
    assert ends[0]["turn"] == kind and ends[0]["calls"] == TURNS[kind].calls
    assert ends[0]["reason"].startswith("call cap")


def test_at_most_four_analyst_questions_per_week(seed1_history) -> None:
    asks = tuple(tool_call("ask_analyst", question=f"Question {n}?") for n in range(6))
    script = [Reply(None, asks), end()]
    mw = load(seed1_history.handoff)
    policy = manager(script, records=seed1_history.records, onboarded=True)
    policy.weekly(mw, 0)
    assert len(kinds(mw.log.lines, "analyst")) == TURNS[TurnKind.WEEKLY].questions == 4
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


ANSWERS = {
    "How have croissant sales moved over the past three years?": (
        "Croissant units fell from 210 to 168 a week between 2023 and 2025, mostly on weekdays."
    ),
    "What does the flour supplier charge per kg?": "Millbrook charges 98 cents per kg of flour.",
    "Which hours are busiest on Saturdays?": "Saturday sales peak from 9 to 11 in the morning.",
}


def answering_analyst(messages, tools) -> Reply:
    return Reply(ANSWERS[messages[1]["content"]])


def recalling_agent(messages, tools) -> Reply:
    head = messages[1]["content"]
    n = sum(m["role"] == "assistant" for m in messages)
    if "This is your onboarding" in head and n < len(ANSWERS):
        return step("ask_analyst", question=list(ANSWERS)[n])
    if "start of week 3 of" in head and n == 0:
        return Reply(
            None,
            (
                tool_call("recall", query="trend in croissants sold"),
                tool_call("recall", query="what we pay per kg of flour in 2025"),
            ),
        )
    return end()


def test_recall_finds_an_onboarding_answer_by_a_paraphrase(seed1_history) -> None:
    policy = manager(
        recalling_agent,
        records=seed1_history.records,
        analyst=FakeChatModel(answering_analyst, name="fake-analyst"),
    )
    result = manage(seed1_history.handoff, policy, weeks=3)
    saved = kinds(result.log, "analyst")
    assert [(a["turn"], a["week"]) for a in saved] == [("onboarding", 0)] * 3
    recalls = [t["result"] for t in kinds(result.log, "tool_call") if t["tool"] == "recall"]
    paraphrase, unrelated = recalls
    top = json.loads(paraphrase)[0]
    assert top["question"].startswith("How have croissant sales") and top["date"] == "2026-01-01"
    assert json.loads(unrelated)[0]["question"] == "What does the flour supplier charge per kg?"
    seen = [m[1]["content"] for m, _ in policy.manager_model.seen]
    week3 = next(text for text in seen if "week 3 of" in text)
    assert "Saved analyst answers: 3." in week3
    assert "- Which hours are busiest on Saturdays?" in week3


def test_end_week_needs_a_handover_and_the_next_turn_shows_it(seed1_history) -> None:
    script = [
        step("end_week"),
        step("end_week", handover="  "),
        end("Saw weekday croissant waste. Cut the Tuesday bake. Check waste next week."),
        Reply("I am done."),
        end(),
    ]
    policy = manager(script, records=seed1_history.records)
    result = manage(seed1_history.handoff, policy, weeks=2)
    refusals = [t["result"] for t in kinds(result.log, "tool_call")][:2]
    assert all(r.startswith("error: write a handover") for r in refusals)
    week1, week2 = (m[1]["content"] for m, _ in policy.manager_model.seen[3:5])
    heading = "Handover from your last turn, 2026-01-01 (onboarding turn, week 1):\n"
    assert heading + "Saw weekday croissant waste." in week1
    assert week1.index(heading) < week1.index("Budget:")
    assert "(weekly turn, week 1):\n(none written)" in week2
    ends = [t["handover"] for t in kinds(result.log, "turn") if t["phase"] == "end"]
    assert ends[1:] == ["(none written)", "Nothing new; keep the plan."]


def reviewing_agent(messages, tools) -> Reply:
    head = messages[1]["content"].split("\n\n")[0]
    n = sum(m["role"] == "assistant" for m in messages)
    if "monthly review" in head:
        if n == 0:
            return step("notes_write", text="Review: the latte rise held; test croissant next.")
        return step("end_review", summary="Latte rise worked; croissant bake is next.")
    return end(f"Handover written {head[9:40]}.")


def test_a_monthly_review_comes_before_week_5_and_rewrites_the_notes(seed1_history) -> None:
    policy = manager(reviewing_agent, records=seed1_history.records)
    result = manage(seed1_history.handoff, policy, weeks=6)
    turns = kinds(result.log, "turn")
    starts = [(t["turn"], t["week"]) for t in turns if t["phase"] == "start"]
    starts = [s for s in starts if s[0] != "interrupt"]
    assert starts == [("onboarding", 0)] + [("weekly", w) for w in range(4)] + [
        ("review", 4),
        ("weekly", 4),
        ("weekly", 5),
    ]
    review = next(i for i, t in enumerate(turns) if t["turn"] == "review")
    earlier = [t["handover"] for t in turns[:review] if t["phase"] == "end"][-4:]
    seen = policy.manager_model.seen
    (messages, tools), *_ = [(m, t) for m, t in seen if "monthly review" in m[1]["content"]]
    text = messages[1]["content"]
    assert text.startswith("Today is 2026-01-29. This is your monthly review, before week 5")
    assert all(h in text for h in earlier) and len(set(earlier)) == 4
    assert text.index(earlier[0]) < text.index(earlier[-1]) < text.index("Budget: 15 model")
    names = {t["function"]["name"] for t in tools}
    assert {"recall", "ask_analyst", "calculate", "notes_write", "plan_update"} <= names
    assert {"experiment_start", "experiment_conclude", "end_review", "view_dashboard"} <= names
    assert not names & {"set_price", "set_bake_plan", "end_week", "hire"}
    week5 = next(m[1]["content"] for m, _ in seen if "start of week 5 of" in m[1]["content"])
    assert "(review turn, week 5):\nLatte rise worked; croissant bake is next." in week5
    assert "Your notes:\nReview: the latte rise held; test croissant next." in week5
    lowered = (text + json.dumps(tools)).lower()
    assert [word for word in FORBIDDEN if word in lowered] == []


def test_the_review_lists_only_experiments_from_its_four_weeks() -> None:
    old = Experiment("old", "h", "c", "m", 1, 1060, "r", "d", concluded_day=1090)
    closed = Experiment("closed", "h", "c", "m", 1, 1095, "r", "d", concluded_day=1110)
    running = Experiment("running", "h", "c", "m", 8, 1100)
    ctx = TurnContext(
        TurnKind.REVIEW,
        "2026-01-29",
        4,
        52,
        (),
        (),
        "",
        day=1123,
        experiments=(old, closed, running),
    )
    text = turn_messages(ctx)[1]["content"]
    assert "- closed (" in text and "- running [running]" in text and "- old" not in text
