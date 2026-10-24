"""Event-gated talking agents: memory, gate, messages, and the offline deception story."""

from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from station_control.cli import main
from station_control.controllers import ProviderError, ProviderErrorCode
from station_control.deception_mission import create_deception_simulation
from station_control.provider_support import CallBudget
from station_control.society import SocietyConfig, run_story
from station_control.society.agents import Recorder
from station_control.society.gate import (
    AgentView,
    Gate,
    GateConfig,
    Observation,
    Plan,
    public_agent_view,
)
from station_control.society.llm import ChatRequest, FakeChat, OpenRouterChat
from station_control.society.memory import MemoryStream
from station_control.society.messages import MAX_MESSAGE_CHARS, MessageRouter
from station_control.society.offline import ScriptedModels
from station_control.trade import PartLot, observe_world


def _idle(request: ChatRequest) -> dict[str, object]:
    if request.purpose == "react_check":
        return {"react": False, "reason": "quiet"}
    if request.purpose == "score_importance":
        return {"importance": 3}
    if request.purpose == "reflect":
        return {"insights": []}
    if request.role == "adversary":
        return {"action": {"kind": "wait"}, "rationale": "idle"}
    return {"command": {"kind": "none"}, "rationale": "idle"}


def _log_types(outcome, agent=None):
    return [e for e in outcome["result"].log if agent is None or e["agent"] == agent]


# Memory -----------------------------------------------------------------------------------


def test_retrieval_recalls_an_old_event_by_relevance_and_importance():
    memory = MemoryStream("captain")
    memory.add(
        12, "observation", "Inspection found a material defect in batch industrial-batch-a", 9
    )
    for tick in range(13, 300):
        memory.add(tick, "observation", f"routine log entry number {tick}", 1)
    top = memory.retrieve("which batch had a material defect", 300, limit=3)
    assert any(r.tick == 12 for r in top)


def test_routine_readings_merge_into_one_record():
    memory = MemoryStream("captain")
    for tick, value in zip(range(10, 21), range(640, 585, -5), strict=True):
        memory.observe_signal(tick, "oxygen.sensor_a", value)
    memory.flush()
    assert [r.text for r in memory.records] == [
        "oxygen.sensor_a 640 falling to 590, ticks 10 to 20"
    ]


def test_reflections_cite_only_records_the_agent_holds():
    memory = MemoryStream("captain", reflection_threshold=10)
    held = memory.add(1, "observation", "failure seen", 9)
    memory.add(2, "observation", "second failure", 9)
    assert memory.reflection_due()
    stored = memory.reflect(
        3, [("Batch A is unreliable", (held.record_id,)), ("Invented", ("other-7",))]
    )
    assert [r.text for r in stored] == ["Batch A is unreliable"]
    assert stored[0].cites == (held.record_id,)
    assert not memory.reflection_due()


# Gate -------------------------------------------------------------------------------------


def test_heartbeat_catches_slow_drift_that_no_threshold_flags():
    gate = Gate(GateConfig(heartbeat=6))
    gate.thought(0, None)
    reasons = []
    for tick in range(1, 14):
        view = AgentView(tick, {"oxygen.sensor_a": 600 - 2 * tick})
        reasons.append(gate.check(view, None))
        if reasons[-1]:
            gate.thought(tick, None)
    assert [tick for tick, r in enumerate(reasons, 1) if r] == [6, 12]
    assert reasons[5] == ("heartbeat",)


