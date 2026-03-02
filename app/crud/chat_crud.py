from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, or_, func, desc
from uuid import UUID
from datetime import datetime, timedelta

from app.crud.base_crud import CRUDBase
from app.models.chat_model import (
    Conversation, Message, UserPresence, TypingIndicator
)
from app.core.exceptions import NotFoundException, ValidationException


# ============================================
# CONVERSATION CRUD
# ============================================

class CRUDConversation(CRUDBase[Conversation, dict, dict]):

    def get_or_create(
        self, db: Session, *,
        user_one_id: UUID, user_two_id: UUID,
        conversation_type: str = "direct",
        context_type: Optional[str] = None,
        context_id: Optional[UUID] = None
    ) -> tuple[Conversation, bool]:
        """
        Idempotent: return existing conversation or create one.
        Returns (conversation, created_flag).
        """
        # Normalise order so we never get duplicate pairs
        uid_a, uid_b = sorted([str(user_one_id), str(user_two_id)])

        existing = db.query(Conversation).filter(
            and_(
                Conversation.user_one_id == uid_a,
                Conversation.user_two_id == uid_b,
                Conversation.context_type == context_type,
                Conversation.context_id == context_id
            )
        ).first()

        if existing:
            return existing, False

        convo = Conversation(
            user_one_id=uid_a,
            user_two_id=uid_b,
            conversation_type=conversation_type,
            context_type=context_type,
            context_id=context_id
        )
        db.add(convo)
        db.commit()
        db.refresh(convo)
        return convo, True

    def get_conversations_for_user(
        self, db: Session, *,
        user_id: UUID,
        include_archived: bool = False,
        skip: int = 0, limit: int = 40
    ) -> List[Conversation]:
        """Latest-first list of conversations the user is part of."""
        query = db.query(Conversation).options(
            joinedload(Conversation.last_message)
        ).filter(
            or_(
                Conversation.user_one_id == user_id,
                Conversation.user_two_id == user_id
            )
        )

        if not include_archived:
            # Filter out archived for *this* user
            query = query.filter(
                and_(
                    # If user is user_one, check user_one archive flag; otherwise user_two
                    or_(
                        and_(Conversation.user_one_id == user_id, Conversation.is_archived_user_one == False),
                        and_(Conversation.user_two_id == user_id, Conversation.is_archived_user_two == False)
                    )
                )
            )

        return query.order_by(
            Conversation.last_message_at.desc().nullslast()
        ).offset(skip).limit(limit).all()

    def mark_read(self, db: Session, *, conversation_id: UUID, user_id: UUID) -> None:
        """Zero out unread counter for the given user."""
        convo = self.get(db, id=conversation_id)
        if not convo:
            return

        if convo.user_one_id == user_id:
            convo.unread_count_user_one = 0
        else:
            convo.unread_count_user_two = 0

        # Mark all messages as read
        db.query(Message).filter(
            and_(
                Message.conversation_id == conversation_id,
                Message.sender_id != user_id,
                Message.is_read == False
            )
        ).update({"is_read": True, "read_at": datetime.utcnow()})

        db.commit()

    def mute_toggle(self, db: Session, *, conversation_id: UUID, user_id: UUID) -> bool:
        convo = self.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        if convo.user_one_id == user_id:
            convo.is_muted_user_one = not convo.is_muted_user_one
            db.commit()
            return convo.is_muted_user_one
        else:
            convo.is_muted_user_two = not convo.is_muted_user_two
            db.commit()
            return convo.is_muted_user_two

    def archive_toggle(self, db: Session, *, conversation_id: UUID, user_id: UUID) -> bool:
        convo = self.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        if convo.user_one_id == user_id:
            convo.is_archived_user_one = not convo.is_archived_user_one
            db.commit()
            return convo.is_archived_user_one
        else:
            convo.is_archived_user_two = not convo.is_archived_user_two
            db.commit()
            return convo.is_archived_user_two

    def other_user_id(self, conversation: Conversation, current_user_id: UUID) -> UUID:
        """Helper: given a conversation, return the *other* user's id."""
        return conversation.user_two_id if conversation.user_one_id == current_user_id else conversation.user_one_id


# ============================================
# MESSAGE CRUD
# ============================================

