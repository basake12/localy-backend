from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, status
from sqlalchemy.orm import Session
from typing import List, Optional
from uuid import UUID
from datetime import datetime
import json

from app.core.database import get_db, SessionLocal
from app.dependencies import (
    get_current_active_user,
    require_customer,
    get_pagination_params
)
from app.schemas.common_schema import SuccessResponse
from app.schemas.chat_schema import (
    ConversationCreateRequest,
    ConversationResponse,
    MessageCreateRequest,
    MessageResponse,
    MessageUpdateRequest,
    ReactionRequest,
    PresenceUpdateRequest,
    PresenceResponse,
    TypingStartRequest
)
from app.services.chat_service import chat_service
from app.crud.chat_crud import (
    conversation_crud, message_crud,
    presence_crud, typing_crud
)
from app.core.websocket_manager import ws_manager
from app.models.user_model import User
from app.core.exceptions import NotFoundException, ValidationException
from app.core.security import decode_token   # reuse existing JWT decode util

router = APIRouter()


# ============================================
# HELPER – serialise a Conversation for the response
# ============================================

def _serialize_conversation(convo: "Conversation", current_user_id: UUID, db: Session) -> dict:
    other_id = conversation_crud.other_user_id(convo, current_user_id)

    # Fetch other user's basic info
    from app.models.user import User as UserModel
    other = db.query(UserModel).get(other_id)

    is_user_one = convo.user_one_id == current_user_id

    return {
        "id": str(convo.id),
        "conversation_type": convo.conversation_type,
        "other_user_id": str(other_id),
        "other_user_name": f"{other.first_name} {other.last_name}" if other else None,
        "context_type": convo.context_type,
        "context_id": str(convo.context_id) if convo.context_id else None,
        "last_message_preview": convo.last_message_preview,
        "last_message_at": convo.last_message_at.isoformat() if convo.last_message_at else None,
        "unread_count": convo.unread_count_user_one if is_user_one else convo.unread_count_user_two,
        "is_muted": convo.is_muted_user_one if is_user_one else convo.is_muted_user_two,
        "is_archived": convo.is_archived_user_one if is_user_one else convo.is_archived_user_two,
        "created_at": convo.created_at.isoformat()
    }


def _serialize_message(msg: "Message", db: Session) -> dict:
    from app.models.user import User as UserModel
    sender = db.query(UserModel).get(msg.sender_id)

    return {
        "id": str(msg.id),
        "conversation_id": str(msg.conversation_id),
        "sender_id": str(msg.sender_id),
        "sender_name": f"{sender.first_name} {sender.last_name}" if sender else None,
        "message_type": msg.message_type,
        "content": msg.content if not msg.is_deleted else None,
        "media": msg.media if not msg.is_deleted else None,
        "reply_to_message_id": str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
        "is_read": msg.is_read,
        "is_delivered": msg.is_delivered,
        "is_edited": msg.is_edited,
        "is_deleted": msg.is_deleted,
        "reactions": msg.reactions or [],
        "created_at": msg.created_at.isoformat(),
        "edited_at": msg.edited_at.isoformat() if msg.edited_at else None
    }


# ============================================
# CONVERSATIONS
# ============================================