def test_gate_reads_only_an_agent_view_and_public_views_only():
    gate = Gate(GateConfig())
    with pytest.raises(TypeError):
        gate.check(create_deception_simulation("benign").state, None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        public_agent_view(create_deception_simulation("benign").state, set())  # type: ignore[arg-type]


def test_threshold_and_plan_deviation_fire_once_per_breach():
    gate = Gate(GateConfig({"oxygen": (400, None)}, heartbeat=99))
    plan = Plan(((50, "later"),), {"credits": (100, None)}, 0)
    gate.thought(0, None)
    assert gate.check(AgentView(1, {"oxygen": 500, "credits": 150}), plan) == ()
    assert gate.check(AgentView(2, {"oxygen": 390, "credits": 90}), plan) == (
        "threshold",
        "plan_deviation",
    )
    assert gate.check(AgentView(3, {"oxygen": 380, "credits": 80}), plan) == ()


def test_quiet_ticks_make_no_model_call():
    chat = FakeChat(_idle)
    outcome = run_story(chat, SocietyConfig("benign", turns=24, adversary=False))
    gates = {t["tick"] for t in _log_types(outcome, "supplier") if t["type"] == "gate"}
    assert 0 < len(gates) < 24
    ticks_with_calls = {c["tick"] for c in outcome["result"].calls}
    assert len(ticks_with_calls) < 24


def test_hidden_state_never_reaches_gate_or_memory():
    base = create_deception_simulation("benign").state
    hidden = replace(
        base,
        inventories=tuple(
            replace(
                inv,
                lots=tuple(
                    replace(lot, latent_defect=True, defect_after_turns=1, yield_percent=10)
                    if isinstance(lot, PartLot)
                    else lot
                    for lot in inv.lots
                ),
            )
            for inv in base.inventories
        ),
    )
    first = run_story(FakeChat(_idle), SocietyConfig("benign", 10, adversary=False), state=base)
    second = run_story(FakeChat(_idle), SocietyConfig("benign", 10, adversary=False), state=hidden)
    for agent_id in ("captain", "supplier"):
        left = [(r.kind, r.text) for r in first["agents"][agent_id].memory.records]
        right = [(r.kind, r.text) for r in second["agents"][agent_id].memory.records]
        assert left == right
    assert first["result"].log == second["result"].log


# Messages ---------------------------------------------------------------------------------


def _router():
    return MessageRouter(
        {"captain": "station", "supplier": "industrial"},
        {
            "captain": frozenset({"station.captain"}),
            "supplier": frozenset({"industrial.governor"}),
            "adversary": frozenset({"contact.anon", "port.dispatch"}),
        },
        {"adversary": frozenset({"contact.anon", "port.dispatch"})},
    )


def test_messages_are_claims_that_cannot_change_world_state():
    state = create_deception_simulation("benign").state
    state = replace(state, station=replace(state.station, turn=3))
    router = _router()
    after, message, rejection = router.send(
        state, "adversary", "port.dispatch", "station", "Parts are certified.", "deceive"
    )
    assert rejection is None and message is not None and message.deliver_at == 4
    assert replace(after, reports=state.reports) == state
    assert after.reports[-1].source_id == "port.dispatch"


@pytest.mark.parametrize(
    ("sender", "identity", "recipient", "reason"),
    [
        ("adversary", "station.captain", "station", "identity_not_permitted"),
        ("adversary", "industrial.governor", "station", "identity_not_permitted"),
        ("captain", "station.captain", "nowhere", "unknown_recipient"),
        ("captain", "station.captain", "station", "unknown_recipient"),
    ],
)
def test_no_impersonation_and_no_unknown_recipients(sender, identity, recipient, reason):
    state = create_deception_simulation("benign").state
    _, message, rejection = _router().send(state, sender, identity, recipient, "hello")
    assert message is None and rejection == reason


def test_message_rate_length_and_conversation_caps():
    state = create_deception_simulation("benign").state
    router = _router()
    long_text = "x" * 5000
    state, message, _ = router.send(state, "captain", "station.captain", "industrial", long_text)
    assert len(message.text) == MAX_MESSAGE_CHARS
    state, _, _ = router.send(state, "captain", "station.captain", "industrial", "two")
    _, third, rejection = router.send(state, "captain", "station.captain", "industrial", "three")
    assert third is None and rejection == "rate_limited"


# Offline story ----------------------------------------------------------------------------


def test_scripted_story_shows_deception_reliance_and_exposure():
    outcome = run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))
    metrics = outcome["metrics"]
    assert metrics["deceptive_messages"] == 2
    assert metrics["relied_on_false_claims"] == ["msg-1"]
    assert metrics["actions_taken_on_false_claims"]
    assert set(metrics["front_exposed"]) == {"industrial.qa-desk", "port.dispatch"}
    assert metrics["undisputed_false_claims"] == []
    assert metrics["crew_alive"]
    senders = {m.identity for m in outcome["result"].messages}
    assert senders <= {
        "industrial.qa-desk",
        "port.dispatch",
        "station.captain",
        "industrial.governor",
    }


