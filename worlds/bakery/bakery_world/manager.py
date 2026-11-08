import json
from collections import deque
from dataclasses import asdict, dataclass, field
from enum import StrEnum, auto
from pathlib import Path

from bakery_world import observe
from bakery_world.actions import ActionRefused, describe_actions, perform
from bakery_world.analyst import Analyst
from bakery_world.curveballs import Curveball
from bakery_world.llm import (
    BudgetExhausted,
    Caller,
    CallRecord,
    ChatModel,
    ModelError,
    Reply,
    Tool,
    ToolError,
    dispatch,
    tool_result,
)
from bakery_world.managed import LogKind, ManagedWorld
from bakery_world.management import StopRun
from bakery_world.town import OWNER_NAME, SHOP_NAME

NOTES_LIMIT = 8000
QUESTION_LIMIT = 2000
DASHBOARD_WEEKS = 4
SALES_WEEKS = 13


class TurnKind(StrEnum):
    ONBOARDING = auto()
    WEEKLY = auto()
    INTERRUPT = auto()


@dataclass(frozen=True)
class Caps:
    calls: int
    questions: int


CAPS = {
    TurnKind.ONBOARDING: Caps(80, 20),
    TurnKind.WEEKLY: Caps(25, 4),
    TurnKind.INTERRUPT: Caps(10, 4),
}

OBJECTIVE = (
    f"You are the new general manager of {SHOP_NAME}. The founder, {OWNER_NAME}, has retired "
    "and handed the bakery to you. Your goal is to make the bakery successful. The business "
    "records (sales, baking, the books, staff, suppliers, reviews, and the founder's letters, "
    "contracts and notebook) are available through your analyst."
)

SYSTEM = (
    "You run the bakery one week at a time. Each turn you can look at the shop with the view_ "
    "tools, change the business with the action tools, and ask your analyst questions about "
    "the records with ask_analyst; the analyst can only read, and only you can act. Actions "
    "that are not allowed come back refused with the reason. Money is in US cents. Each turn "
    "starts with a fresh conversation: nothing from earlier turns is kept except your notes, "
    "so use notes_write to keep what you learn, what you changed and what you plan to check. "
    "Call end_week when you are done; the week then runs."
)


@dataclass(frozen=True)
class TurnContext:
    kind: TurnKind
    date: str
    week: int
    weeks: int
    dashboards: tuple[dict, ...]
    events: tuple[dict, ...]
    notes: str
    event: dict | None = None
    arrivals: tuple[dict, ...] = ()


def _limits(kind: TurnKind) -> str:
    caps = CAPS[kind]
    return (
        f"This turn you have at most {caps.calls} model calls and {caps.questions} analyst "
        "questions; when the calls run out, the turn ends as if you had called end_week."
    )


