"""
Run a graph conversation from the terminal.

Single message:
    uv run python run.py "quiero comprar ya"

Interactive multi-turn chat:
    uv run python run.py --chat
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import yaml
from agentflow.core.compiler import GraphCompiler
from agentflow.core.models import GraphDefinition, MessageRole
from agentflow.core.runtime import ExecutionRuntime
from agentflow.core.session_manager import InMemorySessionManager
from agentflow.core.state_manager import InMemoryStateManager
from examples.tools import register_example_tools

register_example_tools()


# ---------------------------------------------------------------------------
# Trace handler — imprime eventos del engine de forma legible
# ---------------------------------------------------------------------------

_IGNORE_KEYS = {"event_type", "execution_id", "graph_id", "timestamp"}
_STATE_SKIP = {"conversation_history"}  # campos ruidosos que no aportan al trace

def _fmt_state(state: dict) -> str:
    filtered = {k: v for k, v in state.items() if k not in _STATE_SKIP and v not in (None, "", [], {})}
    if not filtered:
        return "{}"
    parts = [f"{k}={repr(v)}" for k, v in filtered.items()]
    return "{ " + ", ".join(parts) + " }"


class TraceHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            ev = json.loads(record.getMessage())
        except (json.JSONDecodeError, TypeError):
            return

        etype = ev.get("event_type", "")
        node = ev.get("node_id", "")
        ntype = ev.get("node_type", "")

        if etype == "node.started":
            inp = _fmt_state(ev.get("input_state", {}))
            attempt = ev.get("attempt", 1)
            attempt_tag = f" (retry #{attempt})" if attempt > 1 else ""
            print(f"  ▶ [{ntype}] {node}{attempt_tag}")
            print(f"      input : {inp}")

        elif etype == "node.completed":
            updates = _fmt_state(ev.get("state_updates", {}))
            ms = ev.get("duration_ms", 0)
            out = f"output: {updates}" if ev.get("state_updates") else "output: (sin cambios)"
            print(f"  ✓ [{ntype}] {node}  {ms}ms")
            print(f"      {out}")

        elif etype == "node.failed":
            print(f"  ✗ [{ntype}] {node}  error: {ev.get('error')}")

        elif etype == "node.retrying":
            print(f"  ↺ [{ntype}] {node}  reintento en {ev.get('backoff_seconds')}s — {ev.get('error')}")

        elif etype == "transition.resolved":
            arrow = "→" if ev.get("condition_matched") else "→ (fallback)"
            print(f"  {ev.get('from_node')} {arrow} {ev.get('to_node')}")

        elif etype == "transition.no_valid_transition":
            print(f"  ✗ sin transición desde {ev.get('from_node')}")


def _setup_trace() -> None:
    log = logging.getLogger("agentflow.observability")
    log.setLevel(logging.INFO)
    log.addHandler(TraceHandler())


def load_graph(path: str) -> GraphDefinition:
    with open(path) as f:
        data = yaml.safe_load(f)
    return GraphDefinition(**data)


async def send_message(
    message: str,
    graph_def: GraphDefinition,
    compiled,
    session_manager: InMemorySessionManager,
    state_manager: InMemoryStateManager,
    session_id: str,
) -> dict:
    session = await session_manager.load_session(session_id)

    # Append user message
    await session_manager.append_message(session_id, MessageRole.USER, message)

    # History for LLM = all previous messages (before current)
    history = await session_manager.get_history_for_llm(session_id)
    history_for_llm = history[:-1]

    # Build initial state
    initial_input = dict(session.accumulated_state)
    initial_input["user_input"] = message
    initial_input["conversation_history"] = history_for_llm

    # Run graph
    exec_state = await state_manager.create_execution(graph_def, initial_input)
    runtime = ExecutionRuntime(state_manager)
    final = await runtime.run(compiled, exec_state)

    graph_state = final.graph_state
    response = graph_state.get("response") or graph_state.get("output") or ""
    if not response and final.error_message:
        response = f"[Error: {final.error_message}]"

    # Append assistant response
    await session_manager.append_message(session_id, MessageRole.ASSISTANT, response)

    # Accumulate state for next turn
    to_accumulate = {
        k: v for k, v in graph_state.items()
        if k not in ("conversation_history", "user_input")
    }
    await session_manager.update_accumulated_state(session_id, to_accumulate)

    return {
        "response": response,
        "intent": graph_state.get("intent"),
        "status": final.status.value,
        "depth": final.depth,
    }


async def run_single(message: str, yaml_path: str) -> None:
    _setup_trace()
    graph_def = load_graph(yaml_path)
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)

    sm = InMemorySessionManager()
    state_manager = InMemoryStateManager()
    session = await sm.create_session(graph_def.id, graph_def.version)

    print(f"\n{'─'*50}")
    print(f"  Mensaje: {message}")
    print(f"{'─'*50}\n")

    result = await send_message(message, graph_def, compiled, sm, state_manager, session.session_id)

    print(f"\n{'─'*50}")
    print(f"  Grafo  : {yaml_path}")
    print(f"  Status : {result['status'].upper()}")
    print(f"  Intent : {result['intent']}  (depth: {result['depth']})")
    print(f"\n  Respuesta:\n  {result['response']}\n")


async def run_chat(yaml_path: str) -> None:
    _setup_trace()
    graph_def = load_graph(yaml_path)
    compiler = GraphCompiler()
    compiled = compiler.compile(graph_def)

    sm = InMemorySessionManager()
    state_manager = InMemoryStateManager()
    session = await sm.create_session(graph_def.id, graph_def.version)

    print(f"\n{'─'*50}")
    print(f"  AgentFlow Chat — {graph_def.name}")
    print(f"  Grafo: {graph_def.id} v{graph_def.version}")
    print(f"  Session: {session.session_id[:8]}...")
    print(f"  (escribe 'salir' para terminar)")
    print(f"{'─'*50}\n")

    turn = 0
    while True:
        try:
            user_input = input("  Tú: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Conversación terminada.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("salir", "exit", "quit"):
            print("  Conversación terminada.")
            break

        turn += 1
        result = await send_message(
            user_input, graph_def, compiled, sm, state_manager, session.session_id
        )

        intent_tag = f"[{result['intent']}]" if result['intent'] else ""
        print(f"\n  Agente {intent_tag}: {result['response']}\n")

        # Show accumulated state after each turn (for debugging)
        loaded = await sm.load_session(session.session_id)
        if loaded.accumulated_state.get("intent"):
            print(f"  ─ Estado acumulado: intent={loaded.accumulated_state.get('intent')} "
                  f"| mensajes={len(loaded.history)}\n")


if __name__ == "__main__":
    args = sys.argv[1:]

    # Extract --yaml flag
    yaml_path = "examples/sales_router.yaml"
    if "--yaml" in args:
        idx = args.index("--yaml")
        try:
            yaml_path = args[idx + 1]
            args = args[:idx] + args[idx + 2:]
        except IndexError:
            print("Error: --yaml requires a path argument", file=sys.stderr)
            sys.exit(1)

    if "--chat" in args:
        asyncio.run(run_chat(yaml_path))
    else:
        msg = " ".join(a for a in args if not a.startswith("--")) or "quiero adquirir un plan"
        asyncio.run(run_single(msg, yaml_path))
