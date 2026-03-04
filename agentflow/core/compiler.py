"""
Graph Compiler — validates a GraphDefinition and produces a CompiledGraph.

Checks enforced (SPEC §2):
- All from_node / to_node references exist in nodes
- All nodes are reachable from entry_node (BFS) — warning if not
- No unconditional-only cycles (cycle where every edge lacks a condition) — error
- Condition fields exist in state_schema (SPEC §4.2)
- Condition value types are compatible with state_schema field types (SPEC §4.3)
- Condition operators are valid (SPEC §4.1)
"""
from __future__ import annotations

from collections import deque
from typing import Any

from agentflow.core.models import (
    CompiledGraph,
    CompiledNode,
    CompilationError,
    CompoundCondition,
    ConditionBranch,
    ConditionNodeConfig,
    EdgeCondition,
    GraphDefinition,
    NodeType,
    StateFieldType,
)


class GraphCompilationError(Exception):
    """Raised when the graph has hard errors that prevent execution."""

    def __init__(self, errors: list[CompilationError]) -> None:
        self.errors = errors
        messages = "; ".join(e.message for e in errors)
        super().__init__(f"Graph compilation failed: {messages}")


# ---------------------------------------------------------------------------
# Type compatibility table (SPEC §4.3)
# ---------------------------------------------------------------------------

_PYTHON_TYPE_FOR_FIELD_TYPE: dict[StateFieldType, tuple[type, ...]] = {
    StateFieldType.STR: (str,),
    StateFieldType.INT: (int,),
    StateFieldType.FLOAT: (float, int),   # int is valid for float comparisons
    StateFieldType.BOOL: (bool,),
    StateFieldType.LIST: (list, tuple),
    StateFieldType.DICT: (dict,),
    StateFieldType.ANY: (),               # empty = accept anything
}


def _is_type_compatible(field_type: StateFieldType, value: Any) -> bool:
    allowed = _PYTHON_TYPE_FOR_FIELD_TYPE[field_type]
    if not allowed:
        return True
    return isinstance(value, allowed)


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------

