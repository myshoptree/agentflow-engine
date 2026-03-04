from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


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
    output_schema: str | None = None          # fully-qualified class name or None
    state_output_mapping: dict[str, str] = {}  # {state_field: output_field}
    max_retries: int = 3                       # legacy — use retry_policy instead
    tools: list[str] = []
    retry_policy: RetryPolicy | None = None


class ToolNodeConfig(BaseModel):
    tool_name: str
    input_mapping: dict[str, str] = {}    # {tool_param: state_field}
    output_mapping: dict[str, str] = {}   # {state_field: tool_output_field}
    max_retries: int = 3                  # legacy — use retry_policy instead
    on_error: str | None = None           # node_id to route on failure
    retry_policy: RetryPolicy | None = None


class ParallelNodeConfig(BaseModel):
    branches: list[str]    # list of node_ids to execute in parallel
    output_mapping: dict[str, str] = {}


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


NodeConfig = (
    AgentNodeConfig
    | ToolNodeConfig
    | ParallelNodeConfig
    | HumanInputNodeConfig
    | ConditionNodeConfig
    | EndNodeConfig
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
