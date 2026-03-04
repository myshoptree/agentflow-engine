"""
Simulated external tools for example graphs.

Register them before running a graph that uses tool nodes:

    from examples.tools import register_example_tools
    register_example_tools()
"""
from agentflow.executors.tool_executor import register_tool


# ---------------------------------------------------------------------------
# Tool: get_user_context (used in 03_tool_plus_agent.yaml)
# Simulates a CRM/database lookup by user_id
# ---------------------------------------------------------------------------

_USER_DB = {
    "u001": {"name": "Ana García",    "plan": "Starter",    "usage_percent": 45.0},
    "u002": {"name": "Carlos López",  "plan": "Pro",        "usage_percent": 78.0},
    "u003": {"name": "María Pérez",   "plan": "Enterprise", "usage_percent": 12.0},
    "u004": {"name": "Luis Torres",   "plan": "Starter",    "usage_percent": 94.0},
}

_DEFAULT_USER = {"name": "Usuario", "plan": "Free", "usage_percent": 0.0}


async def get_user_context(user_id: str) -> dict:
    user = _USER_DB.get(user_id, _DEFAULT_USER)
    return {
        "name": user["name"],
        "plan": user["plan"],
        "usage_percent": user["usage_percent"],
    }


def register_example_tools() -> None:
    register_tool("get_user_context", get_user_context)