class GraphCompiler:
    def compile(self, definition: GraphDefinition) -> CompiledGraph:
        errors: list[CompilationError] = []
        warnings: list[CompilationError] = []

        self._check_edge_references(definition, errors)
        self._check_reachability(definition, warnings)
        self._check_cycles(definition, errors)
        self._check_conditions(definition, errors)

        if errors:
            raise GraphCompilationError(errors)

        nodes = self._build_compiled_nodes(definition)

        return CompiledGraph(
            definition=definition,
            nodes=nodes,
            compilation_warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Validation steps
    # ------------------------------------------------------------------

    def _check_edge_references(
        self, definition: GraphDefinition, errors: list[CompilationError]
    ) -> None:
        for edge in definition.edges:
            if edge.from_node not in definition.nodes:
                errors.append(CompilationError(
                    severity="error",
                    message=f"Edge references unknown from_node '{edge.from_node}'",
                    node_id=edge.from_node,
                ))
            if edge.to_node not in definition.nodes:
                errors.append(CompilationError(
                    severity="error",
                    message=f"Edge references unknown to_node '{edge.to_node}'",
                    node_id=edge.to_node,
                ))

    def _check_reachability(
        self, definition: GraphDefinition, warnings: list[CompilationError]
    ) -> None:
        """BFS from entry_node — unreachable nodes produce a warning (SPEC §2.3)."""
        adjacency: dict[str, list[str]] = {n: [] for n in definition.nodes}
        for edge in definition.edges:
            if edge.from_node in adjacency:
                adjacency[edge.from_node].append(edge.to_node)

        visited: set[str] = set()
        queue: deque[str] = deque([definition.entry_node])
        while queue:
            node_id = queue.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            for neighbor in adjacency.get(node_id, []):
                if neighbor not in visited:
                    queue.append(neighbor)

        for node_id in definition.nodes:
            if node_id not in visited:
                warnings.append(CompilationError(
                    severity="warning",
                    message=f"Node '{node_id}' is unreachable from entry_node '{definition.entry_node}'",
                    node_id=node_id,
                ))

    def _check_cycles(
        self, definition: GraphDefinition, errors: list[CompilationError]
    ) -> None:
        """
        Detect cycles where EVERY edge in the cycle is unconditional (SPEC §2.4).
        A cycle with at least one conditional edge is valid — it has a potential exit.

        Algorithm: find all SCCs with >1 node (or self-loops) using DFS.
        For each SCC, check if ALL edges within the SCC are unconditional.
        """
        # Build adjacency with edge metadata
        # edge_map: from_node → list of (to_node, has_condition)
        edge_map: dict[str, list[tuple[str, bool]]] = {n: [] for n in definition.nodes}
        for edge in definition.edges:
            if edge.from_node in edge_map:
                edge_map[edge.from_node].append(
                    (edge.to_node, edge.condition is not None)
                )

        # Tarjan's SCC
        index_counter = [0]
        stack: list[str] = []
        lowlink: dict[str, int] = {}
        index: dict[str, int] = {}
        on_stack: dict[str, bool] = {}
        sccs: list[list[str]] = []

        def strongconnect(v: str) -> None:
            index[v] = index_counter[0]
            lowlink[v] = index_counter[0]
            index_counter[0] += 1
            stack.append(v)
            on_stack[v] = True

            for (w, _) in edge_map.get(v, []):
                if w not in index:
                    strongconnect(w)
                    lowlink[v] = min(lowlink[v], lowlink[w])
                elif on_stack.get(w, False):
                    lowlink[v] = min(lowlink[v], index[w])

            if lowlink[v] == index[v]:
                scc: list[str] = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    scc.append(w)
                    if w == v:
                        break
                sccs.append(scc)

        for node_id in definition.nodes:
            if node_id not in index:
                strongconnect(node_id)

        # Check each SCC with more than one node or self-loops
        for scc in sccs:
            scc_set = set(scc)

            # Detect self-loops (single-node SCC with edge to itself)
            is_cycle = len(scc) > 1
            if len(scc) == 1:
                node = scc[0]
                for (to, _) in edge_map.get(node, []):
                    if to == node:
                        is_cycle = True
                        break

            if not is_cycle:
                continue

            # A cycle is dangerous only if there is NO way out.
            # "A way out" means: at least one edge FROM a node IN the SCC
            # that either (a) goes to a node OUTSIDE the SCC, or
            # (b) is conditional (could be false, allowing another edge to exit).
            has_exit = False
            for node in scc:
                for (to, has_condition) in edge_map.get(node, []):
                    if to not in scc_set:
                        # Edge exits the SCC — there is a way out
                        has_exit = True
                        break
                    if has_condition:
                        # Conditional internal edge — if false, another edge may exit
                        has_exit = True
                        break
                if has_exit:
                    break

            if not has_exit:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"Infinite cycle detected among nodes {sorted(scc_set)}. "
                        "All edges in the cycle are unconditional — the graph would never terminate. "
                        "Add a conditional edge with an exit path."
                    ),
                    node_id=scc[0],
                ))

    def _check_conditions(
        self, definition: GraphDefinition, errors: list[CompilationError]
    ) -> None:
        """Validate condition fields and value types against state_schema (SPEC §4.2, §4.3)."""
        schema_map = {f.name: f for f in definition.state_schema}

        for edge in definition.edges:
            if edge.condition is None:
                continue
            self._validate_condition(edge.condition, schema_map, errors)

        # Validate ConditionNode branches
        for node_id, node_def in definition.nodes.items():
            if node_def.type != NodeType.CONDITION:
                continue
            if not node_def.config.get("branches"):
                continue
            try:
                config = ConditionNodeConfig(**node_def.config)
            except Exception as exc:
                errors.append(CompilationError(
                    severity="error",
                    message=f"ConditionNode '{node_id}' has invalid config: {exc}",
                    node_id=node_id,
                ))
                continue

            for branch in config.branches:
                # Validate target exists
                if branch.target not in definition.nodes:
                    errors.append(CompilationError(
                        severity="error",
                        message=(
                            f"ConditionNode '{node_id}' branch target '{branch.target}' "
                            "does not exist in nodes"
                        ),
                        node_id=node_id,
                    ))
                # Validate conditions
                conditions = (
                    branch.condition.conditions
                    if isinstance(branch.condition, CompoundCondition)
                    else [branch.condition]
                )
                for cond in conditions:
                    self._validate_condition(cond, schema_map, errors)

            # Validate default target
            if config.default is not None and config.default not in definition.nodes:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"ConditionNode '{node_id}' default target '{config.default}' "
                        "does not exist in nodes"
                    ),
                    node_id=node_id,
                ))

    def _validate_condition(
        self,
        condition: EdgeCondition,
        schema_map: dict,
        errors: list[CompilationError],
    ) -> None:
        field = condition.field

        if not field.startswith("state."):
            errors.append(CompilationError(
                severity="error",
                message=f"Condition field '{field}' must start with 'state.'",
            ))
            return

        field_name = field[len("state."):]
        # Only validate against schema if state_schema is defined
        if not schema_map:
            return

        if field_name not in schema_map:
            errors.append(CompilationError(
                severity="error",
                message=(
                    f"Condition references field '{field_name}' "
                    "which is not declared in state_schema"
                ),
            ))
            return

        schema_field = schema_map[field_name]
        # Skip type check for is_null (value irrelevant) and ANY type
        if condition.operator == "is_null" or schema_field.type == StateFieldType.ANY:
            return

        if condition.value is not None and not _is_type_compatible(
            schema_field.type, condition.value
        ):
            errors.append(CompilationError(
                severity="error",
                message=(
                    f"Condition value {condition.value!r} is not compatible "
                    f"with field '{field_name}' of type '{schema_field.type}'"
                ),
            ))

    # ------------------------------------------------------------------
    # Build compiled nodes
    # ------------------------------------------------------------------

    def _build_compiled_nodes(
        self, definition: GraphDefinition
    ) -> dict[str, CompiledNode]:
        # Group edges by from_node, sorted by priority descending
        outgoing: dict[str, list] = {n: [] for n in definition.nodes}
        for edge in definition.edges:
            if edge.from_node in outgoing:
                outgoing[edge.from_node].append(edge)

        for node_id in outgoing:
            outgoing[node_id].sort(key=lambda e: e.priority, reverse=True)

        return {
            node_id: CompiledNode(
                definition=node_def,
                outgoing_edges=outgoing[node_id],
            )
            for node_id, node_def in definition.nodes.items()
        }
