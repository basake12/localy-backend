
from pydantic import BaseModel, Field
from typing import Optional
from uuid import UUID
from datetime import datetime
from decimal import Decimal


class RiderBase(BaseModel):
    first_name: str = Field(..., min_length=2, max_length=100)
    last_name: str = Field(..., min_length=2, max_length=100)
    vehicle_type: str = Field(..., description="bike, car, van, truck")
    vehicle_plate_number: Optional[str] = None
    vehicle_model: Optional[str] = None


class RiderCreate(RiderBase):
    pass


class RiderUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    vehicle_plate_number: Optional[str] = None
    vehicle_model: Optional[str] = None
    profile_picture: Optional[str] = None
    drivers_license: Optional[str] = None
    vehicle_registration: Optional[str] = None
    service_radius_km: Optional[Decimal] = None


class RiderOut(RiderBase):
    id: UUID
    user_id: UUID
    profile_picture: Optional[str] = None
    average_rating: Decimal
    total_deliveries: int
    completion_rate: Decimal
    is_online: bool
    is_verified: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RiderLocationUpdate(BaseModel):
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)


class RiderStatsOut(BaseModel):
    total_deliveries: int
    completed_deliveries: int
    cancelled_deliveries: int
    total_earnings: Decimal
    average_rating: Decimal
    completion_rate: Decimal
