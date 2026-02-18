"""
In-memory WebSocket connection manager.

Production upgrade path: replace the in-memory dicts with a Redis pub/sub
broker so multiple app-server replicas can fan-out messages to each other.
"""

from fastapi import WebSocket
from uuid import UUID
from typing import Dict, Set, Optional
import json
import asyncio
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages active WebSocket connections keyed by user_id.
    A single user may have multiple sockets open (tabs / devices).
    """

    def __init__(self):
        # user_id -> set of active WebSocket connections
        self.active_connections: Dict[UUID, Set[WebSocket]] = {}

    # ── lifecycle ──

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Accept the WS handshake and register."""
        await websocket.accept()
        self.active_connections.setdefault(user_id, set()).add(websocket)
        logger.info("WS connected: user=%s  total_sockets=%d", user_id, len(self.active_connections[user_id]))

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Clean up on close."""
        sockets = self.active_connections.get(user_id, set())
        sockets.discard(websocket)
        if not sockets:
            self.active_connections.pop(user_id, None)
        logger.info("WS disconnected: user=%s", user_id)

    # ── predicates ──

    def is_online(self, user_id: UUID) -> bool:
        return user_id in self.active_connections and len(self.active_connections[user_id]) > 0

    # ── sending ──

    async def send_to_user(self, user_id: UUID, payload: dict) -> None:
        """Fan out a JSON payload to every socket owned by *user_id*."""
        sockets = self.active_connections.get(user_id, set())
        dead: Set[WebSocket] = set()

        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:                   # pragma: no cover
                dead.add(ws)

        # prune broken sockets
        for ws in dead:                         # pragma: no cover
            sockets.discard(ws)
            logger.warning("Pruned dead socket for user=%s", user_id)

    async def broadcast(self, user_ids: list[UUID], payload: dict) -> None:
        """Send the same payload to multiple users."""
        await asyncio.gather(*(self.send_to_user(uid, payload) for uid in user_ids))


# Singleton — imported everywhere that needs to push events
ws_manager = ConnectionManager()