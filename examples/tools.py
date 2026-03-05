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
    register_tool("fetch_product_from_api", fetch_product_from_api)
    register_tool("fetch_product_from_cache", fetch_product_from_cache)


# ---------------------------------------------------------------------------
# Tools: fetch_product_from_api / fetch_product_from_cache
# Used in 10_node_timeout.yaml
#
# fetch_product_from_api simulates a slow external API — configure
# SIMULATE_SLOW_API=true (env) to make it sleep and trigger node timeout.
# fetch_product_from_cache is the fast local fallback.
# ---------------------------------------------------------------------------

import asyncio
import os

_PRODUCT_API_DB = {
    "p001": {"name": "Laptop Pro 15",   "price": 1299.99, "stock": 5,  "source": "api"},
    "p002": {"name": "Mouse Inalámbrico", "price": 29.99,  "stock": 42, "source": "api"},
    "p003": {"name": "Monitor 4K 27\"",  "price": 599.00,  "stock": 0,  "source": "api"},
}

_PRODUCT_CACHE_DB = {
    "p001": {"name": "Laptop Pro 15",   "price": 1249.99, "stock": 3,  "source": "cache"},
    "p002": {"name": "Mouse Inalámbrico", "price": 29.99,  "stock": 40, "source": "cache"},
    "p003": {"name": "Monitor 4K 27\"",  "price": 589.00,  "stock": 0,  "source": "cache"},
}

_DEFAULT_PRODUCT = {"name": "Producto desconocido", "price": 0.0, "stock": 0, "source": "cache"}


async def fetch_product_from_api(product_id: str) -> dict:
    """
    Simulates an external inventory API call.
    Set env SIMULATE_SLOW_API=true to trigger the node timeout in the example.
    """
    if os.getenv("SIMULATE_SLOW_API", "").lower() == "true":
        await asyncio.sleep(10)  # intentionally slow — triggers timeout_seconds: 3.0

    product = _PRODUCT_API_DB.get(product_id, {**_DEFAULT_PRODUCT, "source": "api"})
    return {
        "name": product["name"],
        "price": product["price"],
        "stock": product["stock"],
        "source": product["source"],
    }


async def fetch_product_from_cache(product_id: str) -> dict:
    """Fast local cache fallback — used when fetch_product_from_api times out."""
    product = _PRODUCT_CACHE_DB.get(product_id, _DEFAULT_PRODUCT)
    return {
        "name": product["name"],
        "price": product["price"],
        "stock": product["stock"],
        "source": product["source"],
    }
