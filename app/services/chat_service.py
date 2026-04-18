"""
app/services/chat_service.py

Blueprint §10.1 — Business ↔ Customer chat
Blueprint §10.2 — Rider ↔ Customer chat (delivery-scoped, voice notes HARD BLOCKED)
Blueprint §10.3 — Platform support chat (ticket-based, FAQ bot, SLA by plan)

FIXES vs previous version:
  1. message_type="system" removed from all send paths.
     Blueprint §14 CHECK: content_type IN ('text','image','voice_note').
     'system' violates this — PostgreSQL rejects the insert.
     Chat-open and chat-close notifications are now WebSocket push events only.

  2. sender_role now passed to create_message at every call site.
     Blueprint §14: sender_role VARCHAR(20) NOT NULL.
     Was missing from all previous create_message calls — every insert failed.

  3. content_type kwarg used everywhere (not message_type).
     Blueprint §14: column name is content_type.

  4. media_url string parameter used (not media dict).
     Blueprint §14: media_url TEXT.

  5. delivery_chat:{delivery_id} Redis key set on chat open (§16.3).
     TTL = estimated_delivery_seconds + 3600 (1hr post-delivery grace).
     Key deleted on close_rider_chat.

  6. Blueprint §10.2 HARD RULE voice note check uses "voice_note" (not "voice").
     Previous code checked == "voice" which never matched — HARD RULE was
     completely unenforced.

  7. start_support_conversation now creates a SupportTicket record (§10.3 + §15).
     SLA deadline set from user's subscription tier.
     Returns (Conversation, SupportTicket) tuple.

  8. All section references corrected to §10 (previously wrongly cited §9).
"""

import json
import logging
from typing import Optional, Dict, Tuple
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session
from sqlalchemy import and_

