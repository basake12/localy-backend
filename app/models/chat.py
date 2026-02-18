from sqlalchemy import (
    Column, String, Boolean, Text, Integer,
    DateTime, ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB
from geoalchemy2 import Geography
import enum

from app.models.base import BaseModel


# ============================================
# ENUMS
# ============================================

class MessageTypeEnum(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    LOCATION = "location"
    SYSTEM = "system"       # Auto-generated (e.g. "John joined the chat")
    TEMPLATE = "template"   # Quick-reply templates


class ConversationTypeEnum(str, enum.Enum):
    DIRECT = "direct"           # 1:1
    BUSINESS = "business"       # Customer <-> Business
    SUPPORT = "support"         # Customer <-> Platform support


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

    # Context — links the chat to a specific order/booking if applicable
    # e.g. customer messaging a restaurant about their food order
    context_type = Column(String(50), nullable=True)   # food_order, hotel_booking, product_order, etc
    context_id = Column(UUID(as_uuid=True), nullable=True)

    # Metadata - REMOVED FOREIGN KEY to avoid circular dependency
    # This is just a denormalized cache field for performance
    last_message_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        index=True
    )
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    last_message_preview = Column(String(255), nullable=True)

    # Unread counts — denormalised for performance
    unread_count_user_one = Column(Integer, default=0)
    unread_count_user_two = Column(Integer, default=0)

    # Mute / archive per user
    is_muted_user_one = Column(Boolean, default=False)
    is_muted_user_two = Column(Boolean, default=False)
    is_archived_user_one = Column(Boolean, default=False)
    is_archived_user_two = Column(Boolean, default=False)

    # Active
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
    # Note: last_message relationship removed due to circular dependency
    # Use application logic to fetch last message: conversation.messages[-1] if conversation.messages else None

    __table_args__ = (
        UniqueConstraint(
            'user_one_id', 'user_two_id', 'context_type', 'context_id',
            name='unique_conversation_pair'
        ),
        Index('ix_conversations_user_one', 'user_one_id'),
        Index('ix_conversations_user_two', 'user_two_id'),
    )

    def __repr__(self):
        return f"<Conversation {self.id}>"


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
    content = Column(Text, nullable=True)  # Text body or caption

    # Media payload (JSONB — flexible per type)
    media = Column(JSONB, nullable=True)
    # text     -> null
    # image    -> {"url": "...", "width": 800, "height": 600, "thumbnail_url": "..."}
    # file     -> {"url": "...", "name": "doc.pdf", "size_bytes": 1024, "mime_type": "application/pdf"}
    # location -> {"latitude": 9.07, "longitude": 7.40, "address": "..."}
    # template -> {"options": ["Yes", "No", "Maybe"], "selected": null}

    # Threading
    reply_to_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True
    )

    # Read receipts
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime(timezone=True), nullable=True)

    # Delivery receipt
    is_delivered = Column(Boolean, default=False)
    delivered_at = Column(DateTime(timezone=True), nullable=True)

    # Editing
    is_edited = Column(Boolean, default=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted = Column(Boolean, default=False)
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), nullable=True)

    # Reactions
    reactions = Column(JSONB, default=list)
    # [{"user_id": "uuid", "emoji": "👍", "reacted_at": "2026-..."}]

    # Relationships
    conversation = relationship("Conversation", back_populates="messages")
    sender = relationship("User", foreign_keys=[sender_id])
    reply_to = relationship("Message", remote_side="Message.id", foreign_keys=[reply_to_message_id])

    __table_args__ = (
        Index('ix_messages_conversation_created', 'conversation_id', 'created_at'),
    )

    def __repr__(self):
        return f"<Message {self.id} type={self.message_type}>"


# ============================================
# ONLINE PRESENCE MODEL
# ============================================

class UserPresence(BaseModel):
    """Tracks online status & last seen"""
    __tablename__ = "user_presences"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True
    )

    is_online = Column(Boolean, default=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), default="online")  # online, away, busy, offline
    device_type = Column(String(20), nullable=True)  # web, mobile, desktop

    # Relationships
    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self):
        return f"<UserPresence user={self.user_id} online={self.is_online}>"


# ============================================
# TYPING INDICATOR (ephemeral — stored in memory / Redis in prod)
# For the DB-backed fallback we keep a short-lived row
# ============================================

class TypingIndicator(BaseModel):
    """Who is currently typing in a conversation"""
    __tablename__ = "typing_indicators"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False
    )

    # expires_at lets a background job (or query filter) auto-clean stale rows
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint('conversation_id', 'user_id', name='unique_typing_indicator'),
    )