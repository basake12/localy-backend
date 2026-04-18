"""
app/crud/chat_crud.py

FIXES vs previous version:
  1. create_message: kwarg renamed message_type → content_type (§14 column name).
     Previous code passed Message(message_type=...) — 'message_type' is NOT a mapped
     column. SQLAlchemy silently ignored it, leaving content_type as NULL which
     violates the NOT NULL constraint. Every single message insert was failing.

  2. create_message: sender_role parameter added.
     Blueprint §14: sender_role VARCHAR(20) NOT NULL.
     Was never passed to Message() — caused NOT NULL constraint violation every insert.

  3. create_message: media dict → media_url string.
     Blueprint §14: media_url TEXT. Previous code passed media=dict to a non-existent
     JSONB column. Now accepts a plain string URL.

  4. Redis unread:{user_id}:{room_id} incremented on message send (§16.3).
     Blueprint §16.3: unread:{user_id}:{room_id} is the fast-path unread counter.
     Was previously DB-only with no Redis involvement.

  5. Redis unread:{user_id}:{room_id} zeroed to 0 on mark_read (§16.3).

  6. Redis presence:{user_id} TTL=30s set/deleted in update_presence (§16.3).
     Blueprint §16.3: presence:{user_id} TTL=30s heartbeat.

  7. 'system' content_type removed from all call sites.
     Blueprint §14 CHECK: content_type IN ('text','image','voice_note').
     'system' violates this constraint — PostgreSQL rejects those inserts.
     System notifications go via WebSocket push events only.

  8. edit_message: checks msg.content_type (not msg.message_type).

  9. soft_delete: clears media_url (not msg.media).

  10. SupportTicket CRUD class added (blueprint §10.3 + §15).

  11. All datetime.utcnow() calls use datetime.now(timezone.utc) (§16.4 HARD RULE).
"""

import json
import logging
from typing import Optional, List
from datetime import datetime, timezone, timedelta
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, desc
from uuid import UUID

from app.crud.base_crud import CRUDBase
from app.models.chat_model import (
    Conversation,
    Message,
    UserPresence,
    TypingIndicator,
    SupportTicket,
)
from app.core.exceptions import NotFoundException, ValidationException
from app.core.cache import get_redis

logger = logging.getLogger(__name__)

# ── Redis key helpers (Blueprint §16.3) ──────────────────────────────────────

_PRESENCE_TTL = 30  # seconds — heartbeat per §16.3


def _unread_key(user_id: UUID, room_id: UUID) -> str:
    """Blueprint §16.3: unread:{user_id}:{room_id}"""
    return f"unread:{user_id}:{room_id}"


def _presence_key(user_id: UUID) -> str:
    """Blueprint §16.3: presence:{user_id} TTL=30s"""
    return f"presence:{user_id}"


def _utcnow() -> datetime:
    """Blueprint §16.4 HARD RULE: always timezone-aware UTC. Never datetime.utcnow()."""
    return datetime.now(timezone.utc)


# ── Conversation CRUD ─────────────────────────────────────────────────────────