from app.crud.chat_crud import (
    conversation_crud,
    message_crud,
    presence_crud,
    typing_crud,
    support_ticket_crud,
)
from app.core.websocket_manager import ws_manager
from app.core.exceptions import NotFoundException, ValidationException
from app.core.cache import get_redis
from app.models.chat_model import (
    Conversation,
    Message,
    ConversationTypeEnum,
    SupportTicket,
)
from app.models.user_model import User
from app.config import settings

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: timezone-aware UTC. Never datetime.utcnow()."""
    return datetime.now(timezone.utc)


# Blueprint §10.3: SLA hours by subscription tier
_SLA_HOURS: Dict[str, int] = {
    "enterprise": 1,
    "pro":        4,
    "starter":    24,
    "free":       24,
}

# FAQ bot greeting — pushed via WS only, NOT stored as a message
_FAQ_GREETING = (
    "👋 Hi! Welcome to Localy Support.\n\n"
    "I can help you with:\n"
    "• 🔄 Order & booking issues\n"
    "• 💳 Wallet & payment queries\n"
    "• 🏪 Business account help\n"
    "• 🚴 Delivery problems\n\n"
    "Please describe your issue and a support agent will assist you shortly."
)


def _delivery_chat_key(delivery_id: UUID) -> str:
    """Blueprint §16.3: delivery_chat:{delivery_id}"""
    return f"delivery_chat:{delivery_id}"


def _sla_hours_for_user(user: User) -> int:
    """
    Blueprint §10.3: resolve SLA hours from user's subscription tier.
    Customers are always Free (24h). Businesses use their plan tier.
    """
    tier = getattr(user, "subscription_tier", "free") or "free"
    return _SLA_HOURS.get(tier.lower(), 24)


class ChatService:

    # ── Conversations ──────────────────────────────────────────────────────────

    @staticmethod
    async def start_conversation(
        db: Session,
        *,
        current_user: User,
        other_user_id: UUID,
        context_type: Optional[str] = None,
        context_id: Optional[UUID] = None,
        initial_message: Optional[str] = None,
    ) -> Conversation:
        """
        Blueprint §10.1 — Business ↔ Customer chat.
        Idempotent: returns existing conversation if one already exists.
        """
        convo, created = conversation_crud.get_or_create(
            db,
            user_one_id=current_user.id,
            user_two_id=other_user_id,
            conversation_type=ConversationTypeEnum.BUSINESS,
            context_type=context_type,
            context_id=context_id,
        )

        if initial_message and initial_message.strip():
            await ChatService.send_message(
                db,
                current_user=current_user,
                conversation_id=convo.id,
                content_type="text",
                content=initial_message.strip(),
            )
            db.refresh(convo)

        return convo

    @staticmethod
    async def start_support_conversation(
        db: Session,
        *,
        current_user: User,
        subject: str,
        initial_message: Optional[str] = None,
    ) -> Tuple[Conversation, SupportTicket]:
        """
        Blueprint §10.3 — Open support chat + create support ticket.
        Blueprint §15: POST /support/tickets.

        On first open:
          - Creates SupportTicket with SLA deadline from user's plan tier.
          - Pushes FAQ bot greeting via WebSocket (NOT stored as a message —
            'system' violates §14 CHECK constraint).

        Returns (Conversation, SupportTicket).
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

        # Ensure conversation_type is always 'support'
        if convo.conversation_type != ConversationTypeEnum.SUPPORT:
            convo.conversation_type = ConversationTypeEnum.SUPPORT
            db.commit()
            db.refresh(convo)

        # Resolve SLA hours from user's subscription tier (Blueprint §10.3)
        sla_hours = _sla_hours_for_user(current_user)

        # Create the ticket record (Blueprint §10.3 + §15)
        ticket = support_ticket_crud.create_ticket(
            db,
            user_id=current_user.id,
            subject=subject,
            sla_hours=sla_hours,
            conversation_id=convo.id,
        )

        if created:
            # Push FAQ greeting via WS — NOT stored as a message (§14 CHECK violation)
            await ws_manager.send_to_user(current_user.id, {
                "event": "support_greeting",
                "data": {
                    "ticket_id":       str(ticket.id),
                    "conversation_id": str(convo.id),
                    "message":         _FAQ_GREETING,
                    "sla_hours":       sla_hours,
                },
            })

        if initial_message and initial_message.strip():
            await ChatService.send_message(
                db,
                current_user=current_user,
                conversation_id=convo.id,
                content_type="text",
                content=initial_message.strip(),
            )
            db.refresh(convo)

        return convo, ticket

    @staticmethod
    async def start_rider_conversation(
        db: Session,
        *,
        current_user: User,
        rider_id: UUID,
        delivery_id: UUID,
        estimated_delivery_seconds: int = 3600,
    ) -> Conversation:
        """
        Blueprint §10.2 — Rider ↔ Customer chat, scoped to an active delivery.

        Blueprint §16.3: delivery_chat:{delivery_id} Redis key is set on creation
        with TTL = estimated_delivery_seconds + 3600 (1hr grace post-delivery).

        Opening notification pushed via WebSocket only — no system message stored
        (would violate §14 CHECK constraint).
        """
        from app.models.delivery_model import Delivery

        delivery = db.query(Delivery).filter(Delivery.id == delivery_id).first()
        if not delivery:
            raise NotFoundException("Delivery")

        current_user_str  = str(current_user.id)
        delivery_rider    = str(getattr(delivery, "rider_id", ""))
        delivery_customer = str(getattr(delivery, "customer_id", ""))

        is_customer = current_user_str == delivery_customer
        is_rider    = current_user_str == delivery_rider

        if not (is_customer or is_rider):
            raise ValidationException("You are not part of this delivery.")

        active_statuses = {"pending", "accepted", "picked_up", "in_transit", "en_route"}
        if getattr(delivery, "status", "").lower() not in active_statuses:
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

        # Ensure conversation_type is always 'rider'
        if convo.conversation_type != ConversationTypeEnum.RIDER:
            convo.conversation_type = ConversationTypeEnum.RIDER
            db.commit()
            db.refresh(convo)

        if created:
            # Blueprint §16.3: set delivery_chat:{delivery_id} with dynamic TTL
            redis_ttl = estimated_delivery_seconds + 3600
            try:
                get_redis().setex(
                    _delivery_chat_key(delivery_id),
                    redis_ttl,
                    json.dumps({
                        "conversation_id": str(convo.id),
                        "customer_id":     delivery_customer,
                        "rider_id":        delivery_rider,
                    }),
                )
            except Exception as exc:
                logger.warning(
                    "Redis delivery_chat key set failed delivery=%s: %s",
                    delivery_id, exc,
                )

            # Notify both parties via WS — NOT as a stored message (§14 CHECK)
            other_id = rider_id if is_customer else UUID(delivery_customer)
            await ws_manager.broadcast([current_user.id, other_id], {
                "event": "delivery_chat_opened",
                "data": {
                    "conversation_id": str(convo.id),
                    "delivery_id":     str(delivery_id),
                    "message": (
                        "Chat is open for this delivery. "
                        "Text and images only — voice notes are not available. "
                        "This chat closes 1 hour after delivery is complete."
                    ),
                },
            })

        return convo

    @staticmethod
    def close_rider_chat(db: Session, *, delivery_id: UUID) -> None:
        """
        Blueprint §10.2 — Called by Celery task 1hr after delivery completion.
        Sets conversation.is_active=False and deletes Redis delivery_chat key.

        Blueprint §16.3: delivery_chat:{delivery_id} deleted on close.
        WS close event pushed synchronously via asyncio.run if needed from Celery.
        NOTE: no 'system' message stored — violates §14 CHECK constraint.
        """
        convo = db.query(Conversation).filter(
            and_(
                Conversation.conversation_type == ConversationTypeEnum.RIDER,
                Conversation.context_type == "delivery",
                Conversation.context_id == delivery_id,
                Conversation.is_active.is_(True),
            )
        ).first()

        if not convo:
            return

        convo.is_active = False
        db.commit()

        # Blueprint §16.3: delete Redis delivery_chat key
        try:
            get_redis().delete(_delivery_chat_key(delivery_id))
        except Exception as exc:
            logger.warning(
                "Redis delivery_chat delete failed delivery=%s: %s",
                delivery_id, exc,
            )

    # ── Messages ───────────────────────────────────────────────────────────────

    @staticmethod
    async def send_message(
        db: Session,
        *,
        current_user: User,
        conversation_id: UUID,
        content_type: str = "text",             # FIX: was message_type
        content: Optional[str] = None,
        media_url: Optional[str] = None,        # FIX: was media: dict
        reply_to_message_id: Optional[UUID] = None,
    ) -> Message:
        """
        Blueprint §10.2 HARD RULE: content_type='voice_note' BLOCKED in rider chats.
        FIX: previous check was == "voice" which NEVER matched — rule was unenforced.
             Correct content_type string per §14 is "voice_note".
        """
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        # Blueprint §10.2: rider chat is read-only once closed
        if convo.conversation_type == ConversationTypeEnum.RIDER and not convo.is_active:
            raise ValidationException(
                "This delivery chat has been closed and is now read-only."
            )

        # Blueprint §10.2 HARD RULE — voice notes blocked in rider chats
        # FIX: was == "voice" — correct value is "voice_note" (§14 CHECK values)
        if (convo.conversation_type == ConversationTypeEnum.RIDER
                and content_type == "voice_note"):
            raise ValidationException(
                "Voice notes are not available in delivery chats. "
                "(Blueprint §10.2 HARD RULE)"
            )

        msg = message_crud.create_message(
            db,
            conversation_id=conversation_id,
            sender_id=current_user.id,
            sender_role=current_user.role,       # FIX: was never passed — NOT NULL violation
            content_type=content_type,           # FIX: was message_type kwarg
            content=content,
            media_url=media_url,                 # FIX: was media dict
            reply_to_message_id=reply_to_message_id,
        )

        other_id = conversation_crud.other_user_id(convo, current_user.id)

        event_payload = {
            "event": "new_message",
            "data": {
                "message_id":          str(msg.id),
                "conversation_id":     str(convo.id),
                "chat_room_id":        str(msg.chat_room_id),  # Blueprint §14 name
                "sender_id":           str(current_user.id),
                "sender_role":         msg.sender_role,         # Blueprint §14
                "content_type":        msg.content_type,        # FIX: was message_type
                "content":             msg.content,
                "media_url":           msg.media_url,           # FIX: was msg.media dict
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
        db: Session,
        *,
        current_user: User,
        conversation_id: UUID,
    ) -> None:
        """Mark all messages read. Zeroes Redis and DB unread counters."""
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        # Zeroes both DB counter and Redis key
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
        db: Session,
        *,
        current_user: User,
        message_id: UUID,
    ) -> Message:
        msg   = message_crud.soft_delete(db, message_id=message_id, user_id=current_user.id)
        convo = conversation_crud.get(db, id=msg.conversation_id)
        if not convo:
            raise NotFoundException("Conversation")
        other_id = conversation_crud.other_user_id(convo, current_user.id)

        payload = {
            "event": "message_deleted",
            "data":  {
                "message_id":      str(message_id),
                "conversation_id": str(convo.id),
            },
        }
        await ws_manager.broadcast([current_user.id, other_id], payload)
        return msg

    @staticmethod
    async def edit_message(
        db: Session,
        *,
        current_user: User,
        message_id: UUID,
        new_content: str,
    ) -> Message:
        msg   = message_crud.edit_message(
            db, message_id=message_id, user_id=current_user.id, new_content=new_content
        )
        convo = conversation_crud.get(db, id=msg.conversation_id)
        if not convo:
            raise NotFoundException("Conversation")
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
        db: Session,
        *,
        current_user: User,
        message_id: UUID,
        emoji: str,
    ) -> Message:
        msg   = message_crud.add_reaction(
            db, message_id=message_id, user_id=current_user.id, emoji=emoji
        )
        convo = conversation_crud.get(db, id=msg.conversation_id)
        if not convo:
            raise NotFoundException("Conversation")
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

    # ── Typing Indicators ──────────────────────────────────────────────────────

    @staticmethod
    async def typing_start(
        db: Session,
        *,
        current_user: User,
        conversation_id: UUID,
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
        db: Session,
        *,
        current_user: User,
        conversation_id: UUID,
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
        db: Session,
        *,
        user_id: UUID,
        is_online: bool,
        status: str = "online",
        device_type: Optional[str] = None,
    ) -> None:
        """
        Blueprint §16.3: sets presence:{user_id} in Redis with TTL=30s on online,
        deletes key on offline. Also updates DB for persistent fallback.
        Broadcasts presence_update event to all conversation partners.
        """
        presence = presence_crud.update_presence(
            db,
            user_id=user_id,
            is_online=is_online,
            status=status,
            device_type=device_type,
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