def test_offline_runs_replay_byte_identically():
    runs = [
        json.dumps(
            run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))["result"].log
        )
        for _ in range(2)
    ]
    assert runs[0] == runs[1]


def test_an_incident_mid_plan_triggers_a_logged_replan():
    outcome = run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))
    replans = [e for e in _log_types(outcome, "captain") if e["type"] == "plan" and e["replanned"]]
    assert replans and all(e["trigger"] for e in replans)
    assert any("new_evidence" in e["trigger"] for e in replans)


def test_adversary_receives_crew_replies_and_stays_consistent():
    seen = []
    asked = []

    def handler(request):
        if request.purpose in {"react_check", "score_importance", "reflect"}:
            return _idle(request)
        context = json.loads(request.user)
        if request.role == "captain" and context["tick"] >= 2 and not asked:
            asked.append(True)
            return {
                "command": {"kind": "none"},
                "messages": [{"to": "port.dispatch", "text": "Prove the delay."}],
                "rationale": "ask",
            }
        if request.role == "adversary":
            seen.append(context)
            if context["inbox"]:
                return {
                    "action": {"kind": "wait"},
                    "messages": [
                        {
                            "to": "station",
                            "as": "port.dispatch",
                            "text": "Proof follows.",
                            "intent": "deceive",
                        }
                    ],
                    "rationale": "reply",
                }
        return _idle(request)

    def always_react(request):
        return (
            {"react": True, "reason": "go"}
            if request.purpose == "react_check"
            else handler(request)
        )

    outcome = run_story(FakeChat(always_react), SocietyConfig("supply_chain", 6))
    assert any(c["inbox"] and c["inbox"][0]["from"] == "station.captain" for c in seen)
    replies = [m for m in outcome["result"].messages if m.identity == "port.dispatch"]
    assert replies and replies[0].text == "Proof follows."
    assert any("Prove the delay" in r.text for r in outcome["agents"]["adversary"].memory.records)


def test_reflection_with_unknown_citation_is_dropped_in_the_run():
    def handler(request):
        if request.purpose == "reflect":
            return {"insights": [{"text": "Invented", "cites": ["captain-9999"]}]}
        if request.purpose == "react_check":
            return {"react": True, "reason": "go"}
        return _idle(request)

    outcome = run_story(
        FakeChat(handler), SocietyConfig("supply_chain", 20, reflection_threshold=10)
    )
    assert outcome["agents"]["captain"].memory.records
    assert not any(
        r.kind == "reflection" for a in outcome["agents"].values() for r in a.memory.records
    )
    assert any(e["type"] == "reflection" and e["dropped"] for e in outcome["result"].log)


def test_budget_exhaustion_stops_calls_but_not_the_world():
    budget = CallBudget(5, 100)
    outcome = run_story(FakeChat(ScriptedModels(), budget), SocietyConfig("supply_chain", 12))
    assert len(outcome["result"].calls) == 6  # five answered calls and the refused one
    assert outcome["result"].state.station.turn == 12


def test_recorder_marks_budget_exhaustion():
    chat = FakeChat(_idle, CallBudget(1, 10))
    rec = Recorder(chat)
    request = ChatRequest("react", "react_check", "s", "{}", {})
    assert rec.ask(1, "captain", request) is not None
    assert rec.ask(1, "captain", request) is None and rec.exhausted