class CRUDMessage(CRUDBase[Message, dict, dict]):

    def create_message(
        self, db: Session, *,
        conversation_id: UUID,
        sender_id: UUID,
        message_type: str = "text",
        content: Optional[str] = None,
        media: Optional[Dict] = None,
        reply_to_message_id: Optional[UUID] = None
    ) -> Message:
        # Validate conversation membership
        convo = conversation_crud.get(db, id=conversation_id)
        if not convo:
            raise NotFoundException("Conversation")

        if sender_id not in (convo.user_one_id, convo.user_two_id):
            raise ValidationException("You are not part of this conversation")

        # Validate reply target if provided
        if reply_to_message_id:
            reply_msg = self.get(db, id=reply_to_message_id)
            if not reply_msg or reply_msg.conversation_id != conversation_id:
                raise ValidationException("Reply target not in this conversation")

        msg = Message(
            conversation_id=conversation_id,
            sender_id=sender_id,
            message_type=message_type,
            content=content,
            media=media,
            reply_to_message_id=reply_to_message_id
        )
        db.add(msg)
        db.flush()

        # ── update conversation metadata ──
        convo.last_message_id = msg.id
        convo.last_message_at = msg.created_at
        convo.last_message_preview = (content or "")[:255] if message_type == "text" else f"[{message_type}]"

        # Bump unread for the *other* user
        if convo.user_one_id == sender_id:
            convo.unread_count_user_two += 1
        else:
            convo.unread_count_user_one += 1

        db.commit()
        db.refresh(msg)
        return msg

    def get_messages(
        self, db: Session, *,
        conversation_id: UUID,
        before_id: Optional[UUID] = None,   # cursor-based pagination
        limit: int = 40
    ) -> List[Message]:
        """Newest-first page, optionally starting before a given message id."""
        query = db.query(Message).options(
            joinedload(Message.sender)
        ).filter(
            Message.conversation_id == conversation_id
        )

        if before_id:
            cursor_msg = self.get(db, id=before_id)
            if cursor_msg:
                query = query.filter(Message.created_at < cursor_msg.created_at)

        return query.order_by(desc(Message.created_at)).limit(limit).all()

    def soft_delete(self, db: Session, *, message_id: UUID, user_id: UUID) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")
        if msg.sender_id != user_id:
            raise ValidationException("You can only delete your own messages")
        if msg.is_deleted:
            raise ValidationException("Already deleted")

        msg.is_deleted = True
        msg.deleted_at = datetime.utcnow()
        msg.deleted_by_id = user_id
        msg.content = None
        msg.media = None
        db.commit()
        db.refresh(msg)
        return msg

    def edit_message(self, db: Session, *, message_id: UUID, user_id: UUID, new_content: str) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")
        if msg.sender_id != user_id:
            raise ValidationException("You can only edit your own messages")
        if msg.is_deleted:
            raise ValidationException("Cannot edit a deleted message")
        if msg.message_type != "text":
            raise ValidationException("Can only edit text messages")

        msg.content = new_content
        msg.is_edited = True
        msg.edited_at = datetime.utcnow()
        db.commit()
        db.refresh(msg)
        return msg

    def add_reaction(self, db: Session, *, message_id: UUID, user_id: UUID, emoji: str) -> Message:
        msg = self.get(db, id=message_id)
        if not msg:
            raise NotFoundException("Message")

        reactions: list = msg.reactions or []

        # Toggle: remove if already reacted with same emoji, else add
        existing_idx = next(
            (i for i, r in enumerate(reactions) if r["user_id"] == str(user_id) and r["emoji"] == emoji),
            None
        )
        if existing_idx is not None:
            reactions.pop(existing_idx)
        else:
            reactions.append({
                "user_id": str(user_id),
                "emoji": emoji,
                "reacted_at": datetime.utcnow().isoformat()
            })

        msg.reactions = reactions
        db.commit()
        db.refresh(msg)
        return msg

    def mark_delivered(self, db: Session, *, message_id: UUID) -> None:
        msg = self.get(db, id=message_id)
        if msg and not msg.is_delivered:
            msg.is_delivered = True
            msg.delivered_at = datetime.utcnow()
            db.commit()


# ============================================
# PRESENCE CRUD
# ============================================

class CRUDUserPresence(CRUDBase[UserPresence, dict, dict]):

    def get_or_create(self, db: Session, *, user_id: UUID) -> UserPresence:
        presence = db.query(UserPresence).filter(UserPresence.user_id == user_id).first()
        if not presence:
            presence = UserPresence(user_id=user_id)
            db.add(presence)
            db.commit()
            db.refresh(presence)
        return presence

    def update_presence(
        self, db: Session, *,
        user_id: UUID, is_online: bool,
        status: str = "online", device_type: Optional[str] = None
    ) -> UserPresence:
        presence = self.get_or_create(db, user_id=user_id)
        presence.is_online = is_online
        presence.status = status
        presence.last_seen_at = datetime.utcnow()
        if device_type:
            presence.device_type = device_type
        db.commit()
        db.refresh(presence)
        return presence

    def get_presence(self, db: Session, *, user_id: UUID) -> Optional[UserPresence]:
        return db.query(UserPresence).filter(UserPresence.user_id == user_id).first()


# ============================================
# TYPING INDICATOR CRUD
# ============================================

class CRUDTypingIndicator(CRUDBase[TypingIndicator, dict, dict]):

    def start_typing(self, db: Session, *, conversation_id: UUID, user_id: UUID) -> None:
        existing = db.query(TypingIndicator).filter(
            and_(
                TypingIndicator.conversation_id == conversation_id,
                TypingIndicator.user_id == user_id
            )
        ).first()

        expires = datetime.utcnow() + timedelta(seconds=5)

        if existing:
            existing.expires_at = expires
        else:
            db.add(TypingIndicator(
                conversation_id=conversation_id,
                user_id=user_id,
                expires_at=expires
            ))
        db.commit()

    def stop_typing(self, db: Session, *, conversation_id: UUID, user_id: UUID) -> None:
        db.query(TypingIndicator).filter(
            and_(
                TypingIndicator.conversation_id == conversation_id,
                TypingIndicator.user_id == user_id
            )
        ).delete()
        db.commit()

    def cleanup_expired(self, db: Session) -> None:
        """Purge rows whose expires_at < now.  Run on a schedule or per-request."""
        db.query(TypingIndicator).filter(
            TypingIndicator.expires_at < datetime.utcnow()
        ).delete()
        db.commit()


# Singletons
conversation_crud = CRUDConversation(Conversation)
message_crud = CRUDMessage(Message)
presence_crud = CRUDUserPresence(UserPresence)
typing_crud = CRUDTypingIndicator(TypingIndicator)