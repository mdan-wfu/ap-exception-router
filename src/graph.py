"""LangGraph assembly.

Reads like the flow diagram. If you are adding logic here beyond wiring,
it belongs in a node or a routing predicate — not in this file.

Node sequence:

    triage → validate → policy_gate → adjudicate →
        route_after_adjudicate:
            → critique → adjudicate           (up to MAX_CRITIC_ROUNDS)
            → scribe (ESCALATE / REJECT)      → route_after_scribe:
                                                    → human_gate (ESCALATE)
                                                        → settle → route_outcome → END
                                                    → settle (REJECT)   → route_outcome → END
            → settle (APPROVE)                → route_outcome → END

The critic conditional fires when CLAUDE.md §4 CRITIC_TRIGGER holds
(stated_total > threshold OR any HIGH+ finding). Scribe runs only for
ESCALATE / REJECT. Human gate fires only for ESCALATE — three modes:
interactive / demo (fixture) / queue (audit-store write, no resume).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from src.config import APPROVAL_THRESHOLD_USD, MAX_CRITIC_ROUNDS
from src.graph_state import GraphState
from src.nodes.adjudicate import adjudicate
from src.nodes.critique import critique
from src.nodes.human_gate import human_gate
from src.nodes.policy_gate import policy_gate
from src.nodes.route_outcome import route_outcome
from src.nodes.scribe import scribe
from src.nodes.settle import settle
from src.nodes.triage import triage
from src.nodes.validate import validate
from src.schema import Outcome, Severity


_THRESHOLD = Decimal(str(APPROVAL_THRESHOLD_USD))


def route_after_adjudicate(state: GraphState) -> str:
    """Route the graph after each `adjudicate` call.

    Two gates on continuing to `critique`:
      1. Rounds cap: never above MAX_CRITIC_ROUNDS (currently 1).
         A single critic round runs on every invoice meeting the trigger;
         there is no second round regardless of revision.
      2. Trigger: CRITIC_TRIGGER (total > threshold OR any HIGH+ finding).

    Otherwise route to `scribe` (ESCALATE / REJECT) or directly to
    `route_outcome` (APPROVE).

    A previous "convergence check" (only run round 2 if round 1 revised)
    was removed with the single-round policy — see DECISIONS.md. It became
    dead code once MAX_CRITIC_ROUNDS dropped to 1.
    """
    rounds_so_far = state.get("critic_rounds", 0)
    if rounds_so_far >= MAX_CRITIC_ROUNDS or not _critic_trigger(state):
        return _post_critic_target(state)
    return "critique"


def _post_critic_target(state: GraphState) -> str:
    """After the critic loop terminates, route based on the model's outcome.
    ESCALATE / REJECT → scribe; APPROVE → straight to settle."""
    decision = state.get("decision")
    if decision is not None and decision.outcome in (Outcome.ESCALATE, Outcome.REJECT):
        return "scribe"
    return "settle"


def route_after_scribe(state: GraphState) -> str:
    """After scribe writes its note: ESCALATE → human_gate, REJECT → settle."""
    decision = state.get("decision")
    if decision is not None and decision.outcome == Outcome.ESCALATE:
        return "human_gate"
    return "settle"


def _critic_trigger(state: GraphState) -> bool:
    invoice = state.get("invoice")
    over = (
        invoice is not None
        and invoice.stated_total is not None
        and invoice.stated_total.amount_usd > _THRESHOLD
    )
    has_high = any(
        f.severity in (Severity.HIGH, Severity.CRITICAL)
        for f in state.get("findings", [])
    )
    return over or has_high


def build_graph(checkpointer_path: Path | str | None = "runs/checkpoints.sqlite"):
    g = StateGraph(GraphState)

    g.add_node("triage", triage)
    g.add_node("validate", validate)
    g.add_node("policy_gate", policy_gate)
    g.add_node("adjudicate", adjudicate)
    g.add_node("critique", critique)
    g.add_node("scribe", scribe)
    g.add_node("human_gate", human_gate)
    g.add_node("settle", settle)
    g.add_node("route_outcome", route_outcome)

    g.add_edge(START, "triage")
    g.add_edge("triage", "validate")
    g.add_edge("validate", "policy_gate")
    g.add_edge("policy_gate", "adjudicate")

    g.add_conditional_edges(
        "adjudicate",
        route_after_adjudicate,
        {
            "critique": "critique",
            "scribe": "scribe",
            "settle": "settle",
        },
    )
    g.add_edge("critique", "adjudicate")

    g.add_conditional_edges(
        "scribe",
        route_after_scribe,
        {"human_gate": "human_gate", "settle": "settle"},
    )
    g.add_edge("human_gate", "settle")
    g.add_edge("settle", "route_outcome")
    g.add_edge("route_outcome", END)

    if checkpointer_path is None:
        # `interrupt()` and `Command(resume=...)` require a checkpointer.
        # For test paths that do not want persistence, fall back to an
        # in-memory saver rather than compiling without one — otherwise
        # any test invoice that reaches human_gate blows up at resume.
        from langgraph.checkpoint.memory import MemorySaver
        return g.compile(checkpointer=MemorySaver())

    checkpointer_path = Path(checkpointer_path)
    checkpointer_path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(checkpointer_path), check_same_thread=False)
    return g.compile(checkpointer=SqliteSaver(conn))


def run_with_human_resume(graph, initial_or_command, config) -> GraphState:
    """Drive `graph.invoke` through any number of human-gate interrupts.

    The graph pauses at `human_gate` via LangGraph's `interrupt()`. This
    helper resolves the pause according to `HUMAN_GATE_MODE` and resumes
    with `Command(resume=...)`. Loops until the graph reaches END.

    Modes:
      - "demo"        → resolve from data/fixtures/human_gate.json.
                        Non-interactive; `make demo` never hangs.
      - "interactive" → prompt on stdin; block until the clerk answers.
      - "queue"       → default the resume to HOLD so the run completes
                        with `human_queued=True`, gets a proper audit
                        record, and remains actionable through the
                        dashboard's post-completion override path.
                        A truly "leave-paused-forever" mode would strand
                        the run outside the audit store; deferring that
                        variant until there is a concrete need for it.
    """
    from src.human_gate_runner import resolve_interrupt

    result = graph.invoke(initial_or_command, config=config)
    while True:
        packet = _pending_human_gate_interrupt(result)
        if packet is None:
            return result
        answer = resolve_interrupt(packet)
        result = graph.invoke(Command(resume=answer), config=config)


def _pending_human_gate_interrupt(result) -> dict | None:
    """Extract the interrupt payload from a graph result, or None if the
    graph finished. LangGraph 1.x exposes interrupts under the
    `__interrupt__` key on the returned state (as a tuple of Interrupt
    objects). Older builds expose them via graph.get_state(config).tasks;
    the __interrupt__ shape is the newer, more direct one.
    """
    if not isinstance(result, dict):
        return None
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    first = interrupts[0]
    # Interrupt objects have `.value`; plain dicts wouldn't but we tolerate both.
    value = getattr(first, "value", first)
    return value if isinstance(value, dict) else None


def run_one(source_path: str, graph=None, thread_id: str | None = None) -> GraphState:
    graph = graph if graph is not None else build_graph()
    config = {"configurable": {"thread_id": thread_id or source_path}}
    initial: GraphState = {
        "source_path": source_path,
        "findings": [],
        "nodes_fired": [],
        "model_calls": [],
        "tool_calls": [],
        "critic_challenges": [],
        "critic_rounds": 0,
        "tool_result_cache": {},
        "human_queued": False,
    }
    try:
        return run_with_human_resume(graph, initial, config)
    except _NON_FAILURE_EXCEPTIONS:
        # CircuitBreakerTripped / CacheMissError still surface to the caller
        # unwrapped — they are already handled specifically (CLI prints a
        # friendly message; upload_run redirects with an error param).
        # Recording them as FAILED would pollute the audit store with
        # infrastructure noise unrelated to a real extraction/validation
        # crash.
        raise
    except Exception as exc:
        _persist_failed(source_path, exc)
        # Return a FAILED state so callers can react (redirect to detail,
        # print a summary) instead of choosing between an unhandled 500
        # and a bespoke try/except at every call site.
        return {
            **initial,
            "terminal_status": Outcome.FAILED,
            "failure_reason": f"{type(exc).__name__}: {exc}",
        }


# CircuitBreakerTripped and CacheMissError propagate as before — they are
# infrastructure signals with dedicated handlers, not a run failure to be
# audited. Imported lazily to avoid pulling llm modules into every graph
# consumer at import time (test fixtures that stub out the LLM entirely).
def _non_failure_exceptions() -> tuple[type[BaseException], ...]:
    from src.llm.provider import CacheMissError
    try:
        from src.llm.agent_loop import CircuitBreakerTripped
        return (CacheMissError, CircuitBreakerTripped)
    except ImportError:
        return (CacheMissError,)


_NON_FAILURE_EXCEPTIONS = _non_failure_exceptions()


def _persist_failed(source_path: str, exc: BaseException) -> None:
    """Write a FAILED audit row for an exception that escaped the graph.
    Node inference walks the traceback for the innermost frame whose file
    lives under `src/nodes/` (that's the node that raised). Non-fatal: any
    persistence failure is logged but never re-raised — the caller already
    has the original exception to react to."""
    from pathlib import PurePath
    try:
        from src.store.audit import AuditStore
        node = _infer_failing_node(exc)
        invoice_number = PurePath(source_path).name or source_path
        source_format = PurePath(source_path).suffix.lstrip(".") or None
        AuditStore().record_failed_run(
            source_file=source_path,
            invoice_number=invoice_number,
            source_format=source_format,
            error_type=type(exc).__name__,
            error_message=str(exc)[:2000],
            node=node,
        )
    except Exception as persist_exc:
        print(f"[audit] failed to persist FAILED run for {source_path}: {persist_exc}")


def _infer_failing_node(exc: BaseException) -> str | None:
    """Innermost traceback frame under src/nodes/ names the node that raised.
    Falls back to the module basename under src/adapters/ (extraction blew
    up before triage returned), or None if neither matches."""
    import traceback
    node: str | None = None
    for frame in traceback.extract_tb(exc.__traceback__):
        parts = Path(frame.filename).parts
        if "nodes" in parts:
            i = parts.index("nodes")
            if i + 1 < len(parts):
                node = Path(parts[i + 1]).stem
        elif "adapters" in parts and node is None:
            i = parts.index("adapters")
            if i + 1 < len(parts):
                node = f"adapter:{Path(parts[i + 1]).stem}"
    return node
