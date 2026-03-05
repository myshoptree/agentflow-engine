"""
Agent Node Executor — integrates with Pydantic AI.

Each AgentNode runs an LLM agent that produces a structured (Pydantic-validated)
output. The output is then mapped into graph_state via state_output_mapping.

The runtime handles retries; this executor performs a single attempt.
"""
from __future__ import annotations

import importlib
from typing import Any

from pydantic import BaseModel, create_model

from agentflow.core.models import AgentNodeConfig, ExecutionState, NodeDefinition


class AgentExecutorError(Exception):
    pass


def _resolve_schema(schema_path: str | None) -> type[BaseModel] | None:
    """
    Resolve a fully-qualified class name like 'myapp.schemas.ClassifierOutput'
    to the actual Pydantic model class.
    """
    if schema_path is None:
        return None
    parts = schema_path.rsplit(".", 1)
    if len(parts) != 2:
        raise AgentExecutorError(
            f"output_schema must be a fully-qualified class name (got '{schema_path}')"
        )
    module_path, class_name = parts
    try:
        module = importlib.import_module(module_path)
    except ModuleNotFoundError as e:
        raise AgentExecutorError(f"Cannot import module '{module_path}': {e}") from e
    cls = getattr(module, class_name, None)
    if cls is None:
        raise AgentExecutorError(
            f"Class '{class_name}' not found in module '{module_path}'"
        )
    return cls


_INLINE_TYPE_MAP: dict[str, type] = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "any": Any,
}


def _build_inline_schema(spec: dict[str, Any], model_name: str = "InlineSchema") -> type[BaseModel]:
    """
    Build a Pydantic model dynamically from an inline schema spec (B4).

    Spec format:
        {
          "field_name": {"type": "str", "enum": ["A", "B"]},
          "other_field": {"type": "float"},
        }

    Enum fields use Literal types when enum values are provided.
    """
    from typing import Literal, get_args

    field_definitions: dict[str, Any] = {}

    for field_name, field_spec in spec.items():
        if not isinstance(field_spec, dict):
            # Simple shorthand: "field": "str"
            py_type = _INLINE_TYPE_MAP.get(str(field_spec), Any)
            field_definitions[field_name] = (py_type, ...)
            continue

        type_name = field_spec.get("type", "any")
        py_type = _INLINE_TYPE_MAP.get(type_name, Any)
        enum_values = field_spec.get("enum")

        if enum_values:
            # Create a Literal type from enum values
            literal_type = Literal[tuple(enum_values)]  # type: ignore[valid-type]
            field_definitions[field_name] = (literal_type, ...)
        else:
            default = field_spec.get("default", ...)
            field_definitions[field_name] = (py_type, default)

    return create_model(model_name, **field_definitions)


class AgentExecutor:
    async def execute(
        self,
        node_id: str,
        node_def: NodeDefinition,
        execution_state: ExecutionState,
    ) -> dict[str, Any]:
        """
        Run the Pydantic AI agent and return state updates.

        Returns a dict of {state_field: value} to be merged into graph_state
        by the runtime (SPEC §3.1 — only declared fields are written).
        """
        from pydantic_ai import Agent  # imported here to keep it optional at module level

        config = AgentNodeConfig(**node_def.config)

        # B4: inline schema takes precedence if output_schema is not set
        if config.output_schema_inline is not None:
            result_type: type[BaseModel] | None = _build_inline_schema(
                config.output_schema_inline, model_name=f"{node_id}_schema"
            )
        else:
            result_type = _resolve_schema(config.output_schema)

        agent: Agent = Agent(
            model=config.model,
            system_prompt=config.system_prompt,
            output_type=result_type if result_type is not None else str,
        )

        # Build the user message from graph_state
        user_input = execution_state.graph_state.get("user_input", "")

        # conversation_history: injected by SessionManager as list of {role, content}
        # Pass to pydantic-ai as proper message history so the LLM has full context
        raw_history: list[dict] = execution_state.graph_state.get("conversation_history", [])

        message = str(user_input)

        if raw_history:
            from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
            message_history = []
            for entry in raw_history:
                role = entry.get("role", "user")
                content = entry.get("content", "")
                if role == "user":
                    message_history.append(
                        ModelRequest(parts=[UserPromptPart(content=content)])
                    )
                else:
                    message_history.append(
                        ModelResponse(parts=[TextPart(content=content)])
                    )
            result = await agent.run(message, message_history=message_history)
        else:
            result = await agent.run(message)

        # Extract state updates using state_output_mapping
        # mapping: {state_field: output_field}
        state_updates: dict[str, Any] = {}

        if result_type is not None:
            output_obj = result.output
            output_dict = output_obj.model_dump() if isinstance(output_obj, BaseModel) else {}
        else:
            # No schema — treat raw string output
            output_dict = {"output": result.output}

        for state_field, output_field in config.state_output_mapping.items():
            if output_field not in output_dict:
                raise AgentExecutorError(
                    f"Agent output missing field '{output_field}' "
                    f"(declared in state_output_mapping for node '{node_id}')"
                )
            state_updates[state_field] = output_dict[output_field]

        # If no mapping declared, store entire output under node_id key
        if not config.state_output_mapping:
            state_updates[node_id] = result.output if result_type is None else output_dict

        return state_updates
