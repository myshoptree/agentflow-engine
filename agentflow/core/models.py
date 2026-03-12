from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator, field_validator


# ---------------------------------------------------------------------------
# Node types
# ---------------------------------------------------------------------------

class NodeType(str, Enum):
    AGENT = "agent"
    CONDITION = "condition"
    TOOL = "tool"
    PARALLEL = "parallel"
    HUMAN_INPUT = "human_input"
    END = "end"
    SET_STATE = "set_state"
    TRANSFORM = "transform"
    START = "start"
    GUARDRAIL = "guardrail"


# ---------------------------------------------------------------------------
# Edge & Condition
# ---------------------------------------------------------------------------

ConditionOperator = Literal[
    "eq", "neq", "gt", "lt", "gte", "lte", "in", "contains", "is_null"
]


class EdgeCondition(BaseModel):
    field: str          # dot-notation, e.g. "state.intent"
    operator: ConditionOperator
    value: Any = None   # None allowed for is_null


class Edge(BaseModel):
    from_node: str
    to_node: str
    condition: EdgeCondition | None = None  # None = unconditional / fallback
    condition_cel: str | None = None        # CEL expression string (SPEC §4.4)
    condition_language: Literal["dsl", "cel"] = "dsl"
    priority: int = 0


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class StateFieldType(str, Enum):
    STR = "str"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    LIST = "list"
    DICT = "dict"
    ANY = "any"


class StateFieldDefinition(BaseModel):
    name: str
    type: StateFieldType = StateFieldType.ANY
    default: Any = None
    required: bool = False


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------

class RetryPolicy(BaseModel):
    """
    Configurable retry policy per node.
    backoff formula: min(backoff_base * backoff_multiplier^(attempt-1), backoff_max)
    """
    max_retries: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 30.0
    backoff_multiplier: float = 2.0


# ---------------------------------------------------------------------------
# Node configs (per type)
# ---------------------------------------------------------------------------

class AgentNodeConfig(BaseModel):
    model: str = "anthropic:claude-sonnet-4-6"
    system_prompt: str = ""
    output_schema: str | None = None               # fully-qualified class name or None
    output_schema_inline: dict[str, Any] | None = None  # inline schema definition (B4)
    state_output_mapping: dict[str, str] = {}      # {state_field: output_field}
    max_retries: int = 3                           # legacy — use retry_policy instead
    tools: list[str] = []
    retry_policy: RetryPolicy | None = None
    timeout_seconds: float | None = None           # per-node timeout (SPEC §5.4)


class ToolNodeConfig(BaseModel):
    tool_name: str
    input_mapping: dict[str, str] = {}    # {tool_param: state_field}
    output_mapping: dict[str, str] = {}   # {state_field: tool_output_field}
    max_retries: int = 3                  # legacy — use retry_policy instead
    on_error: str | None = None           # node_id to route on failure
    retry_policy: RetryPolicy | None = None
    timeout_seconds: float | None = None  # per-node timeout (SPEC §5.4)


class ParallelNodeConfig(BaseModel):
    branches: list[str]    # list of node_ids to execute in parallel
    output_mapping: dict[str, str] = {}
    timeout_seconds: float | None = None  # per-node timeout (SPEC §5.4)


class HumanInputNodeConfig(BaseModel):
    prompt: str = ""
    input_mapping: dict[str, str] = {}    # {state_field: input_key}
    timeout_seconds: float | None = None


class LogicalOperator(str, Enum):
    AND = "and"
    OR = "or"


class CompoundCondition(BaseModel):
    operator: LogicalOperator = LogicalOperator.AND
    conditions: list[EdgeCondition]


class ConditionBranch(BaseModel):
    condition: CompoundCondition | EdgeCondition
    target: str  # node_id destination


class ConditionNodeConfig(BaseModel):
    branches: list[ConditionBranch] = []
    default: str | None = None


class EndNodeConfig(BaseModel):
    pass


# ---------------------------------------------------------------------------
# SET_STATE node config (A3) — writes values directly to graph_state
# ---------------------------------------------------------------------------

class SetStateNodeConfig(BaseModel):
    assignments: dict[str, Any] = {}  # {state_field: literal_value or "state.ref"}


# ---------------------------------------------------------------------------
# TRANSFORM node config (B1) — reshapes data without LLM
# ---------------------------------------------------------------------------

class TransformOperation(BaseModel):
    set: str                                              # destination state field
    from_field: str | None = None                         # "state.some_field"
    template: str | None = None                           # "{state.first} {state.last}"
    extract: str | None = None                            # dot-path within the value
    cast: Literal["str", "int", "float", "bool"] | None = None


class TransformNodeConfig(BaseModel):
    operations: list[TransformOperation] = []


# ---------------------------------------------------------------------------
# START node config (B2) — explicit entry contract
# ---------------------------------------------------------------------------

class StartInputField(BaseModel):
    name: str
    type: StateFieldType = StateFieldType.STR
    as_text: bool = False  # expose as input_as_text


class StartNodeConfig(BaseModel):
    inputs: list[StartInputField] = []


# ---------------------------------------------------------------------------
# GUARDRAIL node config (C1) — safety checks with pass/fail routing
# ---------------------------------------------------------------------------

class GuardrailCheckType(str, Enum):
    PII = "pii"
    TOXICITY = "toxicity"
    CUSTOM_LLM = "custom_llm"


class GuardrailCheck(BaseModel):
    type: GuardrailCheckType
    field: str                   # state field to evaluate
    prompt: str | None = None    # only for custom_llm
    model: str | None = None     # model for custom_llm (defaults to claude-sonnet-4-6)


