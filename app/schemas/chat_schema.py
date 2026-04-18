"""
app/schemas/chat_schema.py

FIXES vs previous version:
  1. MessageCreateRequest: message_type → content_type (Blueprint §14 column name).
     media: dict → media_url: str (Blueprint §14: media_url TEXT).

  2. MessageResponse: message_type → content_type.
     media: dict → media_url: str.
     chat_room_id field added (Blueprint §14 name).
     sender_role field added (Blueprint §14 NOT NULL).

  3. SupportChatRequest renamed → SupportTicketCreateRequest.
     subject field added (required for ticket creation, §10.3).
     Blueprint §15: POST /support/tickets requires a subject.

  4. SupportTicketResponse schema added.
     Blueprint §10.3: Open → In Progress → Resolved status visible to customer.
     Blueprint §15: GET /support/tickets/{id} response shape.
"""

from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ── Conversation Schemas ──────────────────────────────────────────────────────

class ConversationCreateRequest(BaseModel):
    """Start a new business ↔ customer conversation."""
    other_user_id: UUID
    context_type: Optional[str] = None
    context_id: Optional[UUID] = None
    initial_message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "other_user_id": "uuid-of-business-user",
            "context_type": "food_order",
            "context_id": "uuid-of-order",
            "initial_message": "Hi, I have a question about my order",
        }
    })


class ConversationResponse(BaseModel):
    id: UUID
    conversation_type: str
    other_user_id: UUID
    other_user_name: Optional[str] = None
    other_user_avatar: Optional[str] = None
    context_type: Optional[str]
    context_id: Optional[UUID]
    last_message_preview: Optional[str]
    last_message_at: Optional[datetime]
    unread_count: int = 0
    is_muted: bool = False
    is_archived: bool = False
    is_active: bool = True
    is_online: bool = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Support Ticket Schemas ────────────────────────────────────────────────────

class SupportTicketCreateRequest(BaseModel):
    """
    Blueprint §15: POST /support/tickets.
    Blueprint §10.3: ticket requires a subject; status starts at 'open'.
    SLA is set automatically from the user's subscription tier.

    FIX: previously named SupportChatRequest with no subject field —
    treated support as a plain conversation with no ticket concept.
    """
    subject: str = Field(..., min_length=5, max_length=500)
    initial_message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "subject": "I need help with a refund for order #12345",
            "initial_message": "The item arrived damaged.",
        }
    })


class SupportTicketResponse(BaseModel):
    """
    Blueprint §10.3: Open → In Progress → Resolved.
    Blueprint §15: GET /support/tickets/{id} response.
    """
    ticket_id: UUID
    subject: str
    status: str                          # open | in_progress | resolved
    sla_deadline_at: Optional[datetime]
    resolved_at: Optional[datetime]
    resolution_note: Optional[str]
    conversation: Optional[dict] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Message Schemas ───────────────────────────────────────────────────────────

class MessageCreateRequest(BaseModel):
    """
    Send a message.
    Blueprint §14: content_type IN ('text','image','voice_note').
                   media_url TEXT (not a dict).
    Blueprint §10.2 HARD RULE: content_type='voice_note' rejected in rider chats
    (enforced at router + service layers).
    """
    # FIX: was 'message_type' — blueprint §14 column is 'content_type'
    content_type: str = Field(
        "text",
        description="text | image | voice_note",
        pattern="^(text|image|voice_note)$",
    )
    content: Optional[str] = Field(None, max_length=10_000)
    # FIX: was 'media: dict' — blueprint §14 specifies media_url TEXT (single URL)
    media_url: Optional[str] = Field(None, max_length=2048)
    reply_to_message_id: Optional[UUID] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "content_type": "text",
            "content": "Where is my order?",
        }
    })


class MessageResponse(BaseModel):
    """
    Blueprint §14 fields:
      chat_room_id, sender_id, sender_role, content_type, content, media_url, is_read
    """
    id: UUID
    # FIX: Blueprint §14 name is chat_room_id (alias for conversation_id)
    chat_room_id: UUID
    sender_id: UUID
    # FIX: Blueprint §14 NOT NULL — was missing from response
    sender_role: str
    sender_name: Optional[str] = None
    # FIX: was 'message_type' — blueprint §14 column is 'content_type'
    content_type: str
    content: Optional[str]
    # FIX: was 'media: dict' — blueprint §14 is media_url TEXT
    media_url: Optional[str]
    reply_to_message_id: Optional[UUID]
    is_read: bool
    is_delivered: bool
    is_edited: bool
    is_deleted: bool
    reactions: List[Dict[str, Any]]
    created_at: datetime
    edited_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


class MessageUpdateRequest(BaseModel):
    """Edit a text message."""
    content: str = Field(..., min_length=1, max_length=10_000)


class ReactionRequest(BaseModel):
    """Toggle emoji reaction on a message."""
    emoji: str = Field(..., min_length=1, max_length=8)


# ── Presence Schemas ──────────────────────────────────────────────────────────

class PresenceUpdateRequest(BaseModel):
    """Update own online/away/offline status."""
    status: str = Field("online", description="online | away | busy | offline")
    device_type: Optional[str] = None


class PresenceResponse(BaseModel):
    user_id: UUID
    is_online: bool
    status: str
    last_seen_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ── Typing Indicator Schemas ──────────────────────────────────────────────────

class TypingStartRequest(BaseModel):
    """Sent when the user begins typing."""
    conversation_id: UUID


# ── WebSocket Event Payloads ──────────────────────────────────────────────────

class WSEventPayload(BaseModel):
    """
    Generic envelope for all WebSocket frames (client → server and server → client).
    Blueprint §10: "Real-time WebSocket messaging."
    """
    event: str
    data: Dict[str, Any] = {}

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "event": "send_message",
            "data": {
                "content_type": "text",
                "content": "Hello!",
            },
        }
    })