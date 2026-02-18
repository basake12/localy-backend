from pydantic import BaseModel, Field, ConfigDict
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


class LocationSchema(BaseModel):
    """Geographic location schema"""
    latitude: float = Field(..., ge=-90, le=90)
    longitude: float = Field(..., ge=-180, le=180)

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "latitude": 9.0765,
            "longitude": 7.3986
        }
    })