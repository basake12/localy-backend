"""
app/core/websocket_manager.py

In-memory WebSocket connection manager.

Blueprint §10: "FastAPI WebSocket endpoints: /ws/chat/{chat_room_id}"
Blueprint §16.3: presence:{user_id} TTL=30s (Redis heartbeat) — managed separately
                 by chat_crud.CRUDUserPresence. This manager handles the in-process
                 connection registry only.

PRODUCTION NOTE:
  This in-memory dict is single-process only. On a multi-server/multi-worker
  deployment (any production setup), ws_manager.is_online() only knows about
  connections on the LOCAL process. Use Redis pub/sub for cross-process fan-out.

  Upgrade path:
    1. On connect: SADD ws:connections:{user_id} {server_id}:{socket_id} in Redis.
    2. On message send: PUBLISH ws:events:{user_id} payload to Redis channel.
    3. Each server subscribes to ws:events:{its_users} and delivers to local sockets.
    4. On disconnect: SREM ws:connections:{user_id} {server_id}:{socket_id}.

  Until then, presence:{user_id} TTL=30s in Redis (set by update_presence) is the
  cross-instance online check — use presence_crud.is_online_redis() for that.
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
    Blueprint §10: single long-lived WebSocket per client per room.
    """

    def __init__(self):
        # user_id -> set of active WebSocket connections
        self.active_connections: Dict[UUID, Set[WebSocket]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Accept the WS handshake and register the connection."""
        await websocket.accept()
        self.active_connections.setdefault(user_id, set()).add(websocket)
        logger.info(
            "WS connected: user=%s  total_sockets=%d",
            user_id,
            len(self.active_connections[user_id]),
        )

    async def disconnect(self, user_id: UUID, websocket: WebSocket) -> None:
        """Clean up on close or disconnect."""
        sockets = self.active_connections.get(user_id, set())
        sockets.discard(websocket)
        if not sockets:
            self.active_connections.pop(user_id, None)
        logger.info("WS disconnected: user=%s", user_id)

    # ── Predicates ────────────────────────────────────────────────────────────

    def is_online(self, user_id: UUID) -> bool:
        """
        Local-process online check.
        For cross-instance check use presence_crud.is_online_redis() which reads
        presence:{user_id} from Redis (Blueprint §16.3: TTL=30s heartbeat).
        """
        return bool(
            self.active_connections.get(user_id)
        )

    # ── Sending ───────────────────────────────────────────────────────────────

    async def send_to_user(self, user_id: UUID, payload: dict) -> None:
        """Fan out a JSON payload to every socket owned by user_id."""
        sockets = self.active_connections.get(user_id, set())
        if not sockets:
            return

        dead: Set[WebSocket] = set()
        for ws in sockets:
            try:
                await ws.send_json(payload)
            except Exception:
                dead.add(ws)

        # Prune broken sockets
        for ws in dead:
            sockets.discard(ws)
            logger.warning("Pruned dead socket for user=%s", user_id)

    async def send_text_to_user(self, user_id: UUID, text: str) -> None:
        """Fan out a raw text frame (JSON-stringified) to all user sockets."""
        sockets = self.active_connections.get(user_id, set())
        if not sockets:
            return

        dead: Set[WebSocket] = set()
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.add(ws)

        for ws in dead:
            sockets.discard(ws)

    async def broadcast(self, user_ids: list[UUID], payload: dict) -> None:
        """Send the same payload to multiple users concurrently."""
        await asyncio.gather(
            *(self.send_to_user(uid, payload) for uid in user_ids),
            return_exceptions=True,
        )

    # ── Diagnostics ───────────────────────────────────────────────────────────

    def connection_count(self) -> int:
        """Total number of active WebSocket connections across all users."""
        return sum(len(sockets) for sockets in self.active_connections.values())

    def online_user_count(self) -> int:
        """Number of users with at least one active connection."""
        return len(self.active_connections)


# Singleton — imported everywhere that needs to push WS events
ws_manager = ConnectionManager()