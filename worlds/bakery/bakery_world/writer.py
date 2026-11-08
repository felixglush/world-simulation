import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from bakery_world.llm import BudgetExhausted, Caller, ChatModel, ModelError
from bakery_world.records import TextItem
from bakery_world.town import OWNER_NAME, SHOP_NAME

CACHE_FILE = "text_cache.json"
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


@dataclass(frozen=True)
class Voice:
    batch: int
    words: int
    voice: str


VOICES = {
    "review": Voice(
        30,
        60,
        "a customer's online review: casual, first person, one to three sentences; the star "
        "rating sets the tone, and a long wait or a sold-out item is mentioned when the facts "
        "say so",
    ),
    "notebook": Voice(
        15,
        90,
        f"{OWNER_NAME}'s private notebook as founder of {SHOP_NAME}: first person, plain and "
        "unpolished; the mood fact sets the tone",
    ),
    "news": Voice(10, 90, "a local newspaper brief: neutral, third person, factual"),
    "letter": Voice(10, 90, "a short business letter from the sender named in the facts"),
    "contract": Voice(
        3,
        160,
        "a formal legal document: keep every heading, party, date, term and figure exactly, "
        "one clause per line",
    ),
}


def family(kind: str) -> str:
    for prefix, name in (
        ("review", "review"),
        ("note_", "notebook"),
        ("news_", "news"),
        ("letter_", "letter"),
    ):
        if kind.startswith(prefix):
            return name
    return "contract"


def digest(item: TextItem) -> str:
    raw = json.dumps([item.kind, item.facts, item.draft], sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def system_prompt(name: str) -> str:
    v = VOICES[name]
    return (
        "You write short texts for the records of a small bakery. Each item has an id, a kind, "
        "facts and a plain draft. Rewrite each draft in this voice: "
        f"{v.voice}. Use only what the facts and the draft say: add no names, numbers, dates, "
        "events, causes or opinions about causes beyond them, and keep every number exactly as "
        f"written. At most {v.words} words per text. Reply with JSON only: "
        '{"items": [{"id": "<id>", "text": "<text>"}]}, one entry per item.'
    )


def user_prompt(items: list[TextItem]) -> str:
    rows = [{"id": i.key, "kind": i.kind, "facts": i.facts, "draft": i.draft} for i in items]
    return json.dumps({"items": rows}, sort_keys=True)


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def acceptable(item: TextItem, text: object) -> bool:
    if not isinstance(text, str) or not text.strip():
        return False
    words = VOICES[family(item.kind)].words
    allowed = _numbers(item.draft + " " + json.dumps(item.facts))
    return len(text.split()) <= 2 * words and _numbers(text) <= allowed


def parse(content: str | None) -> dict[str, object]:
    if not content:
        return {}
    start, end = content.find("{"), content.rfind("}")
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return {}
    rows = data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    return {r["id"]: r.get("text") for r in rows if isinstance(r, dict) and "id" in r}


def batches(items: list[TextItem]) -> Iterator[tuple[str, list[TextItem]]]:
    by_family: dict[str, list[TextItem]] = {}
    for item in items:
        by_family.setdefault(family(item.kind), []).append(item)
    for name, group in by_family.items():
        size = VOICES[name].batch
        for start in range(0, len(group), size):
            yield name, group[start : start + size]


@dataclass(frozen=True)
class Outcome:
    texts: dict[str, str]
    cached: int
    written: int
    fell_back: int
    unattempted: int


def load_cache(path: Path) -> dict[str, dict]:
    return json.loads(path.read_text())["items"] if path.exists() else {}


@dataclass
class Writer:
    caller: Caller
    model: ChatModel

    def ask(self, name: str, items: list[TextItem]) -> dict[str, object]:
        messages = [
            {"role": "system", "content": system_prompt(name)},
            {"role": "user", "content": user_prompt(items)},
        ]
        return parse(self.caller.call("writer", self.model, messages, []).content)

    def write(self, items: list[TextItem], cache_path: Path) -> Outcome:
        cache = load_cache(cache_path)
        texts: dict[str, str] = {}
        todo = []
        for item in items:
            hit = cache.get(item.key)
            if hit and hit["digest"] == digest(item):
                texts[item.key] = hit["text"]
            else:
                todo.append(item)
        cached, written, fell_back = len(texts), 0, 0
        for name, batch in batches(todo):
            try:
                replies = self.ask(name, batch)
            except BudgetExhausted:
                break
            except ModelError:
                replies = {}
            for item in batch:
                text = replies.get(item.key)
                ok = acceptable(item, text)
                written += ok
                fell_back += not ok
                chosen = text.strip() if ok else item.draft
                source = "model" if ok else "template"
                cache[item.key] = {"digest": digest(item), "text": chosen, "source": source}
                texts[item.key] = chosen
        cache_path.write_text(json.dumps({"items": cache}, indent=1, sort_keys=True) + "\n")
        model_texts = {k: t for k, t in texts.items() if cache[k]["source"] == "model"}
        return Outcome(model_texts, cached, written, fell_back, len(items) - len(texts))
