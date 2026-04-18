"""
app/api/v1/chat.py

Blueprint §15 endpoint paths (this router mounted under /api/v1/chat/):
  GET  /chat/rooms                        → list_rooms()
  POST /chat/rooms                        → start_conversation()
  GET  /chat/rooms/{room_id}              → get_room()
  GET  /chat/rooms/{room_id}/messages     → get_room_messages()
  POST /chat/rooms/{room_id}/messages     → send_message()
  POST /chat/deliveries/{delivery_id}/chat → open_rider_chat()
  POST /chat/support/tickets              → create_support_ticket()
  GET  /chat/support/tickets              → list_support_tickets()
  GET  /chat/support/tickets/{ticket_id}  → get_support_ticket()
  WS   /ws/chat/{room_id}                → websocket_chat()

Support WebSocket at /ws/support/{ticket_id} should be added to a
separate WebSocket router if required (Blueprint §15).

CRITICAL FIXES vs previous version:
  1. VOICE NOTE CHECK: "voice" → "voice_note" (§10.2 HARD RULE).
     Previous check body.message_type == "voice" NEVER matched because
     the correct content_type string from §14 is "voice_note". The HARD RULE
     was completely unenforced.

  2. API PATHS: /conversations → /rooms (Blueprint §15).
     Flutter api_endpoints.dart will call /chat/rooms — previous /conversations
     paths caused permanent 404s on every chat-related screen.

  3. WEBSOCKET PATH: /ws → /ws/chat/{room_id} (Blueprint §15).
     Room-scoped WS. Membership verified before accepting handshake.

  4. SUPPORT: conversation-only approach → ticket-based with status tracking.
     Blueprint §10.3: Open → In Progress → Resolved.
     Blueprint §15: POST /support/tickets, GET /support/tickets/{id}.

  5. REDIS PRESENCE HEARTBEAT on WS ping (§16.3: presence:{user_id} TTL=30s).

  6. content_type field name used throughout (not message_type).

  7. media_url string in all request/response bodies (not media dict).

  8. sender_role included in all message serialisations (§14 NOT NULL).
"""

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
import json
import logging

from app.core.database import get_db, SessionLocal
from app.core.cache import get_redis
from app.dependencies import get_current_active_user, get_pagination_params
from app.schemas.common_schema import SuccessResponse
from app.schemas.chat_schema import (
    ConversationCreateRequest,
    SupportTicketCreateRequest,
    MessageCreateRequest,
    MessageUpdateRequest,
    ReactionRequest,
    PresenceUpdateRequest,
    PresenceResponse,
)
from app.services.chat_service import chat_service
from app.crud.chat_crud import (
    conversation_crud,
    message_crud,
    presence_crud,
    support_ticket_crud,
)
from app.core.websocket_manager import ws_manager
from app.models.user_model import User
from app.models.chat_model import Conversation, Message, ConversationTypeEnum
from app.core.exceptions import NotFoundException, ValidationException
from app.core.security import decode_token

router = APIRouter()
logger = logging.getLogger(__name__)

# Blueprint §16.3: presence:{user_id} TTL=30s — refreshed on WS ping
_PRESENCE_TTL = 30


# ── Serialisers ───────────────────────────────────────────────────────────────

def _serialize_conversation(
    convo: Conversation,
    current_user_id: UUID,
    db: Session,
) -> dict:
    other_id    = conversation_crud.other_user_id(convo, current_user_id)
    other       = db.get(User, other_id)
    is_user_one = convo.user_one_id == current_user_id

    return {
        "id":                   str(convo.id),
        "conversation_type":    convo.conversation_type,
        "other_user_id":        str(other_id),
        "other_user_name":      getattr(other, "full_name", None),
        "other_user_avatar":    getattr(other, "avatar_url", None),
        "context_type":         convo.context_type,
        "context_id":           str(convo.context_id) if convo.context_id else None,
        "last_message_preview": convo.last_message_preview,
        "last_message_at":      convo.last_message_at.isoformat() if convo.last_message_at else None,
        "unread_count":         convo.unread_count_user_one if is_user_one else convo.unread_count_user_two,
        "is_muted":             convo.is_muted_user_one if is_user_one else convo.is_muted_user_two,
        "is_archived":          convo.is_archived_user_one if is_user_one else convo.is_archived_user_two,
        "is_active":            convo.is_active,
        "is_online":            ws_manager.is_online(other_id) if other else False,
        "created_at":           convo.created_at.isoformat(),
    }