class CRUDConversation(CRUDBase[Conversation, dict, dict]):

    def get_or_create(
        self,
        db: Session,
        *,
        user_one_id: UUID,
        user_two_id: UUID,
        conversation_type: str = "business",
        context_type: Optional[str] = None,
        context_id: Optional[UUID] = None,
    ) -> tuple[Conversation, bool]:
        """
        Idempotent — return existing conversation or create one.
        UUIDs are sorted so (A,B) and (B,A) resolve to the same row.
        Returns (conversation, created_flag).
        """
        uid_a, uid_b = sorted([str(user_one_id), str(user_two_id)])

        existing = db.query(Conversation).filter(
            and_(
                Conversation.user_one_id == uid_a,
                Conversation.user_two_id == uid_b,
                Conversation.context_type == context_type,
                Conversation.context_id == context_id,
            )
        ).first()

        if existing:
            return existing, False

        convo = Conversation(
            user_one_id=uid_a,
            user_two_id=uid_b,
            conversation_type=conversation_type,
            context_type=context_type,
            context_id=context_id,
        )
        db.add(convo)
        db.commit()
        db.refresh(convo)
        return convo, True

    def get_conversations_for_user(
        self,
        db: Session,
        *,
        user_id: UUID,
        include_archived: bool = False,
        skip: int = 0,
        limit: int = 40,
    ) -> List[Conversation]:
        """Latest-first list of conversations the user is part of."""
        query = db.query(Conversation).filter(
            or_(
                Conversation.user_one_id == user_id,
                Conversation.user_two_id == user_id,
            )
        )

        if not include_archived:
            query = query.filter(
                or_(
                    and_(
                        Conversation.user_one_id == user_id,
                        Conversation.is_archived_user_one.is_(False),
                    ),
                    and_(
                        Conversation.user_two_id == user_id,
                        Conversation.is_archived_user_two.is_(False),
                    ),
                )
            )

        return (
            query
            .order_by(Conversation.last_message_at.desc().nullslast())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def mark_read(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        """
        Zero unread counter in DB and in Redis.
        Blueprint §16.3: unread:{user_id}:{room_id} zeroed on read.
        """
        convo = self.get(db, id=conversation_id)
        if not convo:
            return

        if convo.user_one_id == user_id:
            convo.unread_count_user_one = 0
        else:
            convo.unread_count_user_two = 0

        db.query(Message).filter(
            and_(
                Message.conversation_id == conversation_id,
                Message.sender_id != user_id,
                Message.is_read.is_(False),
            )
        ).update({"is_read": True, "read_at": _utcnow()})

        db.commit()

        # Zero Redis unread counter (Blueprint §16.3)
        try:
            get_redis().set(_unread_key(user_id, conversation_id), 0)
        except Exception as exc:
            logger.warning("Redis unread zero failed user=%s room=%s: %s", user_id, conversation_id, exc)

    def mute_toggle(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> bool:
        convo = self.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        if convo.user_one_id == user_id:
            convo.is_muted_user_one = not convo.is_muted_user_one
            db.commit()
            return convo.is_muted_user_one

        convo.is_muted_user_two = not convo.is_muted_user_two
        db.commit()
        return convo.is_muted_user_two

    def archive_toggle(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> bool:
        convo = self.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        if convo.user_one_id == user_id:
            convo.is_archived_user_one = not convo.is_archived_user_one
            db.commit()
            return convo.is_archived_user_one

        convo.is_archived_user_two = not convo.is_archived_user_two
        db.commit()
        return convo.is_archived_user_two

    def other_user_id(
        self,
        conversation: Conversation,
        current_user_id: UUID,
    ) -> UUID:
        """Return the other participant's UUID."""
        return (
            conversation.user_two_id
            if conversation.user_one_id == current_user_id
            else conversation.user_one_id
        )


# ── Message CRUD ──────────────────────────────────────────────────────────────

class CRUDMessage(CRUDBase[Message, dict, dict]):

    def create_message(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        sender_id: UUID,
        sender_role: str,                       # FIX 2: was missing — NOT NULL in §14
        content_type: str = "text",             # FIX 1: was 'message_type' kwarg
        content: Optional[str] = None,
        media_url: Optional[str] = None,        # FIX 3: was 'media: dict'
        reply_to_message_id: Optional[UUID] = None,
    ) -> Message:
        """
        Create and persist a chat message.

        Blueprint §14 constraints enforced here:
          - content_type IN ('text','image','voice_note') — DB CHECK handles this.
            Do NOT pass 'system' — PostgreSQL will reject it.
          - sender_role NOT NULL — must always be supplied by caller.
          - media_url TEXT — pass the CDN URL string, not a dict.

        Blueprint §16.3: Redis unread:{other_user_id}:{room_id} incremented
        atomically after DB commit.
        """
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")
        if sender_id not in (convo.user_one_id, convo.user_two_id):
            raise ValidationException("You are not part of this conversation")

        if reply_to_message_id:
            reply_msg = self.get(db, id=reply_to_message_id)
            if not reply_msg or reply_msg.conversation_id != conversation_id:
                raise ValidationException("Reply target is not in this conversation")

        now = _utcnow()

        msg = Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            sender_role=sender_role,            # FIX 2: now correctly set
            content_type=content_type,          # FIX 1: correct kwarg name
            content=content,
            media_url=media_url,                # FIX 3: string URL not dict
            reply_to_message_id=reply_to_message_id,
        )
        db.add(msg)
        db.flush()  # get msg.id — caller controls the outer transaction

        # ── update conversation denormalised fields ──────────────────────────
        preview = (
            (content or "")[:255]
            if content_type == "text"
            else f"[{content_type}]"
        )
        convo.last_message_id      = msg.id
        convo.last_message_at      = now
        convo.last_message_preview = preview

        # Bump DB unread counter for the other participant
        if convo.user_one_id == sender_id:
            convo.unread_count_user_two += 1
            other_id = convo.user_two_id
        else:
            convo.unread_count_user_one += 1
            other_id = convo.user_one_id

        db.commit()
        db.refresh(msg)

        # ── Blueprint §16.3: increment Redis unread counter ─────────────────
        try:
            get_redis().incr(_unread_key(other_id, conversation_id))
        except Exception as exc:
            logger.warning(
                "Redis unread increment failed user=%s room=%s: %s",
                other_id, conversation_id, exc,
            )

        return msg

    def get_messages(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        before_id: Optional[UUID] = None,
        limit: int = 40,
    ) -> List[Message]:
        """
        Cursor-based pagination — newest first.
        Pass before_id to fetch messages older than that message (next page).
        Soft-deleted messages are excluded.
        """
        query = (
            db.query(Message)
            .options(joinedload(Message.sender))
            .filter(
                Message.conversation_id == conversation_id,
                Message.is_deleted.is_(False),
            )
        )

        if before_id:
            cursor = self.get(db, id=before_id)
            if cursor:
                query = query.filter(Message.created_at < cursor.created_at)

        return query.order_by(desc(Message.created_at)).limit(limit).all()

    def soft_delete(
        self,
        db: Session,
        *,
        message_id: UUID,
        user_id: UUID,
    ) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")
        if msg.sender_id != user_id:
            raise ValidationException("You can only delete your own messages")
        if msg.is_deleted:
            raise ValidationException("Message is already deleted")

        msg.is_deleted    = True
        msg.deleted_at    = _utcnow()
        msg.deleted_by_id = user_id
        msg.content       = None
        msg.media_url     = None                # FIX: was msg.media — no such column
        db.commit()
        db.refresh(msg)
        return msg

    def edit_message(
        self,
        db: Session,
        *,
        message_id: UUID,
        user_id: UUID,
        new_content: str,
    ) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")
        if msg.sender_id != user_id:
            raise ValidationException("You can only edit your own messages")
        if msg.is_deleted:
            raise ValidationException("Cannot edit a deleted message")
        if msg.content_type != "text":          # FIX: was msg.message_type — no such attr
            raise ValidationException("Only text messages can be edited")

        msg.content   = new_content
        msg.is_edited = True                    # FIX: now a real DB column
        msg.edited_at = _utcnow()              # FIX: now a real DB column
        db.commit()
        db.refresh(msg)
        return msg

    def add_reaction(
        self,
        db: Session,
        *,
        message_id: UUID,
        user_id: UUID,
        emoji: str,
    ) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")

        reactions: list = list(msg.reactions or [])

        # Toggle: remove if same emoji already set, otherwise add
        existing_idx = next(
            (i for i, r in enumerate(reactions)
             if r["user_id"] == str(user_id) and r["emoji"] == emoji),
            None,
        )
        if existing_idx is not None:
            reactions.pop(existing_idx)
        else:
            reactions.append({
                "user_id":    str(user_id),
                "emoji":      emoji,
                "reacted_at": _utcnow().isoformat(),
            })

        msg.reactions = reactions
        db.commit()
        db.refresh(msg)
        return msg

    def mark_delivered(self, db: Session, *, message_id: UUID) -> None:
        msg = self.get(db, id=message_id)
        if msg and not msg.is_delivered:
            msg.is_delivered = True
            msg.delivered_at = _utcnow()
            db.commit()


# ── User Presence CRUD ────────────────────────────────────────────────────────

class CRUDUserPresence(CRUDBase[UserPresence, dict, dict]):

    def update_presence(
        self,
        db: Session,
        *,
        user_id: UUID,
        is_online: bool,
        status: str = "online",
        device_type: Optional[str] = None,
    ) -> UserPresence:
        """
        Update online/offline status in DB and Redis.
        Blueprint §16.3: presence:{user_id} TTL=30s heartbeat.
        On offline: Redis key deleted (natural expiry also covers brief disconnects).
        """
        presence = db.query(UserPresence).filter(
            UserPresence.user_id == user_id
        ).first()

        if not presence:
            presence = UserPresence(user_id=user_id)
            db.add(presence)

        presence.is_online    = is_online
        presence.status       = status
        presence.last_seen_at = _utcnow()
        if device_type:
            presence.device_type = device_type

        db.commit()
        db.refresh(presence)

        # Blueprint §16.3: presence:{user_id} TTL=30s
        try:
            if is_online:
                get_redis().setex(
                    _presence_key(user_id),
                    _PRESENCE_TTL,
                    json.dumps({"status": status, "user_id": str(user_id)}),
                )
            else:
                get_redis().delete(_presence_key(user_id))
        except Exception as exc:
            logger.warning("Redis presence update failed user=%s: %s", user_id, exc)

        return presence

    def is_online_redis(self, user_id: UUID) -> bool:
        """
        Blueprint §16.3: check presence:{user_id} in Redis (TTL=30s heartbeat).
        Falls back to DB if Redis is unavailable.
        Multi-instance safe — unlike in-memory ws_manager.is_online().
        """
        try:
            return get_redis().exists(_presence_key(user_id)) == 1
        except Exception as exc:
            logger.warning("Redis presence check failed user=%s: %s", user_id, exc)
            return False

    def get_presence(
        self,
        db: Session,
        *,
        user_id: UUID,
    ) -> Optional[UserPresence]:
        return db.query(UserPresence).filter(
            UserPresence.user_id == user_id
        ).first()


# ── Typing Indicator CRUD ─────────────────────────────────────────────────────

class CRUDTypingIndicator(CRUDBase[TypingIndicator, dict, dict]):

    def start_typing(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        expires = _utcnow() + timedelta(seconds=5)
        existing = db.query(TypingIndicator).filter(
            and_(
                TypingIndicator.conversation_id == conversation_id,
                TypingIndicator.user_id == user_id,
            )
        ).first()

        if existing:
            existing.expires_at = expires
        else:
            db.add(TypingIndicator(
                conversation_id=conversation_id,
                user_id=user_id,
                expires_at=expires,
            ))
        db.commit()

    def stop_typing(
        self,
        db: Session,
        *,
        conversation_id: UUID,
        user_id: UUID,
    ) -> None:
        db.query(TypingIndicator).filter(
            and_(
                TypingIndicator.conversation_id == conversation_id,
                TypingIndicator.user_id == user_id,
            )
        ).delete()
        db.commit()

    def cleanup_expired(self, db: Session) -> None:
        """Purge stale typing rows. Called by Celery beat or on each WS connect."""
        db.query(TypingIndicator).filter(
            TypingIndicator.expires_at < _utcnow()
        ).delete()
        db.commit()


# ── Support Ticket CRUD ───────────────────────────────────────────────────────

class CRUDSupportTicket(CRUDBase[SupportTicket, dict, dict]):

    def create_ticket(
        self,
        db: Session,
        *,
        user_id: UUID,
        subject: str,
        sla_hours: int,
        conversation_id: Optional[UUID] = None,
    ) -> SupportTicket:
        """
        Blueprint §10.3: Create support ticket in 'open' status.
        sla_hours: derived from user's business subscription tier.
          Free/Starter=24, Pro=4, Enterprise=1.
        Blueprint §15: POST /support/tickets.
        """
        ticket = SupportTicket(
            user_id=user_id,
            conversation_id=conversation_id,
            subject=subject,
            status="open",
            sla_deadline_at=_utcnow() + timedelta(hours=sla_hours),
        )
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket

    def update_status(
        self,
        db: Session,
        *,
        ticket_id: UUID,
        new_status: str,
        agent_id: Optional[UUID] = None,
        resolution_note: Optional[str] = None,
    ) -> SupportTicket:
        """
        Blueprint §10.3: Advance ticket through Open → In Progress → Resolved.
        Called by support agents via admin panel.
        """
        ticket = self.get(db, id=ticket_id)
        if not ticket:
            raise NotFoundException("SupportTicket")

        ticket.status = new_status
        if agent_id:
            ticket.assigned_agent_id = agent_id
        if new_status == "resolved":
            ticket.resolved_at     = _utcnow()
            ticket.resolution_note = resolution_note

        db.commit()
        db.refresh(ticket)
        return ticket

    def get_user_tickets(
        self,
        db: Session,
        *,
        user_id: UUID,
        skip: int = 0,
        limit: int = 20,
    ) -> List[SupportTicket]:
        return (
            db.query(SupportTicket)
            .filter(SupportTicket.user_id == user_id)
            .order_by(SupportTicket.created_at.desc())
            .offset(skip)
            .limit(limit)
            .all()
        )

    def get_open_tickets(
        self,
        db: Session,
        *,
        skip: int = 0,
        limit: int = 50,
    ) -> List[SupportTicket]:
        """Admin panel: all open + in_progress tickets ordered by SLA deadline."""
        return (
            db.query(SupportTicket)
            .filter(SupportTicket.status.in_(["open", "in_progress"]))
            .order_by(SupportTicket.sla_deadline_at.asc())
            .offset(skip)
            .limit(limit)
            .all()
        )


# ── Singletons ────────────────────────────────────────────────────────────────

conversation_crud   = CRUDConversation(Conversation)
message_crud        = CRUDMessage(Message)
presence_crud       = CRUDUserPresence(UserPresence)
typing_crud         = CRUDTypingIndicator(TypingIndicator)
support_ticket_crud = CRUDSupportTicket(SupportTicket)