@router.post("/conversations", response_model=SuccessResponse[dict], status_code=status.HTTP_201_CREATED)
async def start_conversation(
    *, db: Session = Depends(get_db),
    body: ConversationCreateRequest,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """Create or resume a conversation, optionally sending the first message."""
    convo = await chat_service.start_conversation(
        db,
        current_user=current_user,
        other_user_id=body.other_user_id,
        context_type=body.context_type,
        context_id=body.context_id,
        initial_message=body.initial_message
    )

    return {"success": True, "data": _serialize_conversation(convo, current_user.id, db)}


@router.get("/conversations", response_model=SuccessResponse[List[dict]])
def list_conversations(
    *, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
    include_archived: bool = Query(default=False),
    pagination: dict = Depends(get_pagination_params)
) -> dict:
    """All conversations for the authenticated user, latest first."""
    convos = conversation_crud.get_conversations_for_user(
        db,
        user_id=current_user.id,
        include_archived=include_archived,
        skip=pagination["skip"],
        limit=pagination["limit"]
    )

    return {
        "success": True,
        "data": [_serialize_conversation(c, current_user.id, db) for c in convos]
    }


@router.get("/conversations/{conversation_id}", response_model=SuccessResponse[dict])
def get_conversation(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    convo = conversation_crud.get(db, id=conversation_id)
    if not convo:
        raise NotFoundException("Conversation")

    if current_user.id not in (convo.user_one_id, convo.user_two_id):
        raise ValidationException("Not a participant")

    return {"success": True, "data": _serialize_conversation(convo, current_user.id, db)}


@router.post("/conversations/{conversation_id}/mute", response_model=SuccessResponse[dict])
def mute_conversation(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    is_muted = conversation_crud.mute_toggle(db, conversation_id=conversation_id, user_id=current_user.id)
    return {"success": True, "data": {"is_muted": is_muted}}


@router.post("/conversations/{conversation_id}/archive", response_model=SuccessResponse[dict])
def archive_conversation(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    is_archived = conversation_crud.archive_toggle(db, conversation_id=conversation_id, user_id=current_user.id)
    return {"success": True, "data": {"is_archived": is_archived}}


# ============================================
# MESSAGES (REST)
# ============================================

@router.post("/conversations/{conversation_id}/messages", response_model=SuccessResponse[dict], status_code=status.HTTP_201_CREATED)
async def send_message(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    body: MessageCreateRequest,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """Send a message via REST (also pushes over WebSocket)."""
    msg = await chat_service.send_message(
        db,
        current_user=current_user,
        conversation_id=conversation_id,
        message_type=body.message_type,
        content=body.content,
        media=body.media,
        reply_to_message_id=body.reply_to_message_id
    )

    return {"success": True, "data": _serialize_message(msg, db)}


@router.get("/conversations/{conversation_id}/messages", response_model=SuccessResponse[List[dict]])
async def get_messages(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    before_id: Optional[UUID] = Query(None),
    limit: int = Query(default=40, le=100),
    current_user: User = Depends(get_current_active_user)
) -> dict:
    """
    Cursor-based message history (newest first).
    Also marks the conversation as read and pushes a read-receipt.
    """
    convo = conversation_crud.get(db, id=conversation_id)
    if not convo:
        raise NotFoundException("Conversation")
    if current_user.id not in (convo.user_one_id, convo.user_two_id):
        raise ValidationException("Not a participant")

    messages = message_crud.get_messages(db, conversation_id=conversation_id, before_id=before_id, limit=limit)

    # Side-effect: mark read + push receipt
    await chat_service.read_messages(db, current_user=current_user, conversation_id=conversation_id)

    return {
        "success": True,
        "data": [_serialize_message(m, db) for m in messages]
    }


@router.put("/messages/{message_id}", response_model=SuccessResponse[dict])
async def edit_message(
    *, db: Session = Depends(get_db),
    message_id: UUID,
    body: MessageUpdateRequest,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    msg = await chat_service.edit_message(db, current_user=current_user, message_id=message_id, new_content=body.content)
    return {"success": True, "data": _serialize_message(msg, db)}


@router.delete("/messages/{message_id}", response_model=SuccessResponse[dict])
async def delete_message(
    *, db: Session = Depends(get_db),
    message_id: UUID,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    msg = await chat_service.delete_message(db, current_user=current_user, message_id=message_id)
    return {"success": True, "data": {"message_id": str(message_id), "is_deleted": True}}


@router.post("/messages/{message_id}/reactions", response_model=SuccessResponse[dict])
async def react_to_message(
    *, db: Session = Depends(get_db),
    message_id: UUID,
    body: ReactionRequest,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    msg = await chat_service.react_to_message(db, current_user=current_user, message_id=message_id, emoji=body.emoji)
    return {"success": True, "data": {"reactions": msg.reactions}}


# ============================================
# TYPING INDICATORS (REST fallback)
# ============================================

@router.post("/conversations/{conversation_id}/typing/start", status_code=status.HTTP_204_NO_CONTENT)
async def typing_start(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user)
):
    await chat_service.typing_start(db, current_user=current_user, conversation_id=conversation_id)


@router.post("/conversations/{conversation_id}/typing/stop", status_code=status.HTTP_204_NO_CONTENT)
async def typing_stop(
    *, db: Session = Depends(get_db),
    conversation_id: UUID,
    current_user: User = Depends(get_current_active_user)
):
    await chat_service.typing_stop(db, current_user=current_user, conversation_id=conversation_id)


# ============================================
# PRESENCE (REST)
# ============================================

@router.put("/presence", response_model=SuccessResponse[PresenceResponse])
async def update_presence(
    *, db: Session = Depends(get_db),
    body: PresenceUpdateRequest,
    current_user: User = Depends(get_current_active_user)
) -> dict:
    await chat_service.update_presence(
        db,
        user_id=current_user.id,
        is_online=body.status != "offline",
        status=body.status,
        device_type=body.device_type
    )
    presence = presence_crud.get_presence(db, user_id=current_user.id)
    return {"success": True, "data": presence}


@router.get("/presence/{user_id}", response_model=SuccessResponse[PresenceResponse])
def get_user_presence(
    *, db: Session = Depends(get_db),
    user_id: UUID
) -> dict:
    presence = presence_crud.get_presence(db, user_id=user_id)
    if not presence:
        return {"success": True, "data": {"user_id": str(user_id), "is_online": False, "status": "offline", "last_seen_at": None}}
    return {"success": True, "data": presence}


# ============================================
# WEBSOCKET ENDPOINT
# ============================================

@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(...)          # ?token=<jwt>  — can't send headers on WS from browsers
):
    """
    Single long-lived WebSocket per client.

    Client sends JSON frames:
        {"event": "send_message",   "data": {...}}
        {"event": "typing_start",   "data": {"conversation_id": "..."}}
        {"event": "typing_stop",    "data": {"conversation_id": "..."}}
        {"event": "read_messages",  "data": {"conversation_id": "..."}}
        {"event": "update_presence","data": {"status": "away"}}
        {"event": "ping"}                          -> pong

    Server pushes (see WSEventPayload):
        new_message | message_delivered | messages_read |
        message_edited | message_deleted | reaction_update |
        typing_start | typing_stop | presence_update | pong
    """

    # ── authenticate ──
    user = await _ws_authenticate(token)
    if not user:
        await websocket.close(code=1008)   # Policy violation
        return

    # ── connect ──
    await ws_manager.connect(user.id, websocket)

    # Mark online
    db = SessionLocal()
    try:
        await chat_service.update_presence(db, user_id=user.id, is_online=True, status="online")
    finally:
        db.close()

    # ── message loop ──
    try:
        while True:
            raw = await websocket.receive_text()
            frame = json.loads(raw)
            event = frame.get("event", "")
            data = frame.get("data", {})

            db = SessionLocal()
            try:
                if event == "ping":
                    await websocket.send_json({"event": "pong"})

                elif event == "send_message":
                    await chat_service.send_message(
                        db,
                        current_user=user,
                        conversation_id=UUID(data["conversation_id"]),
                        message_type=data.get("message_type", "text"),
                        content=data.get("content"),
                        media=data.get("media"),
                        reply_to_message_id=UUID(data["reply_to_message_id"]) if data.get("reply_to_message_id") else None
                    )

                elif event == "typing_start":
                    await chat_service.typing_start(db, current_user=user, conversation_id=UUID(data["conversation_id"]))

                elif event == "typing_stop":
                    await chat_service.typing_stop(db, current_user=user, conversation_id=UUID(data["conversation_id"]))

                elif event == "read_messages":
                    await chat_service.read_messages(db, current_user=user, conversation_id=UUID(data["conversation_id"]))

                elif event == "update_presence":
                    await chat_service.update_presence(
                        db,
                        user_id=user.id,
                        is_online=data.get("status", "online") != "offline",
                        status=data.get("status", "online"),
                        device_type=data.get("device_type")
                    )

                elif event == "react":
                    await chat_service.react_to_message(
                        db,
                        current_user=user,
                        message_id=UUID(data["message_id"]),
                        emoji=data["emoji"]
                    )

                else:
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": f"Unknown event: {event}"}
                    })

            except (NotFoundException, ValidationException) as exc:
                await websocket.send_json({"event": "error", "data": {"message": str(exc)}})
            except Exception as exc:                              # pragma: no cover
                await websocket.send_json({"event": "error", "data": {"message": "Internal error"}})
            finally:
                db.close()

    except WebSocketDisconnect:
        pass
    finally:
        await ws_manager.disconnect(user.id, websocket)
        # Mark offline
        db = SessionLocal()
        try:
            await chat_service.update_presence(db, user_id=user.id, is_online=False, status="offline")
        finally:
            db.close()


# ── internal helper ──

async def _ws_authenticate(token: str) -> Optional[User]:
    """Decode JWT from query-string and return the User, or None."""
    try:
        payload = decode_token(token)
        user_id = UUID(payload.get("sub"))
        db = SessionLocal()
        try:
            return db.query(User).get(user_id)
        finally:
            db.close()
    except Exception:
        return None