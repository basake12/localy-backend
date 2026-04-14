"""
app/services/chat_service.py

Blueprint §9.1 — Business ↔ Customer chat
Blueprint §9.2 — Rider ↔ Customer chat (delivery-scoped, auto-closes 1hr post-delivery)
Blueprint §9.3 — Platform support chat (separate channel, FAQ bot on first open)
"""

from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import and_, or_
from uuid import UUID
from datetime import datetime, timezone

from app.crud.chat_crud import (
    conversation_crud, message_crud,
    presence_crud, typing_crud
)
from app.core.websocket_manager import ws_manager
from app.core.exceptions import NotFoundException, ValidationException
from app.models.chat_model import Conversation, Message, ConversationTypeEnum
from app.models.user_model import User
from app.config import settings


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ─── FAQ bot opening message ───────────────────────────────────────────────────
_SUPPORT_FAQ_MESSAGE = (
    "👋 Hi! Welcome to Localy Support.\n\n"
    "I can help you with:\n"
    "• 🔄 Order & booking issues\n"
    "• 💳 Wallet & payment queries\n"
    "• 🏪 Business account help\n"
    "• 🚴 Delivery problems\n\n"
    "Please describe your issue and a support agent will assist you shortly."
)


class ChatService:

    # ── Conversations ──────────────────────────────────────────────────────────

    @staticmethod
    async def start_conversation(
        db: Session, *,
        current_user: User,
        other_user_id: UUID,
        context_type: Optional[str] = None,
        context_id: Optional[UUID] = None,
        initial_message: Optional[str] = None,
    ) -> Conversation:
        convo, created = conversation_crud.get_or_create(
            db,
            user_one_id=current_user.id,
            user_two_id=other_user_id,
            context_type=context_type,
            context_id=context_id,
        )

        if initial_message and initial_message.strip():
            await ChatService.send_message(
                db,
                current_user=current_user,
                conversation_id=convo.id,
                message_type="text",
                content=initial_message.strip(),
            )
            db.refresh(convo)

        return convo

    @staticmethod
    async def start_support_conversation(
        db: Session, *,
        current_user: User,
        initial_message: Optional[str] = None,
    ) -> Conversation:
        """
        Blueprint §9.3 — Open or resume the support chat channel.
        Creates a conversation of type 'support' between the user and the
        platform support agent (SUPPORT_AGENT_USER_ID from settings).
        On first create, sends an automated FAQ bot greeting as a system message.
        """
        support_agent_id = getattr(settings, "SUPPORT_AGENT_USER_ID", None)
        if not support_agent_id:
            raise ValidationException(
                "Support chat is temporarily unavailable. Please try again later."
            )

        try:
            agent_uuid = UUID(str(support_agent_id))
        except (ValueError, AttributeError):
            raise ValidationException("Support chat configuration error.")

        convo, created = conversation_crud.get_or_create(
            db,
            user_one_id=current_user.id,
            user_two_id=agent_uuid,
            conversation_type=ConversationTypeEnum.SUPPORT,
            context_type="support_ticket",
            context_id=None,
        )

        # Override conversation_type to 'support' regardless of get_or_create default
        if convo.conversation_type != ConversationTypeEnum.SUPPORT:
            convo.conversation_type = ConversationTypeEnum.SUPPORT
            db.commit()
            db.refresh(convo)

        if created:
            # Send automated FAQ greeting as a system message from the agent
            system_msg = message_crud.create_message(
                db,
                conversation_id=convo.id,
                sender_id=agent_uuid,
                message_type="system",
                content=_SUPPORT_FAQ_MESSAGE,
            )
            db.refresh(convo)

            # Push the bot message to the customer over WS
            await ws_manager.send_to_user(current_user.id, {
                "event": "new_message",
                "data": {
                    "message_id":      str(system_msg.id),
                    "conversation_id": str(convo.id),
                    "sender_id":       str(agent_uuid),
                    "message_type":    "system",
                    "content":         _SUPPORT_FAQ_MESSAGE,
                    "created_at":      system_msg.created_at.isoformat(),
                },
            })

        if initial_message and initial_message.strip():
            await ChatService.send_message(
                db,
                current_user=current_user,
                conversation_id=convo.id,
                message_type="text",
                content=initial_message.strip(),
            )
            db.refresh(convo)

        return convo

    @staticmethod
    async def start_rider_conversation(
        db: Session, *,
        current_user: User,
        rider_id: UUID,
        delivery_id: UUID,
    ) -> Conversation:
        """
        Blueprint §9.2 — Rider ↔ Customer chat, scoped to an active delivery.
        Validates that the delivery is active and involves both parties before
        allowing the chat to be created.
        """
        # Import here to avoid circular imports
        from app.models.delivery_model import Delivery

        delivery = db.query(Delivery).filter(
            Delivery.id == delivery_id
        ).first()

        if not delivery:
            raise NotFoundException("Delivery")

        # Validate both parties are participants in this delivery
        current_user_str = str(current_user.id)
        rider_id_str     = str(rider_id)
        delivery_rider   = str(getattr(delivery, "rider_id", ""))
        delivery_customer = str(getattr(delivery, "customer_id", ""))

        is_customer = current_user_str == delivery_customer
        is_rider    = current_user_str == delivery_rider

        if not (is_customer or is_rider):
            raise ValidationException("You are not part of this delivery.")

        # Enforce delivery is active (Blueprint §9.2: active only during delivery)
        delivery_status = getattr(delivery, "status", "").lower()
        active_statuses = {"pending", "accepted", "picked_up", "in_transit", "en_route"}
        if delivery_status not in active_statuses:
            raise ValidationException(
                "Chat is only available during active deliveries."
            )

        convo, created = conversation_crud.get_or_create(
            db,
            user_one_id=current_user.id,
            user_two_id=rider_id,
            conversation_type=ConversationTypeEnum.RIDER,
            context_type="delivery",
            context_id=delivery_id,
        )

        if convo.conversation_type != ConversationTypeEnum.RIDER:
            convo.conversation_type = ConversationTypeEnum.RIDER
            db.commit()
            db.refresh(convo)

        if created:
            # Send a system message to both parties on chat open
            open_msg = message_crud.create_message(
                db,
                conversation_id=convo.id,
                sender_id=current_user.id,
                message_type="system",
                content="Chat opened for this delivery. This chat will close 1 hour after delivery is complete.",
            )
            other_id = rider_id if is_customer else UUID(delivery_customer)
            await ws_manager.broadcast([current_user.id, other_id], {
                "event": "new_message",
                "data": {
                    "message_id":      str(open_msg.id),
                    "conversation_id": str(convo.id),
                    "sender_id":       str(current_user.id),
                    "message_type":    "system",
                    "content":         open_msg.content,
                    "created_at":      open_msg.created_at.isoformat(),
                },
            })

        return convo

    @staticmethod
    def close_rider_chat(db: Session, *, delivery_id: UUID) -> None:
        """
        Blueprint §9.2 — Called by Celery task 1hr after delivery completion.
        Marks the rider conversation for this delivery as inactive.
        Sends a system message to both participants.
        """
        from sqlalchemy import and_

        convo = db.query(Conversation).filter(
            and_(
                Conversation.conversation_type == ConversationTypeEnum.RIDER,
                Conversation.context_type == "delivery",
                Conversation.context_id == delivery_id,
                Conversation.is_active == True,
            )
        ).first()

        if not convo:
            return

        convo.is_active = False

        # Insert a closure system message
        close_msg = Message(
            conversation_id=convo.id,
            sender_id=convo.user_one_id,
            message_type="system",
            content="This delivery chat has been automatically closed.",
        )
        db.add(close_msg)
        db.commit()

    # ── Messages ───────────────────────────────────────────────────────────────

    @staticmethod
    async def send_message(
        db: Session, *,
        current_user: User,
        conversation_id: UUID,
        message_type: str = "text",
        content: Optional[str] = None,
        media: Optional[Dict] = None,
        reply_to_message_id: Optional[UUID] = None,
    ) -> Message:
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        # Blueprint §9.2 — Rider chats are read-only once closed
        if convo.conversation_type == ConversationTypeEnum.RIDER and not convo.is_active:
            raise ValidationException(
                "This delivery chat has been closed and is now read-only."
            )

        msg = message_crud.create_message(
            db,
            conversation_id=conversation_id,
            sender_id=current_user.id,
            message_type=message_type,
            content=content,
            media=media,
            reply_to_message_id=reply_to_message_id,
        )

        other_id = conversation_crud.other_user_id(convo, current_user.id)

        event_payload = {
            "event": "new_message",
            "data": {
                "message_id":          str(msg.id),
                "conversation_id":     str(convo.id),
                "sender_id":           str(current_user.id),
                "message_type":        msg.message_type,
                "content":             msg.content,
                "media":               msg.media,
                "reply_to_message_id": str(msg.reply_to_message_id)
                                       if msg.reply_to_message_id else None,
                "created_at":          msg.created_at.isoformat(),
            },
        }

        await ws_manager.send_to_user(other_id, event_payload)
        await ws_manager.send_to_user(current_user.id, event_payload)

        if ws_manager.is_online(other_id):
            message_crud.mark_delivered(db, message_id=msg.id)
            await ws_manager.send_to_user(current_user.id, {
                "event": "message_delivered",
                "data":  {"message_id": str(msg.id)},
            })

        return msg

    @staticmethod
    async def read_messages(
        db: Session, *,
        current_user: User,
        conversation_id: UUID,
    ) -> None:
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        conversation_crud.mark_read(
            db, conversation_id=conversation_id, user_id=current_user.id
        )
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        await ws_manager.send_to_user(other_id, {
            "event": "messages_read",
            "data": {
                "conversation_id": str(conversation_id),
                "read_by":         str(current_user.id),
                "read_at":         _utcnow().isoformat(),
            },
        })

    @staticmethod
    async def delete_message(
        db: Session, *, current_user: User, message_id: UUID
    ) -> Message:
        msg      = message_crud.soft_delete(db, message_id=message_id, user_id=current_user.id)
        convo    = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "message_deleted",
            "data":  {"message_id": str(message_id), "conversation_id": str(convo.id)},
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    @staticmethod
    async def edit_message(
        db: Session, *, current_user: User, message_id: UUID, new_content: str
    ) -> Message:
        msg      = message_crud.edit_message(
            db, message_id=message_id, user_id=current_user.id, new_content=new_content
        )
        convo    = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "message_edited",
            "data": {
                "message_id":      str(message_id),
                "conversation_id": str(convo.id),
                "content":         new_content,
                "edited_at":       msg.edited_at.isoformat(),
            },
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    @staticmethod
    async def react_to_message(
        db: Session, *, current_user: User, message_id: UUID, emoji: str
    ) -> Message:
        msg      = message_crud.add_reaction(
            db, message_id=message_id, user_id=current_user.id, emoji=emoji
        )
        convo    = conversation_crud.get(db, id=msg.conversation_id)
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "reaction_update",
            "data": {
                "message_id": str(message_id),
                "reactions":  msg.reactions,
            },
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    # ── Typing indicators ──────────────────────────────────────────────────────

    @staticmethod
    async def typing_start(
        db: Session, *, current_user: User, conversation_id: UUID
    ) -> None:
        typing_crud.start_typing(
            db, conversation_id=conversation_id, user_id=current_user.id
        )
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            return
        other_id = conversation_crud.other_user_id(convo, current_user.id)
        await ws_manager.send_to_user(other_id, {
            "event": "typing_start",
            "data": {
                "conversation_id": str(conversation_id),
                "user_id":         str(current_user.id),
            },
        })

    @staticmethod
    async def typing_stop(
        db: Session, *, current_user: User, conversation_id: UUID
    ) -> None:
        typing_crud.stop_typing(
            db, conversation_id=conversation_id, user_id=current_user.id
        )
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            return
        other_id = conversation_crud.other_user_id(convo, current_user.id)
        await ws_manager.send_to_user(other_id, {
            "event": "typing_stop",
            "data": {
                "conversation_id": str(conversation_id),
                "user_id":         str(current_user.id),
            },
        })

    # ── Presence ───────────────────────────────────────────────────────────────

    @staticmethod
    async def update_presence(
        db: Session, *,
        user_id: UUID,
        is_online: bool,
        status: str = "online",
        device_type: Optional[str] = None,
    ) -> None:
        presence = presence_crud.update_presence(
            db, user_id=user_id, is_online=is_online,
            status=status, device_type=device_type,
        )
        convos  = conversation_crud.get_conversations_for_user(db, user_id=user_id)
        targets = {conversation_crud.other_user_id(c, user_id) for c in convos}

        payload = {
            "event": "presence_update",
            "data": {
                "user_id":      str(user_id),
                "is_online":    is_online,
                "status":       status,
                "last_seen_at": presence.last_seen_at.isoformat()
                                if presence.last_seen_at else None,
            },
        }
        if targets:
            await ws_manager.broadcast(list(targets), payload)


chat_service = ChatService()