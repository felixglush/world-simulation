import json
import sqlite3

from bakery_world.history import generate_history
from bakery_world.llm import CallBudget, Caller, FakeChatModel, Reply
from bakery_world.records import TextItem
from bakery_world.writer import CACHE_FILE, Writer, family


def fake_writer(messages, tools) -> Reply:
    items = json.loads(messages[1]["content"])["items"]
    texts = [{"id": i["id"], "text": f"Fake {family(i['kind'])} text."} for i in items]
    if items[0]["kind"].startswith("news_"):
        texts[0]["text"] = "Founded in 1887, the chain is everywhere."
    return Reply(json.dumps({"items": texts}))


def refuse_to_be_called(messages, tools) -> Reply:
    raise AssertionError("the cache should have answered")


def test_model_text_lands_in_the_records_and_the_cache_freezes_it(tmp_path) -> None:
    model = FakeChatModel(fake_writer, name="fake-writer")
    budget = CallBudget(100)
    first = generate_history(1, tmp_path, writer=Writer(Caller(budget), model))
    records = (tmp_path / "records.sqlite").read_bytes()
    cache = (tmp_path / CACHE_FILE).read_bytes()
    conn = sqlite3.connect(first.records)
    reviews = {t for (t,) in conn.execute("SELECT text FROM reviews")}
    news = [b for (b,) in conn.execute("SELECT body FROM documents WHERE kind = 'news'")]
    notes = {b for (b,) in conn.execute("SELECT body FROM documents WHERE kind = 'notebook'")}
    conn.close()
    assert reviews == {"Fake review text."}
    assert notes == {"Fake notebook text."}
    assert "1887" not in news[0] and news[1:] == ["Fake news text."] * (len(news) - 1)
    assert budget.used == len(model.seen) <= 30
    assert first.text.written + first.text.fell_back == len(json.loads(cache)["items"])

    again = Writer(Caller(CallBudget(100)), FakeChatModel(refuse_to_be_called))
    second = generate_history(1, tmp_path, writer=again)
    assert again.caller.budget.used == 0 and second.text.cached == first.text.written + 1
    assert (tmp_path / "records.sqlite").read_bytes() == records
    assert (tmp_path / CACHE_FILE).read_bytes() == cache


def test_an_exhausted_budget_leaves_the_rest_as_templates(tmp_path) -> None:
    items = [
        TextItem(f"review:{n}", "review", {"rating": 4, "item": "scone"}, "Good scone.")
        for n in range(1, 41)
    ]
    outcome = Writer(Caller(CallBudget(1)), FakeChatModel(fake_writer)).write(
        items, tmp_path / CACHE_FILE
    )
    assert outcome.written == 30 and outcome.unattempted == 10
    assert set(outcome.texts) == {f"review:{n}" for n in range(1, 31)}