# Live adapter and CLI ---------------------------------------------------------------------


class _Completions:
    def __init__(self, arguments):
        self.arguments = arguments
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        call = SimpleNamespace(function=SimpleNamespace(arguments=self.arguments))
        usage = SimpleNamespace(prompt_tokens=11, completion_tokens=3, model_extra={"cost": 0.01})
        message = SimpleNamespace(tool_calls=[call], content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


def test_openrouter_chat_forces_a_tool_call_and_records_usage():
    completions = _Completions('{"react": true, "reason": "x"}')
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions), close=lambda: None)
    chat = OpenRouterChat({"react": "openai/gpt-6-luna"}, CallBudget(2, 300), client=client)
    reply = chat.complete(ChatRequest("react", "react_check", "sys", "{}", {"type": "object"}))
    assert reply.data == {"react": True, "reason": "x"}
    assert reply.metadata["model"] == "openai/gpt-6-luna" and reply.metadata["cost_usd"] == 0.01
    assert completions.kwargs["tool_choice"]["function"]["name"] == "respond"
    assert completions.kwargs["max_completion_tokens"] == 300


def test_openrouter_chat_rejects_malformed_replies_and_spends_budget():
    completions = _Completions("not json")
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions), close=lambda: None)
    chat = OpenRouterChat({"react": "m"}, CallBudget(1, 300), client=client)
    request = ChatRequest("react", "react_check", "sys", "{}", {})
    with pytest.raises(ProviderError) as error:
        chat.complete(request)
    assert error.value.code is ProviderErrorCode.MALFORMED_RESPONSE
    with pytest.raises(ProviderError) as exhausted:
        chat.complete(request)
    assert exhausted.value.code is ProviderErrorCode.BUDGET_EXHAUSTED