def _serialize_message(msg: Message, db: Session) -> dict:
    sender = db.get(User, msg.sender_id)
    return {
        "id":                   str(msg.id),
        "chat_room_id":         str(msg.chat_room_id),          # Blueprint §14 name
        "sender_id":            str(msg.sender_id),
        "sender_role":          msg.sender_role,                 # Blueprint §14 NOT NULL
        "sender_name":          getattr(sender, "full_name", None),
        "content_type":         msg.content_type,                # FIX: was message_type
        "content":              msg.content if not msg.is_deleted else None,
        "media_url":            msg.media_url if not msg.is_deleted else None,  # FIX: was media dict
        "reply_to_message_id":  str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
        "is_read":              msg.is_read,
        "is_delivered":         msg.is_delivered,
        "is_edited":            msg.is_edited,
        "is_deleted":           msg.is_deleted,
        "reactions":            msg.reactions or [],
        "created_at":           msg.created_at.isoformat(),
        "edited_at":            msg.edited_at.isoformat() if msg.edited_at else None,
    }


def _serialize_ticket(ticket) -> dict:
    return {
        "ticket_id":       str(ticket.id),
        "subject":         ticket.subject,
        "status":          ticket.status,
        "sla_deadline_at": ticket.sla_deadline_at.isoformat() if ticket.sla_deadline_at else None,
        "resolved_at":     ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "resolution_note": ticket.resolution_note,
        "created_at":      ticket.created_at.isoformat(),
    }


# ── Chat Rooms (Blueprint §15: GET /chat/rooms) ───────────────────────────────

