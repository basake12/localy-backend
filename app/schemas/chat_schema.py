from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from uuid import UUID


# ============================================
# CONVERSATION SCHEMAS
# ============================================

class ConversationCreateRequest(BaseModel):
    """Start a new 1:1 or business conversation"""
    other_user_id: UUID
    context_type: Optional[str] = None
    context_id: Optional[UUID] = None
    initial_message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "other_user_id": "uuid-of-business-user",
            "context_type": "food_order",
            "context_id": "uuid-of-order",
            "initial_message": "Hi, I have a question about my order"
        }
    })


class SupportChatRequest(BaseModel):
    """
    Open or retrieve the platform support conversation.
    Blueprint §9.3 — separate channel from business chat.
    """
    initial_message: Optional[str] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {"initial_message": "I need help with a refund"}
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
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# MESSAGE SCHEMAS
# ============================================

class MessageCreateRequest(BaseModel):
    """Send a message"""
    conversation_id: UUID
    message_type: str = "text"
    content: Optional[str] = None
    media: Optional[Dict[str, Any]] = None
    reply_to_message_id: Optional[UUID] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "conversation_id": "uuid",
            "message_type": "text",
            "content": "Hey, where is my order?"
        }
    })


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_id: UUID
    sender_name: Optional[str] = None
    message_type: str
    content: Optional[str]
    media: Optional[Dict[str, Any]]
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
    content: str = Field(..., min_length=1)


class ReactionRequest(BaseModel):
    emoji: str = Field(..., min_length=1, max_length=4)


# ============================================
# PRESENCE SCHEMAS
# ============================================

class PresenceUpdateRequest(BaseModel):
    status: str = "online"   # online | away | busy | offline
    device_type: Optional[str] = None


class PresenceResponse(BaseModel):
    user_id: UUID
    is_online: bool
    status: str
    last_seen_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


# ============================================
# TYPING INDICATOR SCHEMA
# ============================================

class TypingStartRequest(BaseModel):
    conversation_id: UUID


# ============================================
# WEBSOCKET EVENT PAYLOADS
# ============================================

class WSEventPayload(BaseModel):
    """Generic envelope for all WebSocket frames"""
    event: str
    data: Dict[str, Any] = {}

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "event": "new_message",
            "data": {
                "message_id": "uuid",
                "conversation_id": "uuid",
                "content": "Hello!",
                "sender_id": "uuid"
            }
        }
    })