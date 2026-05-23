from pydantic import BaseModel, EmailStr, Field


class HealthResponse(BaseModel):
    status: str
    service: str


class SubscriptionRequest(BaseModel):
    email: EmailStr
    mail_push_enabled: bool = False
    teaching_notice_enabled: bool = True
    star_new_activity_enabled: bool = True
    star_registration_enabled: bool = True
    selected_star_module_codes: list[str] = Field(default_factory=list)
    followed_star_activity_ids: list[int] = Field(default_factory=list)


class SubscriptionResponse(BaseModel):
    email: EmailStr
    saved: bool


class CredentialRequest(BaseModel):
    email: EmailStr
    tongji_username: str = Field(min_length=1, max_length=64)
    tongji_password: str = Field(min_length=1, max_length=256)


class CredentialResponse(BaseModel):
    email: EmailStr
    saved: bool


class DeleteSubscriptionRequest(BaseModel):
    email: EmailStr


class DeleteSubscriptionResponse(BaseModel):
    email: EmailStr
    deleted: bool


class TestEmailRequest(BaseModel):
    email: EmailStr


class TestEmailResponse(BaseModel):
    sent: bool
