"""
Orchestrates CRUD + WebSocket fan-out in one place.
Every public method that mutates state is also responsible for
pushing the matching real-time event to the right sockets.
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from uuid import UUID
from datetime import datetime

from app.crud.chat import (
    conversation_crud, message_crud,
    presence_crud, typing_crud
)
from app.core.websocket_manager import ws_manager
from app.core.exceptions import NotFoundException, ValidationException
from app.models.chat import Conversation, Message
from app.models.user import User


class ChatService:

    # ── conversations ──

    @staticmethod
    async def start_conversation(
        db: Session, *,
        current_user: User,
        other_user_id: UUID,
        context_type: Optional[str] = None,
        context_id: Optional[UUID] = None,
        initial_message: Optional[str] = None
    ) -> Conversation:
        """Create (or fetch) a conversation and optionally send the first message."""
        convo, created = conversation_crud.get_or_create(
            db,
            user_one_id=current_user.id,
            user_two_id=other_user_id,
            context_type=context_type,
            context_id=context_id
        )

        if initial_message and initial_message.strip():
            await ChatService.send_message(
                db,
                current_user=current_user,
                conversation_id=convo.id,
                message_type="text",
                content=initial_message.strip()
            )
            # Refresh so caller sees the updated preview
            db.refresh(convo)

        return convo

    # ── messages ──

    @staticmethod
    async def send_message(
        db: Session, *,
        current_user: User,
        conversation_id: UUID,
        message_type: str = "text",
        content: Optional[str] = None,
        media: Optional[Dict] = None,
        reply_to_message_id: Optional[UUID] = None
    ) -> Message:
        # Persist
        msg = message_crud.create_message(
            db,
            conversation_id=conversation_id,
            sender_id=current_user.id,
            message_type=message_type,
            content=content,
            media=media,
            reply_to_message_id=reply_to_message_id
        )

        convo = conversation_crud.get(db, id=conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        # ── Real-time push ──
        event_payload = {
            "event": "new_message",
            "data": {
                "message_id": str(msg.id),
                "conversation_id": str(convo.id),
                "sender_id": str(current_user.id),
                "message_type": msg.message_type,
                "content": msg.content,
                "media": msg.media,
                "reply_to_message_id": str(msg.reply_to_message_id) if msg.reply_to_message_id else None,
                "created_at": msg.created_at.isoformat()
            }
        }

        # Push to the *other* user
        await ws_manager.send_to_user(other_id, event_payload)

        # Also echo back to sender (so other tabs / devices see it)
        await ws_manager.send_to_user(current_user.id, event_payload)

        # If other user is online, mark as delivered immediately
        if ws_manager.is_online(other_id):
            message_crud.mark_delivered(db, message_id=msg.id)
            await ws_manager.send_to_user(current_user.id, {
                "event": "message_delivered",
                "data": {"message_id": str(msg.id)}
            })

        return msg

    @staticmethod
    async def read_messages(
        db: Session, *,
        current_user: User,
        conversation_id: UUID
    ) -> None:
        """Mark conversation as read + push read-receipt to the other user."""
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        conversation_crud.mark_read(db, conversation_id=conversation_id, user_id=current_user.id)

        other_id = conversation_crud.other_user_id(convo, current_user.id)

        await ws_manager.send_to_user(other_id, {
            "event": "messages_read",
            "data": {
                "conversation_id": str(conversation_id),
                "read_by": str(current_user.id),
                "read_at": datetime.utcnow().isoformat()
            }
        })

    @staticmethod
    async def delete_message(db: Session, *, current_user: User, message_id: UUID) -> Message:
        msg = message_crud.soft_delete(db, message_id=message_id, user_id=current_user.id)

        # Push deletion event to both participants
        convo = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "message_deleted",
            "data": {"message_id": str(message_id), "conversation_id": str(convo.id)}
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)

        return msg

    @staticmethod
    async def edit_message(db: Session, *, current_user: User, message_id: UUID, new_content: str) -> Message:
        msg = message_crud.edit_message(db, message_id=message_id, user_id=current_user.id, new_content=new_content)

        convo = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "message_edited",
            "data": {
                "message_id": str(message_id),
                "conversation_id": str(convo.id),
                "content": new_content,
                "edited_at": msg.edited_at.isoformat()
            }
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    @staticmethod
    async def react_to_message(db: Session, *, current_user: User, message_id: UUID, emoji: str) -> Message:
        msg = message_crud.add_reaction(db, message_id=message_id, user_id=current_user.id, emoji=emoji)

        convo = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "reaction_update",
            "data": {
                "message_id": str(message_id),
                "reactions": msg.reactions
            }
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    # ── typing indicators ──

    @staticmethod
    async def typing_start(db: Session, *, current_user: User, conversation_id: UUID) -> None:
        typing_crud.start_typing(db, conversation_id=conversation_id, user_id=current_user.id)

        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            return
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        await ws_manager.send_to_user(other_id, {
            "event": "typing_start",
            "data": {"conversation_id": str(conversation_id), "user_id": str(current_user.id)}
        })

    @staticmethod
    async def typing_stop(db: Session, *, current_user: User, conversation_id: UUID) -> None:
        typing_crud.stop_typing(db, conversation_id=conversation_id, user_id=current_user.id)

        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            return
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        await ws_manager.send_to_user(other_id, {
            "event": "typing_stop",
            "data": {"conversation_id": str(conversation_id), "user_id": str(current_user.id)}
        })

    # ── presence ──

    @staticmethod
    async def update_presence(
        db: Session, *,
        user_id: UUID,
        is_online: bool,
        status: str = "online",
        device_type: Optional[str] = None
    ) -> None:
        presence = presence_crud.update_presence(
            db, user_id=user_id, is_online=is_online,
            status=status, device_type=device_type
        )

        # Broadcast presence change to every conversation the user is in
        convos = conversation_crud.get_conversations_for_user(db, user_id=user_id)
        targets = set()
        for c in convos:
            targets.add(conversation_crud.other_user_id(c, user_id))

        payload = {
            "event": "presence_update",
            "data": {
                "user_id": str(user_id),
                "is_online": is_online,
                "status": status,
                "last_seen_at": presence.last_seen_at.isoformat() if presence.last_seen_at else None
            }
        }
        if targets:
            await ws_manager.broadcast(list(targets), payload)


chat_service = ChatService()