@router.get(
    "/rooms",
    response_model=SuccessResponse[List[dict]],
    summary="List user's chat rooms (Blueprint §15)",
)
def list_rooms(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    include_archived: bool = Query(default=False),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    """Blueprint §15: GET /chat/rooms — List user's chat rooms."""
    convos = conversation_crud.get_conversations_for_user(
        db,
        user_id=current_user.id,
        include_archived=include_archived,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {
        "success": True,
        "data": [_serialize_conversation(c, current_user.id, db) for c in convos],
    }


@router.post(
    "/rooms",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Start or resume a business ↔ customer conversation",
)
async def start_conversation(
    *,
    db: Session = Depends(get_db),
    body: ConversationCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    convo = await chat_service.start_conversation(
        db,
        current_user=current_user,
        other_user_id=body.other_user_id,
        context_type=body.context_type,
        context_id=body.context_id,
        initial_message=body.initial_message,
    )
    return {"success": True, "data": _serialize_conversation(convo, current_user.id, db)}


@router.get(
    "/rooms/{room_id}",
    response_model=SuccessResponse[dict],
    summary="Get a single chat room",
)
def get_room(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    convo = conversation_crud.get(db, id=room_id)
    if not convo:
        raise NotFoundException("Conversation")
    if current_user.id not in (convo.user_one_id, convo.user_two_id):
        raise ValidationException("Not a participant in this conversation")
    return {"success": True, "data": _serialize_conversation(convo, current_user.id, db)}


# ── Room Actions ──────────────────────────────────────────────────────────────

@router.post("/rooms/{room_id}/mute", response_model=SuccessResponse[dict])
def mute_conversation(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    is_muted = conversation_crud.mute_toggle(
        db, conversation_id=room_id, user_id=current_user.id
    )
    return {"success": True, "data": {"is_muted": is_muted}}


@router.post("/rooms/{room_id}/archive", response_model=SuccessResponse[dict])
def archive_conversation(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    is_archived = conversation_crud.archive_toggle(
        db, conversation_id=room_id, user_id=current_user.id
    )
    return {"success": True, "data": {"is_archived": is_archived}}


# ── Messages (Blueprint §15: GET /chat/rooms/{id}/messages) ──────────────────

@router.get(
    "/rooms/{room_id}/messages",
    response_model=SuccessResponse[List[dict]],
    summary="Message history for a chat room (Blueprint §15)",
)
def get_room_messages(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    before_id: Optional[UUID] = Query(None, description="Cursor for pagination"),
    limit: int = Query(default=40, le=100),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Blueprint §15: GET /chat/rooms/{id}/messages.
    Fetching messages automatically marks them as read.
    """
    convo = conversation_crud.get(db, id=room_id)
    if not convo:
        raise NotFoundException("Conversation")
    if current_user.id not in (convo.user_one_id, convo.user_two_id):
        raise ValidationException("Not a participant in this conversation")

    messages = message_crud.get_messages(
        db, conversation_id=room_id, before_id=before_id, limit=limit
    )
    # Zero unread counter in DB + Redis (Blueprint §16.3)
    conversation_crud.mark_read(
        db, conversation_id=room_id, user_id=current_user.id
    )
    return {"success": True, "data": [_serialize_message(m, db) for m in messages]}


@router.post(
    "/rooms/{room_id}/messages",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Send a message to a chat room",
)
async def send_message(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    body: MessageCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Blueprint §10.2 HARD RULE:
    content_type='voice_note' rejected in rider/delivery conversations.

    CRITICAL FIX: previous check was body.message_type == "voice" — that string
    NEVER matches anything. The correct content_type value from Blueprint §14 is
    "voice_note". Previous code left rider voice notes completely unblocked.
    """
    convo = conversation_crud.get(db, id=room_id)
    if convo and convo.conversation_type == ConversationTypeEnum.RIDER:
        # FIX: was == "voice" — must be "voice_note" per §14 CHECK values
        if body.content_type == "voice_note":
            raise ValidationException(
                "Voice notes are not available in delivery chats. "
                "(Blueprint §10.2 HARD RULE)"
            )

    msg = await chat_service.send_message(
        db,
        current_user=current_user,
        conversation_id=room_id,
        content_type=body.content_type,         # FIX: was message_type
        content=body.content,
        media_url=body.media_url,               # FIX: was media dict
        reply_to_message_id=body.reply_to_message_id,
    )
    return {"success": True, "data": _serialize_message(msg, db)}


# ── Message Operations ────────────────────────────────────────────────────────

@router.put(
    "/messages/{message_id}",
    response_model=SuccessResponse[dict],
)
async def edit_message(
    *,
    db: Session = Depends(get_db),
    message_id: UUID,
    body: MessageUpdateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    msg = await chat_service.edit_message(
        db, current_user=current_user, message_id=message_id, new_content=body.content
    )
    return {"success": True, "data": _serialize_message(msg, db)}


@router.delete(
    "/messages/{message_id}",
    response_model=SuccessResponse[dict],
)
async def delete_message(
    *,
    db: Session = Depends(get_db),
    message_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    await chat_service.delete_message(
        db, current_user=current_user, message_id=message_id
    )
    return {"success": True, "data": {"message_id": str(message_id), "is_deleted": True}}


@router.post(
    "/messages/{message_id}/reactions",
    response_model=SuccessResponse[dict],
)
async def react_to_message(
    *,
    db: Session = Depends(get_db),
    message_id: UUID,
    body: ReactionRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    msg = await chat_service.react_to_message(
        db, current_user=current_user, message_id=message_id, emoji=body.emoji
    )
    return {"success": True, "data": {"reactions": msg.reactions}}


# ── Rider Chat (Blueprint §10.2) ──────────────────────────────────────────────

@router.post(
    "/deliveries/{delivery_id}/chat",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Open delivery-scoped rider ↔ customer chat (Blueprint §10.2)",
)
async def open_rider_chat(
    *,
    db: Session = Depends(get_db),
    delivery_id: UUID,
    rider_id: UUID = Query(..., description="The rider's user UUID"),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Blueprint §10.2: Rider ↔ Customer chat — active only during delivery.
    Blueprint §16.3: delivery_chat:{delivery_id} Redis key set with dynamic TTL.
    Voice notes blocked. Chat auto-closes 1hr after delivery completion (Celery task).
    """
    convo = await chat_service.start_rider_conversation(
        db,
        current_user=current_user,
        rider_id=rider_id,
        delivery_id=delivery_id,
    )
    return {"success": True, "data": _serialize_conversation(convo, current_user.id, db)}


# ── Typing Indicators ─────────────────────────────────────────────────────────

@router.post(
    "/rooms/{room_id}/typing/start",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def typing_start(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    await chat_service.typing_start(
        db, current_user=current_user, conversation_id=room_id
    )


@router.post(
    "/rooms/{room_id}/typing/stop",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def typing_stop(
    *,
    db: Session = Depends(get_db),
    room_id: UUID,
    current_user: User = Depends(get_current_active_user),
):
    await chat_service.typing_stop(
        db, current_user=current_user, conversation_id=room_id
    )


# ── Support Tickets (Blueprint §10.3 + §15) ───────────────────────────────────

@router.post(
    "/support/tickets",
    response_model=SuccessResponse[dict],
    status_code=status.HTTP_201_CREATED,
    summary="Create support ticket (Blueprint §10.3 + §15)",
)
async def create_support_ticket(
    *,
    db: Session = Depends(get_db),
    body: SupportTicketCreateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Blueprint §10.3: creates support ticket + conversation.
    Blueprint §15: POST /support/tickets.
    SLA by plan: Free/Starter=24h, Pro=4h, Enterprise=1h.
    FAQ bot greeting pushed via WS on first open.
    """
    convo, ticket = await chat_service.start_support_conversation(
        db,
        current_user=current_user,
        subject=body.subject,
        initial_message=body.initial_message,
    )
    return {
        "success": True,
        "data": {
            **_serialize_ticket(ticket),
            "conversation": _serialize_conversation(convo, current_user.id, db),
        },
    }


@router.get(
    "/support/tickets",
    response_model=SuccessResponse[List[dict]],
    summary="List user's support tickets",
)
def list_support_tickets(
    *,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    pagination: dict = Depends(get_pagination_params),
) -> dict:
    tickets = support_ticket_crud.get_user_tickets(
        db,
        user_id=current_user.id,
        skip=pagination["skip"],
        limit=pagination["limit"],
    )
    return {"success": True, "data": [_serialize_ticket(t) for t in tickets]}


@router.get(
    "/support/tickets/{ticket_id}",
    response_model=SuccessResponse[dict],
    summary="Get support ticket status (Blueprint §10.3 + §15)",
)
def get_support_ticket(
    *,
    db: Session = Depends(get_db),
    ticket_id: UUID,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """
    Blueprint §15: GET /support/tickets/{id}.
    Blueprint §10.3: ticket status is Open → In Progress → Resolved.
    """
    ticket = support_ticket_crud.get(db, id=ticket_id)
    if not ticket or ticket.user_id != current_user.id:
        raise NotFoundException("SupportTicket")
    return {"success": True, "data": _serialize_ticket(ticket)}


# ── Presence ──────────────────────────────────────────────────────────────────

@router.put("/presence", response_model=SuccessResponse[PresenceResponse])
async def update_presence(
    *,
    db: Session = Depends(get_db),
    body: PresenceUpdateRequest,
    current_user: User = Depends(get_current_active_user),
) -> dict:
    await chat_service.update_presence(
        db,
        user_id=current_user.id,
        is_online=body.status != "offline",
        status=body.status,
        device_type=body.device_type,
    )
    presence = presence_crud.get_presence(db, user_id=current_user.id)
    return {"success": True, "data": presence}


@router.get("/presence/{user_id}", response_model=SuccessResponse[PresenceResponse])
def get_user_presence(
    *,
    db: Session = Depends(get_db),
    user_id: UUID,
    _current_user: User = Depends(get_current_active_user),
) -> dict:
    presence = presence_crud.get_presence(db, user_id=user_id)
    if not presence:
        return {
            "success": True,
            "data": {
                "user_id":      str(user_id),
                "is_online":    False,
                "status":       "offline",
                "last_seen_at": None,
            },
        }
    return {"success": True, "data": presence}


# ── WebSocket: /ws/chat/{room_id} (Blueprint §15) ─────────────────────────────

@router.websocket("/ws/chat/{room_id}")
async def websocket_chat(
    websocket: WebSocket,
    room_id: UUID,
    token: str = Query(..., description="JWT access token"),
):
    """
    Blueprint §15: WS /ws/chat/{room_id}
    Per-room WebSocket connection. Membership verified before accept.
    Blueprint §16.3: presence:{user_id} TTL=30s refreshed on every ping.
    Blueprint §10.2 HARD RULE: voice_note blocked in rider conversations.

    Client → server event frames:
      {"event": "send_message",    "data": {"content_type": "text", "content": "..."}}
      {"event": "typing_start"}
      {"event": "typing_stop"}
      {"event": "read_messages"}
      {"event": "update_presence", "data": {"status": "away"}}
      {"event": "react",           "data": {"message_id": "...", "emoji": "👍"}}
      {"event": "ping"}

    Server → client events:
      new_message | message_delivered | messages_read | message_edited |
      message_deleted | reaction_update | typing_start | typing_stop |
      presence_update | delivery_chat_opened | support_greeting | pong | error
    """
    # ── Authenticate ────────────────────────────────────────────────────────
    user = await _ws_authenticate(token)
    if not user:
        await websocket.accept()
        await websocket.close(code=1008)  # Policy Violation
        return

    # ── Verify room membership before accepting ──────────────────────────────
    db = SessionLocal()
    try:
        convo = conversation_crud.get(db, id=room_id)
        if not convo or user.id not in (convo.user_one_id, convo.user_two_id):
            await websocket.accept()
            await websocket.close(code=1008)
            return
    finally:
        db.close()

    await ws_manager.connect(user.id, websocket)

    # Mark presence online (Blueprint §16.3: sets presence:{user_id} TTL=30s)
    db = SessionLocal()
    try:
        await chat_service.update_presence(
            db, user_id=user.id, is_online=True, status="online"
        )
    finally:
        db.close()

    try:
        while True:
            raw = await websocket.receive_text()

            try:
                frame = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": "Invalid JSON"},
                })
                continue

            event = frame.get("event", "")
            data  = frame.get("data", {})

            db = SessionLocal()
            try:
                if event == "ping":
                    # Blueprint §16.3: refresh presence:{user_id} TTL=30s on heartbeat
                    try:
                        get_redis().expire(f"presence:{user.id}", _PRESENCE_TTL)
                    except Exception:
                        pass
                    await websocket.send_json({"event": "pong"})

                elif event == "send_message":
                    content_type = data.get("content_type", "text")  # FIX: was message_type

                    # Reload conversation for type check
                    fresh_convo = conversation_crud.get(db, id=room_id)

                    # Blueprint §10.2 HARD RULE
                    # FIX: was == "voice" — NEVER matched. Correct value is "voice_note"
                    if (fresh_convo
                            and fresh_convo.conversation_type == ConversationTypeEnum.RIDER
                            and content_type == "voice_note"):
                        await websocket.send_json({
                            "event": "error",
                            "data": {
                                "message": (
                                    "Voice notes are not available in delivery chats. "
                                    "(Blueprint §10.2 HARD RULE)"
                                )
                            },
                        })
                    else:
                        await chat_service.send_message(
                            db,
                            current_user=user,
                            conversation_id=room_id,
                            content_type=content_type,
                            content=data.get("content"),
                            media_url=data.get("media_url"),  # FIX: was data.get("media")
                            reply_to_message_id=(
                                UUID(data["reply_to_message_id"])
                                if data.get("reply_to_message_id")
                                else None
                            ),
                        )

                elif event == "typing_start":
                    await chat_service.typing_start(
                        db, current_user=user, conversation_id=room_id
                    )

                elif event == "typing_stop":
                    await chat_service.typing_stop(
                        db, current_user=user, conversation_id=room_id
                    )

                elif event == "read_messages":
                    await chat_service.read_messages(
                        db, current_user=user, conversation_id=room_id
                    )

                elif event == "update_presence":
                    await chat_service.update_presence(
                        db,
                        user_id=user.id,
                        is_online=data.get("status", "online") != "offline",
                        status=data.get("status", "online"),
                    )

                elif event == "react":
                    await chat_service.react_to_message(
                        db,
                        current_user=user,
                        message_id=UUID(data["message_id"]),
                        emoji=data["emoji"],
                    )

                else:
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": f"Unknown event: {event}"},
                    })

            except (NotFoundException, ValidationException) as exc:
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": str(exc)},
                })
            except (KeyError, ValueError) as exc:
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": f"Bad payload: {exc}"},
                })
            except Exception as exc:
                logger.error("WS handler error user=%s room=%s: %s", user.id, room_id, exc)
                await websocket.send_json({
                    "event": "error",
                    "data": {"message": "Internal error"},
                })
            finally:
                db.close()

    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(user.id, websocket)
        db = SessionLocal()
        try:
            await chat_service.update_presence(
                db, user_id=user.id, is_online=False, status="offline"
            )
        finally:
            db.close()


# ── WS Authentication Helper ──────────────────────────────────────────────────

async def _ws_authenticate(token: str) -> Optional[User]:
    """
    Decode JWT from WebSocket query param and return the User, or None.
    Blueprint §10: "Authentication: JWT token passed as query param on WS handshake."
    """
    try:
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        db = SessionLocal()
        try:
            return db.get(User, user_id)
        finally:
            db.close()
    except Exception:
        return None