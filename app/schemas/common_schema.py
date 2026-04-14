from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, Generic, TypeVar, List
from datetime import datetime
from uuid import UUID

DataT = TypeVar('DataT')


class ResponseBase(BaseModel):
    """Base response model"""
    success: bool
    message: Optional[str] = None


class SuccessResponse(ResponseBase, Generic[DataT]):
    """Success response with data"""
    success: bool = True
    data: DataT


class ErrorDetail(BaseModel):
    """Error detail model"""
    message: str
    type: str
    details: Optional[dict] = None


class ErrorResponse(ResponseBase):
    """Error response model"""
    success: bool = False
    error: ErrorDetail


class PaginationParams(BaseModel):
    """Pagination parameters"""
    page: int = Field(1, ge=1, description="Page number")
    page_size: int = Field(20, ge=1, le=100, description="Items per page")


class PaginatedResponse(BaseModel, Generic[DataT]):
    """Paginated response"""
    success: bool = True
    data: List[DataT]
    pagination: dict = Field(
        ...,
        description="Pagination metadata"
    )


# ============================================
# LOCATION SCHEMAS (Blueprint v2.0)
# ============================================

class LocationSchema(BaseModel):
    """
    Geographic coordinates.
    
    Used for business registration, user position, etc.
    """
    latitude: float = Field(..., ge=-90, le=90, description="Latitude coordinate")
    longitude: float = Field(..., ge=-180, le=180, description="Longitude coordinate")

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "latitude": 9.0765,
            "longitude": 7.3986
        }
    })


class RadiusSearchRequest(BaseModel):
    """
    Request schema for radius-based discovery queries.
    
    Per Blueprint: "Default radius 5 km. Adjustable by user from 1-50 km."
    LGA is NOT included — filtering is purely coordinate + radius based.
    """
    latitude: float = Field(..., ge=-90, le=90, description="User's latitude")
    longitude: float = Field(..., ge=-180, le=180, description="User's longitude")
    radius_km: float = Field(
        default=5.0,
        ge=1.0,
        le=50.0,
        description="Search radius in kilometers (1-50 km)"
    )
    category: Optional[str] = Field(None, description="Filter by business category")
    search_query: Optional[str] = Field(None, description="Text search query")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)

    @property
    def radius_meters(self) -> float:
        """Convert radius_km to meters for PostGIS ST_DWithin."""
        return self.radius_km * 1000.0

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "latitude": 6.5244,
            "longitude": 3.3792,
            "radius_km": 5.0,
            "category": "food",
            "page": 1,
            "page_size": 20
        }
    })


class DistanceDisplay(BaseModel):
    """
    Distance information for listing cards.
    
    Per Blueprint: "Every listing card shows distance: '1.2 km away' or 'Within 500 m'"
    """
    distance_km: float = Field(..., description="Distance in kilometers")
    distance_text: str = Field(..., description="Human-readable distance")

    @classmethod
    def from_km(cls, distance_km: float) -> "DistanceDisplay":
        """Create from kilometer distance with formatted text."""
        if distance_km < 1.0:
            meters = int(distance_km * 1000)
            text = f"Within {meters} m"
        else:
            text = f"{distance_km:.1f} km away"
        
        return cls(distance_km=distance_km, distance_text=text)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "distance_km": 1.2,
            "distance_text": "1.2 km away"
        }
    })


class AddressSchema(BaseModel):
    """
    Nigerian address structure.
    
    NOTE: State/LGA are stored for display purposes only.
    They are NEVER used for filtering queries (Blueprint: no LGA dependency).
    """
    street: Optional[str] = None
    area: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    local_government: Optional[str] = None
    country: str = "Nigeria"
    landmark: Optional[str] = None
    coordinates: Optional[LocationSchema] = None

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "street": "123 Allen Avenue",
            "area": "Ikeja",
            "city": "Lagos",
            "state": "Lagos",
            "local_government": "Ikeja",
            "country": "Nigeria",
            "coordinates": {
                "latitude": 6.5244,
                "longitude": 3.3792
            }
        }
    })