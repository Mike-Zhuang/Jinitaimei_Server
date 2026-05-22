from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, status

from app.config import get_settings
from app.database import init_database
from app.mailer import send_email
from app.repository import upsert_subscription
from app.schemas import (
    HealthResponse,
    SubscriptionRequest,
    SubscriptionResponse,
    TestEmailRequest,
    TestEmailResponse,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_database()
    yield


app = FastAPI(
    title="济你太美邮件推送服务",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok", service="jinitaimei-push-server")


@app.post("/api/v1/subscriptions", response_model=SubscriptionResponse)
async def save_subscription(payload: SubscriptionRequest) -> SubscriptionResponse:
    await upsert_subscription(payload)
    return SubscriptionResponse(email=payload.email, saved=True)


@app.post("/api/v1/test-email", response_model=TestEmailResponse)
async def send_test_email(
    payload: TestEmailRequest,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> TestEmailResponse:
    settings = get_settings()
    if not settings.admin_token or x_admin_token != settings.admin_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin token",
        )
    await send_email(
        to_address=str(payload.email),
        subject="济你太美通知测试",
        text="如果你收到这封邮件，说明济你太美邮件推送服务已经可以正常发送邮件。",
    )
    return TestEmailResponse(sent=True)