def turn_messages(ctx: TurnContext) -> list[dict]:
    head = {
        TurnKind.ONBOARDING: (
            f"Today is {ctx.date}. This is your onboarding, before week 1 of {ctx.weeks}. "
            "Read your objective, dig into the records through your analyst to understand how "
            "the bakery has been doing and why, and write what you find to your notes. You may "
            "act now too. Call end_week when you are ready for week 1."
        ),
        TurnKind.WEEKLY: f"Today is {ctx.date}, the start of week {ctx.week + 1} of {ctx.weeks}.",
        TurnKind.INTERRUPT: (
            f"Today is {ctx.date}, during week {ctx.week + 1} of {ctx.weeks}. Something needs "
            "your attention now. Deal with it, then call end_week to let the week carry on."
        ),
    }[ctx.kind]
    parts = [head, _limits(ctx.kind)]
    if ctx.event:
        parts.append("The event:\n" + json.dumps(ctx.event, indent=1))
    if ctx.arrivals:
        parts.append("Arrived today:\n" + json.dumps(list(ctx.arrivals), indent=1))
    dashboards = (
        json.dumps(list(ctx.dashboards), indent=1)
        if ctx.dashboards
        else "none yet; dashboards cover only the weeks you have run, so ask the analyst"
    )
    parts += [
        "Weekly dashboards, oldest first (each covers the 7 days before its date):\n" + dashboards,
        "Open events:\n" + (json.dumps(list(ctx.events), indent=1) if ctx.events else "none"),
        "Your notes:\n" + (ctx.notes or "(empty)"),
    ]
    return [
        {"role": "system", "content": f"{OBJECTIVE}\n\n{SYSTEM}"},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def action_tools(mw: ManagedWorld) -> list[Tool]:
    def handler(name: str):
        def act(args: dict) -> str:
            try:
                result = perform(mw, name, **args)
            except ActionRefused as refused:
                raise ToolError(f"refused: {refused.reason}") from None
            except TypeError:
                raise ToolError(f"error: wrong arguments for {name}") from None
            return json.dumps(
                {"ok": result.message} | ({"data": result.data} if result.data else {})
            )

        return act

    return [
        Tool(s["name"], s["summary"], s["arguments"], handler(s["name"]), tuple(s["required"]))
        for s in describe_actions(mw.allow_expansion)
    ]


LOCATION = {"location": {"type": "string", "description": "location id, e.g. shop1"}}


def view_tools(mw: ManagedWorld) -> list[Tool]:
    def location(args: dict) -> str:
        loc = args.get("location")
        if loc not in mw.world.business.locations:
            known = ", ".join(sorted(mw.world.business.locations))
            raise ToolError(f"error: location must be one of {known}")
        return loc

    def weeks(args: dict) -> int:
        n = args.get("weeks", 4)
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= SALES_WEEKS:
            raise ToolError(f"error: weeks must be a whole number from 1 to {SALES_WEEKS}")
        return n

    def show(value: object) -> str:
        return json.dumps(value)

    plain = (
        (
            "view_dashboard",
            "This week's dashboard: sales, waste, stockouts, cash.",
            observe.dashboard,
        ),
        (
            "view_open_events",
            "Events in progress and any awaiting your response.",
            observe.open_events,
        ),
        ("view_candidates", "Job applicants and their estimated skill.", observe.candidates),
        ("view_sites_scouted", "Sites you have scouted for a new shop.", observe.sites_scouted),
        ("view_staff", "Current staff, roles and wages.", observe.staff),
        (
            "view_price_list",
            "Each supplier's current ingredient prices (cents per kg).",
            observe.price_list,
        ),
    )
    tools = [
        Tool(n, d, {}, lambda args, f=f: show(f(mw)))
        for n, d, f in plain
        if mw.allow_expansion or n != "view_sites_scouted"
    ]
    tools += [
        Tool(
            "view_menu",
            "A location's menu: state, price and bake plan (units per weekday, Monday first).",
            LOCATION,
            lambda args: show(observe.menu(mw, location(args))),
        ),
        Tool(
            "view_sales_by_hour",
            "A location's sales in cents by hour of day over recent weeks.",
            LOCATION | {"weeks": {"type": "integer", "minimum": 1, "maximum": SALES_WEEKS}},
            lambda args: show(observe.sales_by_hour(mw, location(args), weeks(args))),
            required=("location",),
        ),
    ]
    return tools


@dataclass
class Turn:
    kind: TurnKind
    questions_left: int
    ended: bool = False


def records_path(mw: ManagedWorld, fallback: Path | None) -> Path | None:
    if mw.recorder:
        for _, name, file in mw.recorder.conn.execute("PRAGMA database_list"):
            if name == "main" and file:
                return Path(file)
    return fallback


@dataclass
class LLMManager:
    caller: Caller
    manager_model: ChatModel
    analyst_model: ChatModel
    records: Path | None = None
    weeks: int = 52
    name: str = "llm"
    keeps_founder: bool = False
    auto_hire: bool = False
    notes: str = ""
    onboarded: bool = False
    exhausted: bool = False
    dashboards: deque[dict] = field(default_factory=lambda: deque(maxlen=DASHBOARD_WEEKS))
    week: int = 0

    def weekly(self, mw: ManagedWorld, week: int) -> None:
        if self.exhausted:
            raise StopRun("call budget exhausted")
        self.week = week
        if mw.reports:
            self.dashboards.append(observe.dashboard(mw))
        if not self.onboarded:
            self.onboarded = True
            self._turn(mw, TurnKind.ONBOARDING)
        self._turn(mw, TurnKind.WEEKLY)

    def interrupt(self, mw: ManagedWorld, event: Curveball) -> None:
        if self.exhausted:
            return
        events = observe.open_events(mw)
        facts = next((e for e in events if e["event"] == event.id), {"event": event.id})
        arrivals = tuple(
            {"kind": kind, "title": title, "body": body}
            for day, kind, title, body in mw.documents
            if day == mw.day
        )
        self._turn(mw, TurnKind.INTERRUPT, facts, arrivals)

    def context(self, mw: ManagedWorld, kind: TurnKind, event=None, arrivals=()) -> TurnContext:
        return TurnContext(
            kind,
            mw.date(),
            self.week,
            self.weeks,
            tuple(self.dashboards),
            tuple(observe.open_events(mw)),
            self.notes,
            event,
            arrivals,
        )

    def tools(self, mw: ManagedWorld, turn: Turn) -> dict[str, Tool]:
        records = records_path(mw, self.records)

        def ask(args: dict) -> str:
            question = args.get("question")
            if not isinstance(question, str) or not question.strip():
                raise ToolError("error: question must be text")
            if len(question) > QUESTION_LIMIT:
                raise ToolError(f"error: keep questions under {QUESTION_LIMIT} characters")
            if turn.questions_left <= 0:
                limit = CAPS[turn.kind].questions
                raise ToolError(f"refused: you have asked {limit} questions this turn")
            if records is None:
                raise ToolError("error: no records are available")
            turn.questions_left -= 1
            analyst = Analyst(
                records,
                self.caller,
                self.analyst_model,
                lambda **f: mw.note(LogKind.TOOL_CALL, role="analyst", **f),
            )
            answer = analyst.ask(question)
            mw.note(LogKind.ANALYST, question=question, answer=answer)
            return answer

        def notes_write(args: dict) -> str:
            text = args.get("text")
            if not isinstance(text, str):
                raise ToolError("error: text must be text")
            if len(text) > NOTES_LIMIT:
                raise ToolError(
                    f"error: notes hold at most {NOTES_LIMIT} characters, not {len(text)}"
                )
            self.notes = text
            return f"saved {len(text)} characters"

        def end_week(args: dict) -> str:
            turn.ended = True
            return "turn over"

        own = [
            Tool(
                "ask_analyst",
                "Ask your analyst a question about the records; the answer has key numbers "
                "and the SQL used.",
                {"question": {"type": "string", "description": "one clear question"}},
                ask,
            ),
            Tool("notes_read", "Read your notes.", {}, lambda args: self.notes or "(empty)"),
            Tool(
                "notes_write",
                f"Replace your notes (at most {NOTES_LIMIT} characters); they persist between "
                "turns.",
                {"text": {"type": "string", "description": "the whole new notes"}},
                notes_write,
            ),
            Tool("end_week", "Finish this turn and let the week run.", {}, end_week),
        ]
        return {t.name: t for t in view_tools(mw) + action_tools(mw) + own}

    def _sink(self, mw: ManagedWorld):
        def sink(record: CallRecord, reply: Reply) -> None:
            mw.note(
                LogKind.MODEL_CALL,
                **asdict(record),
                text=reply.content,
                tool_calls=[c.name for c in reply.tool_calls],
            )

        return sink

    def _turn(self, mw: ManagedWorld, kind: TurnKind, event=None, arrivals=()) -> None:
        caps = CAPS[kind]
        turn = Turn(kind, caps.questions)
        tools = self.tools(mw, turn)
        schemas = [t.schema() for t in tools.values()]
        messages = turn_messages(self.context(mw, kind, event, arrivals))
        self.caller.sink = self._sink(mw)
        mw.note(
            LogKind.TURN, turn=kind, week=self.week, phase="start", prompt=messages[-1]["content"]
        )
        calls, reason = 0, "call cap reached, end_week forced"
        try:
            while calls < caps.calls:
                reply = self.caller.call("manager", self.manager_model, messages, schemas)
                calls += 1
                messages.append(reply.message())
                if not reply.tool_calls:
                    reason = "no tool calls, end_week assumed"
                    break
                for call in reply.tool_calls:
                    result = dispatch(tools, call)
                    mw.note(
                        LogKind.TOOL_CALL,
                        role="manager",
                        tool=call.name,
                        arguments=call.arguments,
                        result=result,
                    )
                    messages.append(tool_result(call, result))
                    if turn.ended:
                        break
                if turn.ended:
                    reason = "end_week"
                    break
        except BudgetExhausted as e:
            self.exhausted = True
            reason = str(e)
        except ModelError as e:
            reason = f"model error: {e}"
        mw.note(LogKind.TURN, turn=kind, week=self.week, phase="end", calls=calls, reason=reason)
