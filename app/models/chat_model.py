from sqlalchemy import (
    Column, String, Boolean, Text, Integer,
    DateTime, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
import enum

from app.models.base_model import BaseModel


# ============================================
# ENUMS
# ============================================

class MessageTypeEnum(str, enum.Enum):
    TEXT     = "text"
    IMAGE    = "image"
    VOICE    = "voice"
    FILE     = "file"
    LOCATION = "location"
    SYSTEM   = "system"
    TEMPLATE = "template"


class ConversationTypeEnum(str, enum.Enum):
    DIRECT   = "direct"     # General 1:1
    BUSINESS = "business"   # Customer ↔ Business (Blueprint §9.1)
    RIDER    = "rider"      # Customer ↔ Rider — delivery-scoped only (Blueprint §9.2)
    SUPPORT  = "support"    # Customer ↔ Platform support team (Blueprint §9.3)


# ============================================
# CONVERSATION MODEL
# ============================================

class Conversation(BaseModel):
    """Chat conversation / thread"""
    __tablename__ = "conversations"

    # Type
    conversation_type = Column(String(20), nullable=False, default="direct")

    # Participants (always exactly 2 for now)
    user_one_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_two_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Context — links the chat to a specific order/booking/delivery if applicable
    # For rider chats: context_type = "delivery", context_id = delivery.id
    # For business chats: context_type = "food_order" | "hotel_booking" | etc.
    # For support chats: context_type = "support_ticket", context_id = null
    context_type = Column(String(50), nullable=True)
    context_id   = Column(UUID(as_uuid=True), nullable=True)

    # Denormalised cache — no FK to avoid circular dependency
    last_message_id      = Column(UUID(as_uuid=True), nullable=True, index=True)
    last_message_at      = Column(DateTime(timezone=True), nullable=True)
    last_message_preview = Column(String(255), nullable=True)

    # Unread counts — denormalised for performance
    unread_count_user_one = Column(Integer, default=0)
    unread_count_user_two = Column(Integer, default=0)

    # Per-user flags
    is_muted_user_one    = Column(Boolean, default=False)
    is_muted_user_two    = Column(Boolean, default=False)
    is_archived_user_one = Column(Boolean, default=False)
    is_archived_user_two = Column(Boolean, default=False)

    # active = False when auto-closed (rider chat 1hr after delivery complete)
    is_active = Column(Boolean, default=True)

    # Relationships
    user_one = relationship("User", foreign_keys=[user_one_id])
    user_two = relationship("User", foreign_keys=[user_two_id])
    messages = relationship(
        "Message",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at.asc()",
        foreign_keys="Message.conversation_id"
    )

    __table_args__ = (
        UniqueConstraint(
            'user_one_id', 'user_two_id', 'context_type', 'context_id',
            name='unique_conversation_pair'
        ),
        Index('ix_conversations_user_one', 'user_one_id'),
        Index('ix_conversations_user_two', 'user_two_id'),
    )

    def __repr__(self):
        return f"<Conversation {self.id} type={self.conversation_type}>"


# ============================================
# MESSAGE MODEL
# ============================================

class Message(BaseModel):
    """Individual chat message"""
    __tablename__ = "messages"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    sender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    # Content
    message_type = Column(String(20), nullable=False, default="text")
    content      = Column(Text, nullable=True)

    # Media payload (JSONB — flexible per type)
    media = Column(JSONB, nullable=True)
    # text     -> null
    # image    -> {"url": "...", "width": 800, "height": 600, "thumbnail_url": "..."}
    # voice    -> {"url": "...", "duration_seconds": 12}
    # file     -> {"url": "...", "name": "doc.pdf", "size_bytes": 1024, "mime_type": "..."}
    # location -> {"latitude": 9.07, "longitude": 7.40, "address": "..."}
    # template -> {"options": ["Yes", "No"], "selected": null}

    # Threading
    reply_to_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True
    )

    # Read receipts
    is_read  = Column(Boolean, default=False)
    read_at  = Column(DateTime(timezone=True), nullable=True)

    # Delivery receipt
    is_delivered  = Column(Boolean, default=False)
    delivered_at  = Column(DateTime(timezone=True), nullable=True)

    # Editing
    is_edited = Column(Boolean, default=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted    = Column(Boolean, default=False)
    deleted_at    = Column(DateTime(timezone=True), nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), nullable=True)

    # Reactions — [{"user_id": "uuid", "emoji": "👍", "reacted_at": "..."}]
    reactions = Column(JSONB, default=list)

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    sender       = relationship("User", foreign_keys=[sender_id])
    reply_to     = relationship(
        "Message",
        remote_side="Message.id",
        foreign_keys=[reply_to_message_id],
        uselist=False
    )

    __table_args__ = (
        Index('ix_messages_conversation_created', 'conversation_id', 'created_at'),
    )

    def __repr__(self):
        return f"<Message {self.id} type={self.message_type}>"


# ============================================
# ONLINE PRESENCE
# ============================================

class UserPresence(BaseModel):
    """Tracks online status & last seen"""
    __tablename__ = "user_presences"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True
    )

    is_online    = Column(Boolean, default=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status       = Column(String(20), default="online")  # online | away | busy | offline
    device_type  = Column(String(20), nullable=True)     # web | mobile | desktop

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<UserPresence user={self.user_id} online={self.is_online}>"


# ============================================
# TYPING INDICATOR (ephemeral — Redis in prod, DB fallback)
# ============================================

class TypingIndicator(BaseModel):
    """Who is currently typing in a conversation"""
    __tablename__ = "typing_indicators"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint('conversation_id', 'user_id', name='unique_typing_indicator'),
    )