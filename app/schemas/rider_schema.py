from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal

from app.models.rider_model import DriverSubscriptionPlan


class RiderBase(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name: str = Field(..., min_length=2, max_length=100)
    vehicle_type: str = Field(..., description="bike, car, van")
    vehicle_plate_number: Optional[str] = Field(None, max_length=20)
    vehicle_model: Optional[str] = Field(None, max_length=100)
    vehicle_color: Optional[str] = Field(None, max_length=50)


class RiderCreate(RiderBase):
    phone: Optional[str] = Field(None, max_length=20)
    nin: Optional[str] = Field(None, max_length=20)


class RiderUpdate(BaseModel):
    first_name: Optional[str] = Field(None, min_length=2, max_length=100)
    last_name: Optional[str] = Field(None, min_length=2, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    vehicle_plate_number: Optional[str] = Field(None, max_length=20)
    vehicle_model: Optional[str] = Field(None, max_length=100)
    vehicle_color: Optional[str] = Field(None, max_length=50)
    profile_picture: Optional[str] = None
    drivers_license: Optional[str] = None
    vehicle_registration: Optional[str] = None
    service_radius_km: Optional[Decimal] = Field(None, gt=0, le=100)
    fcm_token: Optional[str] = None


class RiderOut(RiderBase):
    id: UUID
    user_id: UUID
    phone: Optional[str] = None
    profile_picture: Optional[str] = None
    subscription_plan: DriverSubscriptionPlan
    is_pro: bool
    pro_subscription_end: Optional[datetime] = None
    service_radius_km: Decimal
    average_rating: Decimal
    total_deliveries: int
    completed_deliveries: int
    completion_rate: Decimal
    is_online: bool
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RiderLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class RiderOnlineStatusUpdate(BaseModel):
    is_online: bool


class RiderStatsOut(BaseModel):
    total_deliveries: int
    completed_deliveries: int
    cancelled_deliveries: int
    total_earnings: Decimal
    average_rating: Decimal
    completion_rate: Decimal

    model_config = {"from_attributes": True}