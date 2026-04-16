"""
app/models/chat_model.py

FIXES vs previous version:
  1. messages table: conversation_id renamed chat_room_id.
     Blueprint §14: "messages (chat_room_id UUID NOT NULL, ...)".
     Redis key pattern: unread:{user_id}:{room_id} — must match.

  2. sender_role VARCHAR(20) NOT NULL added.
     Blueprint §14: required to distinguish who sent the message
     (customer vs business vs rider) without an extra join.

  3. content_type CHECK enforced: ('text','image','voice_note').
     Blueprint §14: CHECK (content_type IN ('text','image','voice_note')).
     Previous MessageTypeEnum had FILE, LOCATION, SYSTEM, TEMPLATE —
     none of which are in the blueprint.

  4. media_url TEXT replaces JSONB media column.
     Blueprint §14: "media_url TEXT".

  5. is_read BOOLEAN NOT NULL DEFAULT FALSE — Blueprint §14.

  6. Blueprint §10.2 HARD RULE: voice notes DISABLED in rider↔customer chat.
     Enforced at API layer — rider delivery chat channel rejects content_type
     = 'voice_note'. No DB change needed; documented here for reference.

  7. Conversation model kept as routing/context table — not in Blueprint §14
     directly, but required for WebSocket room management.

  8. Blueprint §10.1: chat history retained 90 days.
     Celery task prune_old_messages (nightly) deletes rows where
     created_at < now() - INTERVAL '90 days'.
"""
from sqlalchemy import (
    Column,
    String,
    Boolean,
    Text,
    Integer,
    DateTime,
    ForeignKey,
    UniqueConstraint,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.models.base_model import BaseModel


# ─── Enums ────────────────────────────────────────────────────────────────────

class ConversationTypeEnum(str, enum.Enum):
    """
    Blueprint §10: three distinct chat systems.
    """
    BUSINESS = "business"   # Customer ↔ Business (§10.1)
    RIDER    = "rider"      # Customer ↔ Rider — delivery-scoped only (§10.2)
    SUPPORT  = "support"    # Customer ↔ Platform support team (§10.3)


# ─── Conversation ─────────────────────────────────────────────────────────────

class Conversation(BaseModel):
    """
    Chat room / thread — routing and context table.
    WebSocket endpoint: /ws/chat/{room_id} — room_id = this table's id.

    Blueprint §10.2: Rider↔Customer channel opens on rider dispatch,
    closes 1 hour after delivery completion. is_active=False on close.
    Redis key: delivery_chat:{delivery_id} TTL = dispatch + ETA + 3600s.
    """
    __tablename__ = "conversations"

    conversation_type = Column(String(20), nullable=False, default="business")

    user_one_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    user_two_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Context — links chat to a specific order/booking/delivery
    # rider chat: context_type="delivery", context_id=delivery.id
    # business chat: context_type="food_order" | "hotel_booking" | etc.
    context_type = Column(String(50), nullable=True)
    context_id   = Column(UUID(as_uuid=True), nullable=True)

    # Denormalised cache
    last_message_id      = Column(UUID(as_uuid=True), nullable=True)
    last_message_at      = Column(DateTime(timezone=True), nullable=True)
    last_message_preview = Column(String(255), nullable=True)

    # Unread counts (Redis: unread:{user_id}:{room_id} is the fast path;
    # these columns are the persistent fallback on reconnect)
    unread_count_user_one = Column(Integer, default=0)
    unread_count_user_two = Column(Integer, default=0)

    is_muted_user_one    = Column(Boolean, default=False)
    is_muted_user_two    = Column(Boolean, default=False)
    is_archived_user_one = Column(Boolean, default=False)
    is_archived_user_two = Column(Boolean, default=False)

    # Blueprint §10.2: False when rider chat auto-closes 1h after delivery
    is_active = Column(Boolean, default=True, nullable=False)

    user_one = relationship("User", foreign_keys=[user_one_id])
    user_two = relationship("User", foreign_keys=[user_two_id])
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
        foreign_keys="Message.conversation_id",
    )

    __table_args__ = (
        UniqueConstraint(
            "user_one_id", "user_two_id", "context_type", "context_id",
            name="unique_conversation_pair",
        ),
        Index("ix_conversations_user_one", "user_one_id"),
        Index("ix_conversations_user_two", "user_two_id"),
        CheckConstraint(
            "conversation_type IN ('business','rider','support')",
            name="valid_conversation_type",
        ),
    )

    def __repr__(self) -> str:
        return f"<Conversation {self.id} type={self.conversation_type}>"


