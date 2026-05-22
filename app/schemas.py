from pydantic import BaseModel, EmailStr, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class SubscriptionRequest(BaseModel):
    email: EmailStr
    teaching_notice_enabled: bool = True
    star_new_activity_enabled: bool = True
    star_registration_enabled: bool = True
    selected_star_module_codes: list[str] = Field(default_factory=list)


class SubscriptionResponse(BaseModel):
    email: EmailStr
    saved: bool


class TestEmailRequest(BaseModel):
    email: EmailStr


class TestEmailResponse(BaseModel):
    sent: bool
