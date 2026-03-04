"""
Session Manager — manages conversation sessions.

A session groups multiple executions under a shared session_id,
maintains message history (append-only), and accumulates graph_state
across turns (SPEC §10).
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any

from agentflow.core.models import (
    ConversationMessage,
    MessageRole,
    Session,
)


class SessionManagerError(Exception):
    pass


class InMemorySessionManager:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    async def create_session(
        self,
        graph_id: str,
        graph_version: str,
        session_id: str | None = None,
    ) -> Session:
        session = Session(
            graph_id=graph_id,
            graph_version=graph_version,
        )
        if session_id is not None:
            if session_id in self._sessions:
                raise SessionManagerError(f"Session '{session_id}' already exists")
            session.session_id = session_id

        self._sessions[session.session_id] = copy.deepcopy(session)
        return copy.deepcopy(session)

    async def load_session(self, session_id: str) -> Session:
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionManagerError(f"Session '{session_id}' not found")
        return copy.deepcopy(session)

    async def append_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
    ) -> None:
        """Append a message to history — append-only (SPEC §10.1)."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionManagerError(f"Session '{session_id}' not found")
        session.history.append(ConversationMessage(role=role, content=content))
        session.updated_at = datetime.now(timezone.utc)

    async def update_accumulated_state(
        self,
        session_id: str,
        graph_state: dict[str, Any],
    ) -> None:
        """Merge completed execution's graph_state into session (SPEC §10.4)."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionManagerError(f"Session '{session_id}' not found")
        session.accumulated_state.update(graph_state)
        session.updated_at = datetime.now(timezone.utc)

    async def get_history_for_llm(
        self, session_id: str
    ) -> list[dict[str, str]]:
        """Return history as list of {role, content} dicts for pydantic-ai."""
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionManagerError(f"Session '{session_id}' not found")
        return [
            {"role": m.role.value, "content": m.content}
            for m in session.history
        ]
