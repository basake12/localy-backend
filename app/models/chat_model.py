"""
app/models/chat_model.py

Blueprint §14 messages table schema (exact column names):
  messages (id, chat_room_id, sender_id, sender_role, content_type,
            content, media_url, is_read, created_at)

Blueprint §10.1 — Business ↔ Customer chat, 90-day retention.
Blueprint §10.2 — Rider ↔ Customer chat, voice notes HARD BLOCKED at API layer.
Blueprint §10.3 — Platform support chat, ticket-based, Open→In Progress→Resolved.
Blueprint §16.3 — Redis keys:
  unread:{user_id}:{room_id}  — fast unread count
  presence:{user_id}          — online status, TTL=30s heartbeat
  delivery_chat:{delivery_id} — rider chat state, TTL = dispatch + ETA + 3600s

FIXES vs previous version:
  1. is_edited + edited_at columns ADDED to Message (used in CRUD but were missing).
  2. content_type is the canonical DB column name — NOT message_type.
  3. CHECK constraint: ('text','image','voice_note') only.
     'system' is NOT valid — system notifications go via WebSocket push only.
  4. media_url TEXT column (blueprint §14) — NOT JSONB media column.
  5. sender_role NOT NULL — CRUD must always supply this.
  6. SupportTicket model ADDED — blueprint §10.3 + §15 require it.
     Status: open → in_progress → resolved.
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


# ── Enums ─────────────────────────────────────────────────────────────────────

class ConversationTypeEnum(str, enum.Enum):
    """
    Blueprint §10: three distinct chat systems.
    """
    BUSINESS = "business"   # Customer ↔ Business  (§10.1)
    RIDER    = "rider"      # Customer ↔ Rider — delivery-scoped only (§10.2)
    SUPPORT  = "support"    # Customer ↔ Platform support team (§10.3)


# ── Conversation ──────────────────────────────────────────────────────────────

class Conversation(BaseModel):
    """
    Chat room / thread routing and context table.
    WebSocket endpoint: /ws/chat/{room_id} — room_id = this table's id.

    Blueprint §10.2: Rider chat opens on rider dispatch, closes 1hr after
    delivery completion. is_active=False on close.
    Redis: delivery_chat:{delivery_id} TTL = dispatch + ETA + 3600s.
    """
    __tablename__ = "conversations"

    conversation_type = Column(
        String(20),
        nullable=False,
        default="business",
    )

    user_one_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_two_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Context — links chat to a specific order/booking/delivery
    # rider chat:   context_type="delivery",       context_id=delivery.id
    # business chat: context_type="food_order" | "hotel_booking" | etc.
    # support chat: context_type="support_ticket", context_id=ticket.id
    context_type = Column(String(50), nullable=True)
    context_id   = Column(UUID(as_uuid=True), nullable=True)

    # Denormalised preview — updated on every new message
    last_message_id      = Column(UUID(as_uuid=True), nullable=True)
    last_message_at      = Column(DateTime(timezone=True), nullable=True)
    last_message_preview = Column(String(255), nullable=True)

    # DB-persisted unread counters.
    # Redis unread:{user_id}:{room_id} is the fast path (§16.3).
    # These columns are the persistent fallback on reconnect.
    unread_count_user_one = Column(Integer, nullable=False, default=0)
    unread_count_user_two = Column(Integer, nullable=False, default=0)

    is_muted_user_one    = Column(Boolean, nullable=False, default=False)
    is_muted_user_two    = Column(Boolean, nullable=False, default=False)
    is_archived_user_one = Column(Boolean, nullable=False, default=False)
    is_archived_user_two = Column(Boolean, nullable=False, default=False)

    # Blueprint §10.2: set False when rider chat auto-closes 1hr after delivery
    is_active = Column(Boolean, nullable=False, default=True)

    # ── Relationships ─────────────────────────────────────────────────────────
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


# ── Message ───────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """
    Blueprint §14 messages table.

    Exact blueprint column names used:
      chat_room_id → exposed via @property (FK stored as conversation_id for ORM)
      sender_id, sender_role, content_type, content, media_url, is_read, created_at

    Blueprint §10.2 HARD RULE: content_type='voice_note' REJECTED at API/WS layer
    for ConversationTypeEnum.RIDER conversations.

    Blueprint §10.1: 90-day retention.
    Celery task prune_old_messages (nightly):
      DELETE FROM messages WHERE created_at < now() - INTERVAL '90 days'

    FIXES:
      - content_type column (NOT message_type)
      - media_url TEXT (NOT JSONB media column)
      - sender_role NOT NULL
      - is_edited + edited_at columns added (were missing, CRUD was writing to them)
      - 'system' NOT a valid content_type — check constraint enforces this
    """
    __tablename__ = "messages"

    # Blueprint §14: chat_room_id UUID NOT NULL
    # Stored as conversation_id for ORM join clarity.
    # Blueprint name exposed via @property for Redis key construction.
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    @property
    def chat_room_id(self) -> "UUID":
        """
        Blueprint §14 + §16.3 alias.
        Used when constructing Redis key: unread:{user_id}:{room_id}
        """
        return self.conversation_id

    sender_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Blueprint §14: sender_role VARCHAR(20) NOT NULL
    # Identifies sender type without an extra join.
    # FIX: was never set in create_message — caused NOT NULL violation every insert.
    sender_role = Column(String(20), nullable=False)

    # Blueprint §14: content_type CHECK IN ('text','image','voice_note')
    # FIX: column was referenced as 'message_type' in CRUD — wrong kwarg name.
    # NOTE: 'system' is NOT valid. System notifications are WS push events only.
    content_type = Column(String(20), nullable=False)

    content = Column(Text, nullable=True)

    # Blueprint §14: media_url TEXT (single URL string)
    # FIX: was a JSONB 'media' column — blueprint specifies TEXT.
    media_url = Column(Text, nullable=True)

    # Blueprint §14: is_read BOOLEAN NOT NULL DEFAULT FALSE
    is_read  = Column(Boolean, nullable=False, default=False)
    read_at  = Column(DateTime(timezone=True), nullable=True)

    # Delivery tracking (not in blueprint schema but required for delivered ticks)
    is_delivered  = Column(Boolean, nullable=False, default=False)
    delivered_at  = Column(DateTime(timezone=True), nullable=True)

    # FIX: is_edited + edited_at were used in chat_crud.py and the router
    # serialiser, but were never declared as columns — changes were silently lost.
    is_edited = Column(Boolean, nullable=False, default=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)

    # Soft delete
    is_deleted    = Column(Boolean, nullable=False, default=False)
    deleted_at    = Column(DateTime(timezone=True), nullable=True)
    deleted_by_id = Column(UUID(as_uuid=True), nullable=True)

    # Reply threading
    reply_to_message_id = Column(
        UUID(as_uuid=True),
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Reactions: [{user_id, emoji, reacted_at}]
    reactions = Column(JSONB, nullable=False, default=list)

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
        # Blueprint §14: content_type CHECK — 'system' is NOT in this list
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


# ── SupportTicket ─────────────────────────────────────────────────────────────

class SupportTicket(BaseModel):
    """
    Blueprint §10.3: support ticket with Open → In Progress → Resolved lifecycle.
    Blueprint §11.3: all tickets visible in admin panel.
    Blueprint §15: POST /support/tickets, GET /support/tickets/{id},
                   WS /ws/support/{ticket_id}

    SLA by plan (§10.3):
      Free / Starter: 24-hour first human response
      Pro:            4-hour first human response
      Enterprise:     1-hour first response + dedicated account manager

    FIX: previous implementation had no SupportTicket model at all —
    support was treated as a plain Conversation which lacks status tracking,
    SLA tracking, and admin panel visibility.
    """
    __tablename__ = "support_tickets"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Linked to the Conversation used for WS chat on this ticket
    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Brief description of the issue
    subject = Column(String(500), nullable=False)

    # Blueprint §10.3: Open → In Progress → Resolved
    status = Column(String(20), nullable=False, default="open")

    # Support agent assigned to this ticket (admin_users table)
    assigned_agent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # SLA deadline set at ticket creation based on user's subscription tier
    sla_deadline_at = Column(DateTime(timezone=True), nullable=True)

    # Resolution fields
    resolved_at     = Column(DateTime(timezone=True), nullable=True)
    resolution_note = Column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    user         = relationship("User", foreign_keys=[user_id])
    conversation = relationship("Conversation", foreign_keys=[conversation_id])

    __table_args__ = (
        CheckConstraint(
            "status IN ('open','in_progress','resolved')",
            name="valid_ticket_status",
        ),
        Index("ix_support_tickets_user_status", "user_id", "status"),
        Index("ix_support_tickets_status_created", "status", "created_at"),
    )

    def __repr__(self) -> str:
        return f"<SupportTicket {self.id} status={self.status}>"


# ── UserPresence ──────────────────────────────────────────────────────────────

class UserPresence(BaseModel):
    """
    Blueprint §16.3: presence:{user_id} TTL=30s (Redis heartbeat) is the fast path.
    This table is the persistent fallback and admin view only.
    Redis key is set/expired by chat_crud.CRUDUserPresence.update_presence().
    """
    __tablename__ = "user_presences"

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    is_online    = Column(Boolean, nullable=False, default=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    status       = Column(String(20), nullable=False, default="offline")
    device_type  = Column(String(20), nullable=True)

    user = relationship("User", foreign_keys=[user_id])

    def __repr__(self) -> str:
        return f"<UserPresence user={self.user_id} online={self.is_online}>"


# ── TypingIndicator ───────────────────────────────────────────────────────────

class TypingIndicator(BaseModel):
    """
    Ephemeral typing state. expires_at = now() + 5s, refreshed on each keypress.
    Production: consider Redis hash instead of DB rows for lower write pressure.
    """
    __tablename__ = "typing_indicators"

    conversation_id = Column(
        UUID(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "user_id",
            name="unique_typing_indicator",
        ),
    )

    def __repr__(self) -> str:
        return f"<TypingIndicator convo={self.conversation_id} user={self.user_id}>"