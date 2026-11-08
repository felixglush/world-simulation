import sqlite3
import time
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from bakery_world.basics import START_DATE
from bakery_world.llm import (
    Caller,
    ChatModel,
    ModelError,
    Tool,
    ToolError,
    dispatch,
    tool_result,
)
from bakery_world.town import SHOP_NAME

MAX_ROWS = 200
MAX_CELL_CHARS = 300
MAX_CALLS = 10
MAX_ANSWER_WORDS = 300
QUERY_SECONDS = 10.0
NO_ANSWER = "The analyst gave no answer."
READ_ONLY_ACTIONS = frozenset(
    {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
)

OnTool = Callable[..., None]


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON")
    return conn


def _authorize(action: int, *_: object) -> int:
    return sqlite3.SQLITE_OK if action in READ_ONLY_ACTIONS else sqlite3.SQLITE_DENY


def _cell(value: object) -> str:
    text = "NULL" if value is None else str(value).replace("\t", " ").replace("\n", " ")
    return text if len(text) <= MAX_CELL_CHARS else text[: MAX_CELL_CHARS - 1] + "…"


def query(path: Path, sql: str) -> str:
    words = sql.strip().split(None, 1)
    if not words or words[0].upper() not in ("SELECT", "WITH"):
        raise ToolError("refused: only a single SELECT or WITH query is allowed")
    deadline = time.monotonic() + QUERY_SECONDS
    conn = connect(path)
    conn.set_authorizer(_authorize)
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    try:
        cursor = conn.execute(sql)
        rows = cursor.fetchmany(MAX_ROWS + 1)
        columns = [d[0] for d in cursor.description or ()]
    except (sqlite3.Error, sqlite3.Warning) as e:
        reason = f"query stopped after {QUERY_SECONDS:.0f} s" if str(e) == "interrupted" else e
        raise ToolError(f"error: {reason}") from None
    finally:
        conn.close()
    lines = ["\t".join(columns)] + ["\t".join(_cell(v) for v in row) for row in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        lines.append(f"(first {MAX_ROWS} rows only; aggregate or add LIMIT)")
    elif not rows:
        lines.append("(no rows)")
    return "\n".join(lines)


def list_documents(path: Path, kind: str | None = None) -> str:
    sql = "SELECT doc_id, date, kind, title FROM documents"
    sql += " WHERE kind = ? ORDER BY doc_id" if kind else " ORDER BY doc_id"
    with closing(connect(path)) as conn:
        rows = conn.execute(sql, (kind,) if kind else ()).fetchall()
    lines = ["doc_id\tdate\tkind\ttitle"] + ["\t".join(_cell(v) for v in r) for r in rows]
    return "\n".join(lines) if rows else "(no documents)"


def read_document(path: Path, doc_id: int) -> str:
    with closing(connect(path)) as conn:
        row = conn.execute(
            "SELECT date, kind, title, body FROM documents WHERE doc_id = ?", (doc_id,)
        ).fetchone()
    if row is None:
        raise ToolError(f"error: there is no document {doc_id}")
    date, kind, title, body = row
    return f"{title}\n{date} ({kind})\n\n{body}"


def schema(path: Path) -> str:
    with closing(connect(path)) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        lines = []
        for table in tables:
            cols = ", ".join(f"{c[1]} {c[2]}" for c in conn.execute(f"PRAGMA table_info({table})"))
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            lines.append(f"- {table}({cols}): {count:,} rows")
        first, last = conn.execute("SELECT MIN(date), MAX(date) FROM sales").fetchone()
        kinds = [r[0] for r in conn.execute("SELECT DISTINCT kind FROM documents ORDER BY kind")]
    lines.append(f"Sales run from {first} to {last}. Document kinds: {', '.join(kinds)}.")
    return "\n".join(lines)


def system_prompt(schema_text: str) -> str:
    return (
        f"You are the analyst for {SHOP_NAME}. The general manager asks you questions about the "
        "business and you answer from its records. You can read the records but cannot change "
        "anything in the business.\n\n"
        f"Tools: query_records runs one read-only SELECT or WITH query on the SQLite records and "
        f"returns at most {MAX_ROWS} rows; list_documents lists the letters, contracts, news "
        "and notebook entries; read_document returns one in full. Columns ending in _cents are "
        f"US cents. The day column counts days from {START_DATE.isoformat()} (day 0).\n\n"
        f"Schema:\n{schema_text}\n\n"
        f"You have at most {MAX_CALLS} model calls for this question. Answer in at most "
        f"{MAX_ANSWER_WORDS} words: give the key numbers, then the SQL you ran."
    )


LAST_CALL = "This is your last call. Answer now with what you have, without tools."


@dataclass
class Analyst:
    records: Path
    caller: Caller
    model: ChatModel
    on_tool: OnTool = lambda **fields: None

    def tools(self) -> dict[str, Tool]:
        def run_query(args: dict) -> str:
            sql = args.get("sql")
            if not isinstance(sql, str):
                raise ToolError("error: sql must be text")
            return query(self.records, sql)

        def run_list(args: dict) -> str:
            kind = args.get("kind")
            if kind is not None and not isinstance(kind, str):
                raise ToolError("error: kind must be text")
            try:
                return list_documents(self.records, kind)
            except sqlite3.Error as e:
                raise ToolError(f"error: {e}") from None

        def run_read(args: dict) -> str:
            doc_id = args.get("doc_id")
            if not isinstance(doc_id, int) or isinstance(doc_id, bool):
                raise ToolError("error: doc_id must be a whole number")
            try:
                return read_document(self.records, doc_id)
            except sqlite3.Error as e:
                raise ToolError(f"error: {e}") from None

        tools = (
            Tool(
                "query_records",
                f"Run one read-only SELECT or WITH query; at most {MAX_ROWS} rows come back.",
                {"sql": {"type": "string", "description": "one SQLite SELECT or WITH query"}},
                run_query,
            ),
            Tool(
                "list_documents",
                "List documents (id, date, kind, title), optionally of one kind.",
                {"kind": {"type": "string", "description": "document kind, e.g. notebook"}},
                run_list,
                required=(),
            ),
            Tool(
                "read_document",
                "Read one document in full.",
                {"doc_id": {"type": "integer", "description": "document id"}},
                run_read,
            ),
        )
        return {t.name: t for t in tools}

    def ask(self, question: str) -> str:
        tools = self.tools()
        schemas = [t.schema() for t in tools.values()]
        try:
            schema_text = schema(self.records)
        except sqlite3.Error as e:
            return f"The analyst could not open the records: {e}"
        messages = [
            {"role": "system", "content": system_prompt(schema_text)},
            {"role": "user", "content": question},
        ]
        for n in range(1, MAX_CALLS + 1):
            last = n == MAX_CALLS
            if last:
                messages.append({"role": "user", "content": LAST_CALL})
            try:
                reply = self.caller.call("analyst", self.model, messages, [] if last else schemas)
            except ModelError as e:
                return f"The analyst could not answer: {e}"
            messages.append(reply.message())
            if last or not reply.tool_calls:
                return reply.content or NO_ANSWER
            for call in reply.tool_calls:
                result = dispatch(tools, call)
                self.on_tool(tool=call.name, arguments=call.arguments, result=result)
                messages.append(tool_result(call, result))
        return NO_ANSWER