class GuardrailNodeConfig(BaseModel):
    checks: list[GuardrailCheck]
    on_fail: str                          # node_id to route to on failure
    output_mapping: dict[str, str] = {}   # {state_field: "result" | "reason"}


NodeConfig = (
    AgentNodeConfig
    | ToolNodeConfig
    | ParallelNodeConfig
    | HumanInputNodeConfig
    | ConditionNodeConfig
    | EndNodeConfig
    | SetStateNodeConfig
    | TransformNodeConfig
    | StartNodeConfig
    | GuardrailNodeConfig
)


# ---------------------------------------------------------------------------
# Node definition
# ---------------------------------------------------------------------------

class NodeDefinition(BaseModel):
    type: NodeType
    config: dict[str, Any] = {}
    on_error: str | None = None    # node_id to route to on unrecoverable error

    def get_typed_config(self) -> NodeConfig:
        mapping: dict[NodeType, type] = {
            NodeType.AGENT: AgentNodeConfig,
            NodeType.TOOL: ToolNodeConfig,
            NodeType.PARALLEL: ParallelNodeConfig,
            NodeType.HUMAN_INPUT: HumanInputNodeConfig,
            NodeType.CONDITION: ConditionNodeConfig,
            NodeType.END: EndNodeConfig,
            NodeType.SET_STATE: SetStateNodeConfig,
            NodeType.TRANSFORM: TransformNodeConfig,
            NodeType.START: StartNodeConfig,
            NodeType.GUARDRAIL: GuardrailNodeConfig,
        }
        cls = mapping[self.type]
        return cls(**self.config)


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------

class GraphDefinition(BaseModel):
    id: str
    version: str = "1.0.0"
    name: str = ""
    entry_node: str
    nodes: dict[str, NodeDefinition]
    edges: list[Edge]
    state_schema: list[StateFieldDefinition] = []
    global_timeout_seconds: float = 300.0
    max_depth: int = 50

    @model_validator(mode="after")
    def entry_node_must_exist(self) -> GraphDefinition:
        if self.entry_node not in self.nodes:
            raise ValueError(f"entry_node '{self.entry_node}' not found in nodes")
        return self


# ---------------------------------------------------------------------------
# Compiled graph
# ---------------------------------------------------------------------------

class CompiledNode(BaseModel):
    definition: NodeDefinition
    # outgoing edges sorted by priority descending (highest first)
    outgoing_edges: list[Edge] = []


class CompilationError(BaseModel):
    severity: Literal["error", "warning"]
    message: str
    node_id: str | None = None


class CompiledGraph(BaseModel):
    definition: GraphDefinition
    nodes: dict[str, CompiledNode]
    compiled_at: datetime = Field(default_factory=datetime.utcnow)
    compilation_warnings: list[CompilationError] = []

    model_config = {"arbitrary_types_allowed": True}


# ---------------------------------------------------------------------------
# Execution state
# ---------------------------------------------------------------------------

class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"


class ExecutionState(BaseModel):
    execution_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    graph_id: str
    graph_version: str
    status: ExecutionStatus = ExecutionStatus.PENDING
    current_node: str | None = None
    graph_state: dict[str, Any] = {}
    depth: int = 0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    error_message: str | None = None
    # Resume support
    suspended_input_key: str | None = None


class StateCheckpoint(BaseModel):
    checkpoint_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str
    node_id: str
    graph_state_snapshot: dict[str, Any]
    depth: int
    created_at: datetime = Field(default_factory=datetime.utcnow)


class NodeExecutionRecord(BaseModel):
    record_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    execution_id: str
    node_id: str
    node_type: NodeType
    attempt: int = 1
    input_state: dict[str, Any] = {}
    output_data: dict[str, Any] | None = None
    error_message: str | None = None
    duration_ms: float = 0.0
    llm_tokens_used: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# ExecutionTrace (B3) — first-class trace object (SPEC §7.4)
# ---------------------------------------------------------------------------

class ExecutionTrace(BaseModel):
    execution_id: str
    graph_id: str
    graph_version: str
    status: ExecutionStatus
    duration_ms: float
    total_tokens: int
    node_records: list[NodeExecutionRecord] = []
    checkpoints: list[StateCheckpoint] = []
    final_state: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Graders (C2) — evaluation of ExecutionTrace (SPEC §12)
# ---------------------------------------------------------------------------

class GraderType(str, Enum):
    DETERMINISTIC = "deterministic"   # compare output vs expected using DSL
    LLM_JUDGE = "llm_judge"           # LLM evaluates the trace
    HEURISTIC = "heuristic"           # rules over trace metrics


class Grader(BaseModel):
    id: str
    name: str
    type: GraderType
    config: dict[str, Any] = {}


class GradeResult(BaseModel):
    grader_id: str
    execution_id: str
    score: float                      # 0.0 = fail, 1.0 = pass
    label: str | None = None          # optional human-readable label
    reason: str | None = None
    graded_at: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Session & conversation history (SPEC §10)
# ---------------------------------------------------------------------------

class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"


class ConversationMessage(BaseModel):
    role: MessageRole
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Session(BaseModel):
    """
    Groups multiple executions under a shared conversation.
    Maintains message history and accumulated graph_state (SPEC §10).
    """
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    graph_id: str
    graph_version: str
    history: list[ConversationMessage] = []
    # Accumulated state from last completed execution (SPEC §10.4)
    accumulated_state: dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