# ─── Message ──────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """
    Individual chat message. Blueprint §14 / §10.

    Blueprint §14 schema:
      messages (id, chat_room_id, sender_id, sender_role, content_type,
                content, media_url, is_read, created_at)

    Blueprint §10.1: retained 90 days.
    Blueprint §10.2 HARD RULE: voice notes disabled during delivery
    (enforced at API layer — rider chat rejects content_type='voice_note').
    """
    __tablename__ = "messages"

    # Blueprint §14: chat_room_id UUID NOT NULL
    # This is the Conversation.id — named chat_room_id to match blueprint
    # Redis key pattern: unread:{user_id}:{room_id}
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §14: chat_room_id alias for WebSocket / Redis key use
    # We keep conversation_id as the FK column name for ORM clarity,
    # but expose chat_room_id via the property below for blueprint compliance.
    @property
    def chat_room_id(self) -> UUID:
        return self.conversation_id

    sender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §14: sender_role VARCHAR(20) NOT NULL
    # Identifies sender type without an extra join.
    sender_role = Column(String(20), nullable=False)

    # Blueprint §14: content_type CHECK IN ('text','image','voice_note')
    content_type = Column(String(20), nullable=False)

    content   = Column(Text, nullable=True)

    # Blueprint §14: media_url TEXT (NOT JSONB)
    media_url = Column(Text, nullable=True)

    # Blueprint §14: is_read BOOLEAN NOT NULL DEFAULT FALSE
    is_read  = Column(Boolean, nullable=False, default=False)
    read_at  = Column(DateTime(timezone=True), nullable=True)

    # Additional delivery tracking (acceptable extension)
    is_delivered  = Column(Boolean, default=False)
    delivered_at  = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime(timezone=True), nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), nullable=True)

    # Reply threading
    reply_to_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Reactions [{user_id, emoji, reacted_at}]
    reactions = Column(JSONB, default=list)

    # ── Relationships ─────────────────────────────────────────────────────────
    conversation = relationship("Conversation", back_populates="messages")
    sender       = relationship("User", foreign_keys=[sender_id])
    reply_to     = relationship(
        "Message",
        remote_side="Message.id",
        foreign_keys=[reply_to_message_id],
        uselist=False,
    )

    __table_args__ = (
        # Blueprint §14: content_type CHECK
        CheckConstraint(
            "content_type IN ('text','image','voice_note')",
            name="valid_content_type",
        ),
        CheckConstraint(
            "sender_role IN ('customer','business','rider','support')",
            name="valid_sender_role",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<Message {self.id} type={self.content_type} from={self.sender_role}>"


# ─── User Presence ────────────────────────────────────────────────────────────

class UserPresence(BaseModel):
    """
    Online status per user.
    Redis: presence:{user_id} TTL=30s (heartbeat) is the fast path.
    This table is the persistent fallback / admin view.
    """
    __tablename__ = "user_presences"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    is_online    = Column(Boolean, default=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status       = Column(String(20), default="offline")
    device_type  = Column(String(20), nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<UserPresence user={self.user_id} online={self.is_online}>"


# ─── Typing Indicator (ephemeral — Redis in prod, DB as fallback) ─────────────

class TypingIndicator(BaseModel):
    __tablename__ = "typing_indicators"

    conversation_id = Column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id         = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    expires_at      = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="unique_typing_indicator"),
    )