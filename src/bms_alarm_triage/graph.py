"""The LangGraph state machine.

P1-C4 chooses a single agent and nine nodes, and chooses LangGraph over
LangChain because it makes state explicit and inspectable at every node
boundary, which matters when traceability is the point of the project.

P1-C2 makes the shape of this graph a design decision rather than a
default: a fixed sequential pipeline, no dynamic planning, no ReAct style
interleaving, and no agent-chosen tool use. The order of operations is
decided here at design time and does not change at run time. That is why
this file is a straight line with no conditional edges.
"""
from __future__ import annotations

from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .audit import AuditLog
from .config import Config, load as load_config
from .model import OllamaClient
from .nodes import (
    n1_ingest,
    n2_validate,
    n3_normalize,
    n4_cluster,
    n5_preliminary_rank,
    n6_evidence,
    n7_reassess,
    n8_explain,
    n9_report,
)
from .state import TriageState

NODE_SEQUENCE = (
    ("N1_ingest", n1_ingest),
    ("N2_validate", n2_validate),
    ("N3_normalize", n3_normalize),
    ("N4_cluster", n4_cluster),
    ("N5_preliminary_rank", n5_preliminary_rank),
    ("N6_evidence", n6_evidence),
    ("N7_reassess", n7_reassess),
    ("N8_explain", n8_explain),
    ("N9_report", n9_report),
)


def build_graph():
    """Wire the nine nodes into one linear graph."""
    builder = StateGraph(TriageState)
    for name, function in NODE_SEQUENCE:
        builder.add_node(name, function)

    builder.add_edge(START, NODE_SEQUENCE[0][0])
    for (earlier, _), (later, _) in zip(NODE_SEQUENCE, NODE_SEQUENCE[1:]):
        builder.add_edge(earlier, later)
    builder.add_edge(NODE_SEQUENCE[-1][0], END)

    return builder.compile()


def run(
    alarm_export_path: Path | str,
    trend_export_path: Path | str,
    output_dir: Path | str,
    config: Config | None = None,
    config_path: Path | None = None,
    model_client=None,
) -> TriageState:
    """Run the pipeline once.

    Single shot and non-interactive, per P1-C5: the operator starts a run
    and reads the report when it finishes. model_client is injectable so
    the tests can supply the recorded responses from P0-C3 and never call
    a model.
    """
    effective_config = config if config is not None else load_config(config_path)
    client = model_client
    if client is None:
        client = OllamaClient(
            name=effective_config.model.name,
            endpoint=effective_config.model.endpoint,
            timeout_s=effective_config.model.timeout_s,
            temperature=effective_config.model.temperature,
        )

    initial: TriageState = {
        "alarm_export_path": str(alarm_export_path),
        "trend_export_path": str(trend_export_path),
        "output_dir": str(output_dir),
        "config": effective_config,
        "audit": AuditLog(),
        "model_client": client,
    }

    graph = build_graph()
    # The node count is fixed and known, so the recursion allowance is set
    # from it rather than left to a default that could mask a wiring error.
    return graph.invoke(initial, {"recursion_limit": len(NODE_SEQUENCE) + 2})
