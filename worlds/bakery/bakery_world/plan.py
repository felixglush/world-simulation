import json
from dataclasses import dataclass
from enum import StrEnum, auto

from bakery_world.basics import date_of
from bakery_world.llm import ToolError

MAX_PLAN_ITEMS = 20
MAX_TEXT_CHARS = 400
MAX_OPEN_EXPERIMENTS = 12
MAX_REVIEW_WEEKS = 26
SHOWN_CONCLUDED = 5
ACTION_DAYS = 28
MAX_ACTIONS_SHOWN = 80
MAX_ACTION_CHARS = 240
ACTION_MARKERS = ('"kind": "action"', '"kind": "refusal"')


class PlanState(StrEnum):
    TODO = auto()
    DOING = auto()
    DONE = auto()
    DROPPED = auto()


@dataclass(frozen=True)
class PlanItem:
    text: str
    state: PlanState


class ExperimentState(StrEnum):
    RUNNING = "running"
    DUE = "due for review"
    CONCLUDED = "concluded"


@dataclass
class Experiment:
    name: str
    hypothesis: str
    change: str
    metric: str
    review_after_weeks: int
    start_day: int
    result: str | None = None
    decision: str | None = None
    concluded_day: int | None = None

    @property
    def due_day(self) -> int:
        return self.start_day + 7 * self.review_after_weeks

    def state(self, day: int) -> ExperimentState:
        if self.concluded_day is not None:
            return ExperimentState.CONCLUDED
        return ExperimentState.DUE if day >= self.due_day else ExperimentState.RUNNING

    def weeks_run(self, day: int) -> int:
        return (
            (self.concluded_day if self.concluded_day is not None else day) - self.start_day
        ) // 7


@dataclass(frozen=True)
class ActionRecord:
    day: int
    action: str
    args: dict
    outcome: str
    refused: bool


def _text(args: dict, key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"error: {key} must be text")
    if len(value) > MAX_TEXT_CHARS:
        raise ToolError(f"error: keep {key} under {MAX_TEXT_CHARS} characters")
    return value.strip()


def parse_plan(items: object) -> tuple[PlanItem, ...]:
    if not isinstance(items, list) or len(items) > MAX_PLAN_ITEMS:
        raise ToolError(f"error: items must be a list of at most {MAX_PLAN_ITEMS} plan items")
    states = ", ".join(PlanState)
    plan = []
    for n, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != {"text", "state"}:
            raise ToolError(f"error: item {n} must have exactly text and state")
        if item["state"] not in set(PlanState):
            raise ToolError(f"error: item {n} state must be one of {states}")
        plan.append(PlanItem(_text(item, "text"), PlanState(item["state"])))
    return tuple(plan)


def start_experiment(experiments: list[Experiment], args: dict, day: int) -> Experiment:
    name = _text(args, "name")
    if any(e.name == name for e in experiments):
        raise ToolError(f"error: there is already an experiment named {name!r}")
    if sum(e.concluded_day is None for e in experiments) >= MAX_OPEN_EXPERIMENTS:
        raise ToolError(f"refused: conclude one of your {MAX_OPEN_EXPERIMENTS} open experiments")
    weeks = args.get("review_after_weeks")
    if not isinstance(weeks, int) or isinstance(weeks, bool) or not 1 <= weeks <= MAX_REVIEW_WEEKS:
        raise ToolError(
            f"error: review_after_weeks must be a whole number from 1 to {MAX_REVIEW_WEEKS}"
        )
    fields = {k: _text(args, k) for k in ("hypothesis", "change", "metric")}
    experiment = Experiment(name, **fields, review_after_weeks=weeks, start_day=day)
    experiments.append(experiment)
    return experiment


def conclude_experiment(experiments: list[Experiment], args: dict, day: int) -> Experiment:
    name = _text(args, "name")
    experiment = next((e for e in experiments if e.name == name), None)
    if experiment is None:
        raise ToolError(f"error: there is no experiment named {name!r}")
    if experiment.concluded_day is not None:
        raise ToolError(f"error: {name!r} is already concluded")
    experiment.result, experiment.decision = _text(args, "result"), _text(args, "decision")
    experiment.concluded_day = day
    return experiment


def recent_actions(lines: list[str], day: int) -> tuple[ActionRecord, ...]:
    out = []
    for line in lines:
        if not any(marker in line for marker in ACTION_MARKERS):
            continue
        entry = json.loads(line)
        if entry["kind"] in ("action", "refusal") and entry["day"] >= day - ACTION_DAYS:
            refused = entry["kind"] == "refusal"
            outcome = entry["reason"] if refused else entry["result"]
            out.append(ActionRecord(entry["day"], entry["action"], entry["args"], outcome, refused))
    return tuple(out)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_ACTION_CHARS else text[: MAX_ACTION_CHARS - 1] + "…"


def show_plan(plan: tuple[PlanItem, ...]) -> str:
    if not plan:
        return "(no plan yet; write one with plan_update)"
    return "\n".join(f"{n}. [{i.state}] {i.text}" for n, i in enumerate(plan, 1))


def show_experiments(experiments: tuple[Experiment, ...], day: int) -> str:
    open_ = [e for e in experiments if e.concluded_day is None]
    done = [e for e in experiments if e.concluded_day is not None][-SHOWN_CONCLUDED:]
    lines = [
        f"- {e.name} [{e.state(day)}]: started "
        f"{date_of(e.start_day)}, {e.weeks_run(day)} of {e.review_after_weeks} weeks run, "
        f"review on {date_of(e.due_day)}. Change: {e.change} Hypothesis: {e.hypothesis} "
        f"Metric: {e.metric}"
        for e in open_
    ] or ["(none open)"]
    if done:
        lines.append("Recently concluded:")
        lines += [
            f"- {e.name} ({date_of(e.start_day)} to {date_of(e.concluded_day)}): "
            f"{e.result} Decision: {e.decision}"
            for e in done
        ]
    return "\n".join(lines)


def show_actions(actions: tuple[ActionRecord, ...]) -> str:
    if not actions:
        return "(none)"
    shown = actions[-MAX_ACTIONS_SHOWN:]
    lines = [] if len(shown) == len(actions) else [f"({len(actions) - len(shown)} older omitted)"]
    lines += [
        f"- {date_of(a.day)} {a.action} {json.dumps(a.args, sort_keys=True)} -> "
        + _clip(("refused: " if a.refused else "") + a.outcome)
        for a in shown
    ]
    return "\n".join(lines)
