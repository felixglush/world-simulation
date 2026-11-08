from types import SimpleNamespace

import pytest

from bakery_world.analyst import MAX_CALLS, MAX_ROWS, Analyst, query
from bakery_world.llm import (
    PLACEHOLDER_KEY,
    BudgetExhausted,
    CallBudget,
    Caller,
    FakeChatModel,
    OpenRouterChat,
    Reply,
    ToolError,
    Usage,
    tool_call,
)
from bakery_world.records import content_hash


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM reviews",
        "UPDATE reviews SET rating = 5",
        "ATTACH DATABASE ':memory:' AS scratch",
        "SELECT 1; DELETE FROM reviews",
        "WITH x AS (SELECT 1) DELETE FROM reviews",
        "  drop table reviews",
        "PRAGMA writable_schema = ON",
    ],
)
def test_the_analyst_refuses_writes_and_leaves_the_records_alone(seed1_history, sql) -> None:
    path = seed1_history.records
    before = path.read_bytes()
    with pytest.raises(ToolError):
        query(path, sql)
    assert path.read_bytes() == before
    assert content_hash(path) == seed1_history.content_hash


def test_a_select_returns_rows_with_a_header(seed1_history) -> None:
    out = query(seed1_history.records, "SELECT rating, COUNT(*) AS n FROM reviews GROUP BY rating")
    lines = out.splitlines()
    assert lines[0] == "rating\tn"
    assert sum(int(line.split("\t")[1]) for line in lines[1:]) > 100


def test_at_most_200_rows_come_back(seed1_history) -> None:
    lines = query(seed1_history.records, "SELECT sale_id FROM sales").splitlines()
    assert len(lines) == 1 + MAX_ROWS + 1
    assert "first 200 rows only" in lines[-1]


def test_the_analyst_answers_after_querying(seed1_history) -> None:
    model = FakeChatModel(
        [
            Reply(None, (tool_call("query_records", sql="SELECT COUNT(*) FROM reviews"),)),
            Reply("There are 340 reviews. SQL: SELECT COUNT(*) FROM reviews"),
        ]
    )
    seen = []
    analyst = Analyst(
        seed1_history.records, Caller(CallBudget(5)), model, lambda **f: seen.append(f)
    )
    assert analyst.ask("How many reviews?").startswith("There are")
    system = model.seen[0][0][0]["content"]
    assert "reviews(review_id INTEGER" in system and "300 words" in system
    tool_message = model.seen[1][0][-1]
    assert tool_message["role"] == "tool" and tool_message["content"].splitlines()[1].isdigit()
    assert seen[0]["tool"] == "query_records"


def test_the_analyst_must_answer_at_its_call_cap(seed1_history) -> None:
    def keep_querying(messages, tools):
        if not tools:
            return Reply("Best I have: reviews average four stars.")
        return Reply(None, (tool_call("query_records", sql="SELECT 1"),))

    model = FakeChatModel(keep_querying)
    budget = CallBudget(100)
    answer = Analyst(seed1_history.records, Caller(budget), model).ask("Why?")
    assert answer.startswith("Best I have")
    assert budget.used == len(model.seen) == MAX_CALLS
    assert model.seen[-1][1] == []


def test_the_budget_is_shared_and_stops_at_zero() -> None:
    budget = CallBudget(2)
    caller = Caller(budget)
    model = FakeChatModel([Reply("a"), Reply("b"), Reply("c")])
    caller.call("manager", model, [], [])
    caller.call("analyst", model, [], [])
    with pytest.raises(BudgetExhausted):
        caller.call("manager", model, [], [])
    assert len(model.seen) == 2


def test_openrouter_replies_become_tool_calls_and_usage(monkeypatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert OpenRouterChat("openai/gpt-6-luna").client.api_key == PLACEHOLDER_KEY
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        fn = SimpleNamespace(name="end_week", arguments="{}")
        message = SimpleNamespace(
            content=None, tool_calls=[SimpleNamespace(id="c1", type="function", function=fn)]
        )
        usage = SimpleNamespace(
            prompt_tokens=120,
            completion_tokens=8,
            prompt_tokens_details=SimpleNamespace(cached_tokens=100),
            cost=0.0012,
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    reply = OpenRouterChat("m", client=client).complete([{"role": "user", "content": "hi"}], [])
    assert reply.tool_calls[0].name == "end_week"
    assert reply.usage == Usage(120, 100, 8, 0.0012)
    assert "tools" not in sent and sent["model"] == "m"
