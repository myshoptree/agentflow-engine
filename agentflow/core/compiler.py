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
    GuardrailNodeConfig,
    NodeType,
    ParallelNodeConfig,
    SetStateNodeConfig,
    StartNodeConfig,
    StateFieldType,
    TransformNodeConfig,
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
        self._check_conditions(definition, errors, warnings)
        self._check_new_node_types(definition, errors)
        self._check_agent_inline_schema(definition, errors)

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
        """BFS from entry_node — unreachable nodes produce a warning (SPEC §2.3).

        Implicit edges included in the BFS:
        - on_error targets on any NodeDefinition
        - ConditionNode branch targets and default
        - GuardrailNode on_fail target
        """
        adjacency: dict[str, list[str]] = {n: [] for n in definition.nodes}
        for edge in definition.edges:
            if edge.from_node in adjacency:
                adjacency[edge.from_node].append(edge.to_node)

        # Add implicit reachability from on_error, ConditionNode branches, GuardrailNode on_fail,
        # and ParallelNode branch nodes
        for node_id, node_def in definition.nodes.items():
            if node_def.on_error and node_def.on_error in definition.nodes:
                adjacency[node_id].append(node_def.on_error)
            if node_def.type == NodeType.CONDITION and node_def.config.get("branches"):
                try:
                    cfg = ConditionNodeConfig(**node_def.config)
                    for branch in cfg.branches:
                        if branch.target in definition.nodes:
                            adjacency[node_id].append(branch.target)
                    if cfg.default and cfg.default in definition.nodes:
                        adjacency[node_id].append(cfg.default)
                except Exception:
                    pass
            if node_def.type == NodeType.GUARDRAIL and node_def.config.get("on_fail"):
                on_fail = node_def.config["on_fail"]
                if on_fail in definition.nodes:
                    adjacency[node_id].append(on_fail)
            if node_def.type == NodeType.PARALLEL and node_def.config.get("branches"):
                try:
                    cfg = ParallelNodeConfig(**node_def.config)
                    for branch_node_id in cfg.branches:
                        if branch_node_id in definition.nodes:
                            adjacency[node_id].append(branch_node_id)
                except Exception:
                    pass

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

        for node_id, node_def in definition.nodes.items():
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
        self,
        definition: GraphDefinition,
        errors: list[CompilationError],
        warnings: list[CompilationError] | None = None,
    ) -> None:
        """Validate condition fields and value types against state_schema (SPEC §4.2, §4.3, §4.4)."""
        schema_map = {f.name: f for f in definition.state_schema}
        _warnings = warnings if warnings is not None else []

        for edge in definition.edges:
            # Detect ambiguity: both DSL and CEL defined (SPEC §4.4)
            if edge.condition is not None and edge.condition_cel is not None:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"Edge from '{edge.from_node}' to '{edge.to_node}' defines both "
                        "'condition' (DSL) and 'condition_cel' (CEL) — use only one."
                    ),
                    node_id=edge.from_node,
                ))
                continue

            # Validate CEL expression syntax at compile time (SPEC §4.4)
            if edge.condition_language == "cel" and edge.condition_cel is not None:
                self._validate_cel_syntax(
                    edge.condition_cel, edge.from_node, errors, _warnings
                )
                continue

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

    def _validate_cel_syntax(
        self,
        expr: str,
        from_node: str,
        errors: list[CompilationError],
        warnings: list[CompilationError] | None = None,
    ) -> None:
        """Parse CEL expression at compile time to catch syntax errors (SPEC §4.4)."""
        if warnings is None:
            warnings = errors  # fallback: use errors list for compat (still just a warning)
        try:
            import cel  # type: ignore[import]
            cel.Environment().compile(expr)
        except ImportError:
            # cel-python not installed — warn but don't block compilation (SPEC §4.4)
            warnings.append(CompilationError(
                severity="warning",
                message=(
                    "CEL condition used but 'google-cel-python' is not installed. "
                    "Install it with: uv add google-cel-python"
                ),
                node_id=from_node,
            ))
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"CEL syntax error in edge from '{from_node}': {exc}",
                node_id=from_node,
            ))

    def _check_new_node_types(
        self, definition: GraphDefinition, errors: list[CompilationError]
    ) -> None:
        """Validate configs for SET_STATE, TRANSFORM, START, GUARDRAIL, PARALLEL nodes."""
        schema_map = {f.name: f for f in definition.state_schema}

        for node_id, node_def in definition.nodes.items():
            if node_def.type == NodeType.SET_STATE:
                self._validate_set_state(node_id, node_def.config, schema_map, errors)
            elif node_def.type == NodeType.TRANSFORM:
                self._validate_transform(node_id, node_def.config, schema_map, errors)
            elif node_def.type == NodeType.START:
                self._validate_start(node_id, node_def.config, definition, errors)
            elif node_def.type == NodeType.GUARDRAIL:
                self._validate_guardrail(node_id, node_def.config, definition, schema_map, errors)
            elif node_def.type == NodeType.PARALLEL:
                self._validate_parallel(node_id, node_def.config, definition, errors)

    def _validate_set_state(
        self,
        node_id: str,
        config: dict,
        schema_map: dict,
        errors: list[CompilationError],
    ) -> None:
        try:
            cfg = SetStateNodeConfig(**config)
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"SET_STATE node '{node_id}' has invalid config: {exc}",
                node_id=node_id,
            ))
            return

        if not schema_map:
            return  # no schema to validate against

        for field_name, value in cfg.assignments.items():
            if field_name not in schema_map:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"SET_STATE node '{node_id}' assigns to field '{field_name}' "
                        "which is not declared in state_schema"
                    ),
                    node_id=node_id,
                ))
            # Validate state.ref references
            if isinstance(value, str) and value.startswith("state."):
                ref_field = value[len("state."):]
                if ref_field not in schema_map:
                    errors.append(CompilationError(
                        severity="error",
                        message=(
                            f"SET_STATE node '{node_id}' references '{value}' "
                            f"which is not declared in state_schema"
                        ),
                        node_id=node_id,
                    ))

    def _validate_transform(
        self,
        node_id: str,
        config: dict,
        schema_map: dict,
        errors: list[CompilationError],
    ) -> None:
        try:
            cfg = TransformNodeConfig(**config)
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"TRANSFORM node '{node_id}' has invalid config: {exc}",
                node_id=node_id,
            ))
            return

        if not schema_map:
            return

        for op in cfg.operations:
            if op.set not in schema_map:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"TRANSFORM node '{node_id}' writes to field '{op.set}' "
                        "which is not declared in state_schema"
                    ),
                    node_id=node_id,
                ))
            if op.from_field is not None and op.from_field.startswith("state."):
                ref = op.from_field[len("state."):]
                if ref not in schema_map:
                    errors.append(CompilationError(
                        severity="error",
                        message=(
                            f"TRANSFORM node '{node_id}' references '{op.from_field}' "
                            "which is not declared in state_schema"
                        ),
                        node_id=node_id,
                    ))

    def _validate_start(
        self,
        node_id: str,
        config: dict,
        definition: GraphDefinition,
        errors: list[CompilationError],
    ) -> None:
        try:
            cfg = StartNodeConfig(**config)
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"START node '{node_id}' has invalid config: {exc}",
                node_id=node_id,
            ))
            return

        schema_map = {f.name: f for f in definition.state_schema}
        if not schema_map:
            return

        has_as_text = False
        for inp in cfg.inputs:
            if inp.name not in schema_map:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"START node '{node_id}' declares input '{inp.name}' "
                        "which is not declared in state_schema"
                    ),
                    node_id=node_id,
                ))
            if inp.as_text:
                has_as_text = True

        if has_as_text and "input_as_text" not in schema_map:
            errors.append(CompilationError(
                severity="error",
                message=(
                    f"START node '{node_id}' has as_text=true but 'input_as_text' "
                    "is not declared in state_schema"
                ),
                node_id=node_id,
            ))

    def _validate_guardrail(
        self,
        node_id: str,
        config: dict,
        definition: GraphDefinition,
        schema_map: dict,
        errors: list[CompilationError],
    ) -> None:
        try:
            cfg = GuardrailNodeConfig(**config)
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"GUARDRAIL node '{node_id}' has invalid config: {exc}",
                node_id=node_id,
            ))
            return

        if cfg.on_fail not in definition.nodes:
            errors.append(CompilationError(
                severity="error",
                message=(
                    f"GUARDRAIL node '{node_id}' on_fail '{cfg.on_fail}' "
                    "does not exist in nodes"
                ),
                node_id=node_id,
            ))

        if schema_map:
            for check in cfg.checks:
                field_name = check.field[len("state."):] if check.field.startswith("state.") else check.field
                if field_name not in schema_map:
                    errors.append(CompilationError(
                        severity="error",
                        message=(
                            f"GUARDRAIL node '{node_id}' check references field '{check.field}' "
                            "which is not declared in state_schema"
                        ),
                        node_id=node_id,
                    ))

    def _validate_parallel(
        self,
        node_id: str,
        config: dict,
        definition: GraphDefinition,
        errors: list[CompilationError],
    ) -> None:
        try:
            cfg = ParallelNodeConfig(**config)
        except Exception as exc:
            errors.append(CompilationError(
                severity="error",
                message=f"PARALLEL node '{node_id}' has invalid config: {exc}",
                node_id=node_id,
            ))
            return

        for branch_node_id in cfg.branches:
            if branch_node_id not in definition.nodes:
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"PARALLEL node '{node_id}' references branch node "
                        f"'{branch_node_id}' which does not exist in nodes"
                    ),
                    node_id=node_id,
                ))

    def _check_agent_inline_schema(
        self, definition: GraphDefinition, errors: list[CompilationError]
    ) -> None:
        """Validate that agent nodes don't use both output_schema and output_schema_inline (B4)."""
        for node_id, node_def in definition.nodes.items():
            if node_def.type != NodeType.AGENT:
                continue
            cfg = node_def.config
            if cfg.get("output_schema") and cfg.get("output_schema_inline"):
                errors.append(CompilationError(
                    severity="error",
                    message=(
                        f"Agent node '{node_id}' defines both 'output_schema' and "
                        "'output_schema_inline' — use only one."
                    ),
                    node_id=node_id,
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