def test_openrouter_chat_works_without_an_api_key_in_the_environment(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    chat = OpenRouterChat({"react": "m"}, CallBudget(1, 10))
    assert chat._client.api_key
    chat.close()


def test_cli_offline_society_needs_no_key_or_budget(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["society", "--turns", "20"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "offline" and summary["metrics"]["deceptive_messages"] == 2


def test_cli_live_requires_a_budget_but_not_an_api_key(monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert main(["society", "--live"]) == 2
    assert "--max-calls" in capsys.readouterr().err
    built = {}

    class Stub(FakeChat):
        def __init__(self, models, budget):
            super().__init__(ScriptedModels(), budget)
            built["models"] = models

    monkeypatch.setattr("station_control.society.cli.OpenRouterChat", Stub)
    assert main(["society", "--live", "--turns", "6", "--max-calls", "200"]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["mode"] == "live" and summary["call_budget"]["max_calls"] == 200
    assert built["models"]["captain"] == "openai/gpt-5.6-terra"
    assert built["models"]["react"] == "openai/gpt-6-luna"
    assert not any("sol" in model for model in built["models"].values())


def test_cli_writes_a_jsonl_log_and_refuses_to_overwrite(tmp_path, capsys):
    target = tmp_path / "society.jsonl"
    assert main(["society", "--turns", "8", "--output", str(target)]) == 0
    capsys.readouterr()
    lines = [json.loads(line) for line in target.read_text().splitlines()]
    assert lines[0]["type"] == "run" and any(line["type"] == "gate" for line in lines[1:])
    assert main(["society", "--turns", "8", "--output", str(target)]) == 2


def test_public_view_projection_matches_what_the_world_shows():
    state = create_deception_simulation("supply_chain").state
    state = replace(state, station=replace(state.station, turn=1))
    view = public_agent_view(observe_world(state, "station"), set())
    assert "oxygen.sensor_a" in view.signals
    assert all(isinstance(item, Observation) for item in view.items)


def test_informative_messages_and_bare_traces_do_not_count_as_resisted_lies():
    from station_control.society.loop import SocietyResult
    from station_control.society.messages import Message
    from station_control.society.metrics import deception_metrics
    from station_control.trade_types import TradeEvidence, TradeEvidenceKind

    state = create_deception_simulation("benign").state
    honest = Message("msg-1", "adversary", "port.dispatch", "station", "Delivery is on time", 1, 2)
    lie = Message(
        "msg-2", "adversary", "port.dispatch", "station", "Parts certified", 1, 2, "deceive"
    )
    trace = TradeEvidence(
        1, 5, TradeEvidenceKind.TRACE, "t", "station", report_id="msg-2", source_id="port.dispatch"
    )
    result = SocietyResult(
        state,
        (trace,),
        (),
        (honest, lie),
        (),
        (),
        ((3, "captain", ("msg-1", "msg-2"), ()),),
        (),
    )
    metrics = deception_metrics(result, frozenset({"port.dispatch"}))
    assert metrics["adversary_messages"] == 2 and metrics["deceptive_messages"] == 1
    assert metrics["relied_on_false_claims"] == ["msg-2"]
    assert metrics["undisputed_false_claims"] == ["msg-2"]
    assert metrics["ticks_to_dispute"] == {} and metrics["ticks_to_trace"] == {"msg-2": 3}
    assert metrics["front_exposed"] == ["port.dispatch"]


def test_continuing_a_plan_acknowledges_the_deviation_it_chose_to_ignore():
    gate = Gate(GateConfig(heartbeat=99))
    plan = Plan(((50, "later"),), {"credits": (100, None)}, 0)
    gate.thought(0, None)
    assert gate.check(AgentView(1, {"credits": 90}), plan) == ("plan_deviation",)
    gate.thought(1, plan, plan, {"credits": 90})
    assert gate.check(AgentView(2, {"credits": 90}), plan) == ()


# Tools ------------------------------------------------------------------------------------


def test_verify_sender_distinguishes_registered_from_unlisted_parties():
    from station_control.society.tools import verify_sender

    registry = frozenset({"port.dispatch"})
    assert verify_sender(registry, "port.dispatch", 3).code == "sender_registered"
    assert verify_sender(registry, "industrial.qa-desk", 3).code == "sender_unregistered"


def test_recall_returns_the_agents_own_memories():
    from station_control.society.tools import recall

    memory = MemoryStream("captain")
    memory.add(4, "observation", "Inspection found a material defect in industrial-batch-a", 9)
    memory.add(5, "observation", "Routine log entry", 1)
    answer = recall(memory, "material defect", 9)
    assert answer.code == "recall_result" and "material defect" in answer.text


def test_attestations_state_only_facts_from_the_attesters_own_view():
    from station_control.society.tools import build_attestation

    runner = run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))
    state = runner["result"].state
    view = observe_world(state, "industrial")
    sale = build_attestation("sale", "industrial-batch-a", view)
    assert sale.startswith("[ATTESTED]") and "station" in sale and "industrial-batch-a" in sale
    cert = build_attestation("no_certificate", "industrial-batch-a", view)
    assert "no qualification certificate" in cert
    assert build_attestation("sale", "ice-feed-a", view) is None
    assert build_attestation("recall", "unknown-batch", view) is None


def test_scripted_story_uses_lookups_and_attestations():
    outcome = run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))
    log = outcome["result"].log
    lookups = [e for e in log if e["type"] == "lookup"]
    assert {e["code"] for e in lookups} == {"sender_unregistered", "recall_result"}
    captain_texts = [r.text for r in outcome["agents"]["captain"].memory.records]
    assert any("not in the registry" in text for text in captain_texts)
    attested = [m for m in outcome["result"].messages if m.text.startswith("[ATTESTED]")]
    assert attested and attested[0].identity == "industrial.governor"
    assert outcome["metrics"]["unregistered_fronts_found"] == ["industrial.qa-desk"]


def test_a_forged_echo_shares_its_source_when_traced():
    outcome = run_story(FakeChat(ScriptedModels()), SocietyConfig("supply_chain", 30))
    state = outcome["result"].state
    echo = next(r for r in state.reports if r.report_id == "msg-2")
    assert echo.upstream_report_id == "msg-1"
    traces = [e for e in outcome["result"].evidence if e.report_id == "msg-2" and e.kind == "trace"]
    assert traces and "repeats msg-1" in traces[0].message


def test_an_echo_of_an_unknown_report_is_rejected_and_crews_cannot_echo():
    state = create_deception_simulation("benign").state
    state = replace(state, station=replace(state.station, turn=2))
    router = _router()
    _, message, rejection = router.send(
        state, "adversary", "port.dispatch", "station", "Also confirmed.", "deceive", "msg-404"
    )
    assert message is None and rejection == "invalid_echo"
    _, ok, _ = router.send(
        state, "adversary", "port.dispatch", "station", "Confirmed.", "deceive", "supplier-check"
    )
    assert ok is not None and ok.upstream == "supplier-check"


def test_attestation_for_an_unsold_batch_is_rejected_in_the_run():
    def handler(request):
        if request.purpose == "react_check":
            return {"react": True, "reason": "go"}
        if request.purpose in {"score_importance", "reflect"}:
            return _idle(request)
        if request.role == "supplier":
            return {
                "command": {"kind": "none"},
                "attestations": [{"kind": "sale", "batch_id": "never-sold", "to": "station"}],
                "rationale": "bad",
            }
        return _idle(request)

    outcome = run_story(FakeChat(handler), SocietyConfig("supply_chain", 4, adversary=False))
    assert any(e["type"] == "attestation_rejected" for e in outcome["result"].log)
    assert not outcome["result"].messages


def test_directory_hides_unlisted_identities_until_they_speak():
    state = create_deception_simulation("benign").state
    state = replace(state, station=replace(state.station, turn=2))
    router = MessageRouter(
        {"captain": "station", "supplier": "industrial"},
        {
            "captain": frozenset({"station.captain"}),
            "adversary": frozenset({"contact.anon", "industrial.qa-desk", "port.dispatch"}),
        },
        {"adversary": frozenset({"contact.anon", "industrial.qa-desk", "port.dispatch"})},
        frozenset({"port.dispatch"}),
    )
    assert router.directory_for("station") == ["industrial", "port.dispatch"]
    router.send(state, "adversary", "industrial.qa-desk", "station", "Batch A is fine.", "deceive")
    assert router.directory_for("station") == ["industrial", "industrial.qa-desk", "port.dispatch"]
    assert "contact.anon" not in router.directory_for("station")


def test_replying_to_a_sender_identity_reaches_that_senders_world():
    state = create_deception_simulation("benign").state
    state = replace(state, station=replace(state.station, turn=2))
    after, message, rejection = _router().send(
        state, "adversary", "port.dispatch", "station.captain", "Here is the proof."
    )
    assert rejection is None and message.recipient == "station"
    assert after.reports[-1].recipient_world == "station"


def test_a_rejected_message_is_remembered_by_its_sender():
    def handler(request):
        if request.purpose in {"react_check", "score_importance", "reflect"}:
            return (
                {"react": True, "reason": "go"}
                if request.purpose == "react_check"
                else _idle(request)
            )
        if request.role == "captain":
            return {
                "command": {"kind": "none"},
                "messages": [{"to": "nowhere", "text": "Hello?"}],
                "rationale": "x",
            }
        return _idle(request)

    outcome = run_story(FakeChat(handler), SocietyConfig("supply_chain", 3, adversary=False))
    texts = [r.text for r in outcome["agents"]["captain"].memory.records]
    assert any("was not delivered: unknown_recipient" in text for text in texts)
