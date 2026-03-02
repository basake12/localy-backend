from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import Optional, List, Dict, Any
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from app.schemas.common_schema import LocationSchema


# ============================================
# DELIVERY SCHEMAS
# ============================================

class DeliveryCreateRequest(BaseModel):
    """Create delivery request"""
    order_id: Optional[UUID] = None
    order_type: str

    # Pickup
    pickup_address: str = Field(..., min_length=10)
    pickup_location: LocationSchema
    pickup_contact_name: str = Field(..., min_length=2)
    pickup_contact_phone: str = Field(..., min_length=10)
    pickup_instructions: Optional[str] = None

    # Dropoff
    dropoff_address: str = Field(..., min_length=10)
    dropoff_location: LocationSchema
    dropoff_contact_name: str = Field(..., min_length=2)
    dropoff_contact_phone: str = Field(..., min_length=10)
    dropoff_instructions: Optional[str] = None

    # Package
    package_description: Optional[str] = None
    package_weight_kg: Optional[Decimal] = Field(None, gt=0)
    package_value: Optional[Decimal] = Field(None, ge=0)

    # Requirements
    requires_cold_storage: bool = False
    is_fragile: bool = False
    required_vehicle_type: Optional[str] = None

    # Payment
    payment_method: str = "wallet"
    cod_amount: Decimal = Field(default=0.00, ge=0)

    @field_validator('order_type')
    @classmethod
    def validate_order_type(cls, v):
        valid_types = ["product", "food", "parcel", "document", "prescription"]
        if v not in valid_types:
            raise ValueError(f'Invalid order type. Must be one of: {valid_types}')
        return v

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "order_type": "product",
            "pickup_address": "TechHub Store, Wuse 2, Abuja",
            "pickup_location": {"latitude": 9.0765, "longitude": 7.3986},
            "pickup_contact_name": "Store Manager",
            "pickup_contact_phone": "+2348012345678",
            "dropoff_address": "123 Main St, Garki, Abuja",
            "dropoff_location": {"latitude": 9.0574, "longitude": 7.4898},
            "dropoff_contact_name": "John Doe",
            "dropoff_contact_phone": "+2348087654321",
            "package_description": "2 boxes of electronics",
            "package_weight_kg": 5.5,
            "is_fragile": True,
            "payment_method": "wallet"
        }
    })


class DeliveryResponse(BaseModel):
    """Delivery response"""
    id: UUID
    order_id: Optional[UUID]
    order_type: str
    customer_id: UUID

    pickup_address: str
    pickup_contact_name: str
    pickup_contact_phone: str

    dropoff_address: str
    dropoff_contact_name: str
    dropoff_contact_phone: str

    package_description: Optional[str]
    package_weight_kg: Optional[Decimal]

    base_fee: Decimal
    distance_fee: Decimal
    total_fee: Decimal

    estimated_distance_km: Optional[Decimal]
    estimated_pickup_time: Optional[datetime]
    estimated_delivery_time: Optional[datetime]

    status: str
    payment_status: str
    tracking_code: str

    rider_id: Optional[UUID]

    rating: Optional[int]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliveryListResponse(BaseModel):
    """Simplified delivery list"""
    id: UUID
    tracking_code: str
    order_type: str
    dropoff_address: str
    total_fee: Decimal
    status: str
    created_at: datetime


class DeliveryTrackingResponse(BaseModel):
    """Tracking update response"""
    id: UUID
    delivery_id: UUID
    status: str
    location: Optional[Dict[str, float]]
    address: Optional[str]
    notes: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DeliveryDetailsResponse(BaseModel):
    """Full delivery details with tracking"""
    delivery: DeliveryResponse
    tracking_updates: List[DeliveryTrackingResponse]
    rider_info: Optional[Dict[str, Any]] = None


# ============================================
# RIDER SCHEMAS
# ============================================

class RiderLocationUpdate(BaseModel):
    """Rider location update"""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class RiderAvailabilityUpdate(BaseModel):
    """Update rider availability"""
    is_online: bool
    current_location: Optional[LocationSchema] = None


class RiderStatsResponse(BaseModel):
    """Rider statistics"""
    total_deliveries: int
    completed_deliveries: int
    active_deliveries: int
    average_rating: Decimal
    total_distance_km: Decimal
    total_earnings: Decimal
    completion_rate: Decimal


class RiderEarningsResponse(BaseModel):
    """Rider earnings response"""
    id: UUID
    delivery_id: UUID
    base_earning: Decimal
    distance_bonus: Decimal
    tip: Decimal
    peak_hour_bonus: Decimal
    total_earning: Decimal
    platform_commission: Decimal
    net_earning: Decimal
    is_paid: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# ADMIN SCHEMAS
# ============================================

class AssignRiderRequest(BaseModel):
    """Admin assign rider to delivery"""
    rider_id: UUID
    delivery_id: UUID


class DeliveryZoneCreateRequest(BaseModel):
    """Create delivery zone"""
    name: str = Field(..., min_length=3)
    state: str
    local_government: str
    center_location: LocationSchema
    radius_km: Decimal = Field(..., gt=0)
    base_fee: Decimal = Field(..., gt=0)
    per_km_fee: Decimal = Field(..., ge=0)
    peak_hours: List[Dict[str, Any]] = Field(default_factory=list)


class DeliveryZoneResponse(BaseModel):
    """Delivery zone response"""
    id: UUID
    name: str
    state: str
    local_government: str
    radius_km: Decimal
    base_fee: Decimal
    per_km_fee: Decimal
    peak_hours: List[Dict[str, Any]]
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ============================================
# SEARCH FILTERS
# ============================================

class DeliverySearchFilters(BaseModel):
    """Delivery search filters"""
    status: Optional[str] = None
    order_type: Optional[str] = None
    date_from: Optional[datetime] = None
    date_to: Optional[datetime] = None
    rider_id: Optional[UUID] = None


class RiderSearchFilters(BaseModel):
    """Rider search filters"""
    is_online: Optional[bool] = None
    location: Optional[LocationSchema] = None
    radius_km: Optional[float] = Field(None, gt=0)
    vehicle_type: Optional[str] = None
    min_rating: Optional[Decimal] = Field(None, ge=0, le=5)