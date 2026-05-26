from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.clients.tongji import (
    _ajax_login_failed,
    _ajax_login_succeeded,
    _ajax_login_url,
    _ajax_requires_interaction,
    _default_headers,
    _encrypt_password,
    _find_login_form,
    _json_payload,
    _looks_like_mfa_or_captcha,
    _origin,
    _post_login_ajax,
    _serialize_ajax_credentials,
)

logger = logging.getLogger("jinitaimei.yikatong")

YIKATONG_BASE_URL = "https://pay-yikatong.tongji.edu.cn"
YIKATONG_FRONT_INFO_URL = f"{YIKATONG_BASE_URL}/berserker-app/frontInfo?synAccessSource=h5"
YIKATONG_LOGIN_ENTRY_URL = (
    f"{YIKATONG_BASE_URL}/berserker-auth/cas/redirect/bamboocloud"
    "?targetUrl=https://pay-yikatong.tongji.edu.cn/plat/?name=loginTransit"
)
YIKATONG_WALLET_URL = f"{YIKATONG_BASE_URL}/plat/wode"
YIKATONG_TOKEN_URL = f"{YIKATONG_BASE_URL}/berserker-auth/oauth/token"
YIKATONG_BALANCE_API = (
    f"{YIKATONG_BASE_URL}/berserker-app/ykt/tsm/queryCard?synAccessSource=h5"
)
YIKATONG_MOBILE_BASIC_AUTH = (
    "Basic bW9iaWxlX3NlcnZpY2VfcGxhdGZvcm06bW9iaWxlX3NlcnZpY2VfcGxhdGZvcm1fc2VjcmV0"
)


class YikatongLoginError(RuntimeError):
    """校园卡登录失败。"""


@dataclass(frozen=True)
class YikatongSession:
    cookie_header: str
    bearer_token: str


@dataclass(frozen=True)
class CampusCardBalanceSummary:
    balance_yuan: float
    account: str
    owner_name: str
    card_identifier: str
    captured_at: datetime


async def login_yikatong(username: str, password: str) -> YikatongSession:
    """使用统一身份账号密码登录校园卡服务并提取访问令牌。"""
    async with httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        timeout=25,
    ) as client:
        response = await client.get(await _resolve_sso_entry_url())
        session = await _build_yikatong_session(client, response)
        if session is not None:
            return session
        session = await _probe_yikatong_session(client)
        if session is not None:
            return session

        for _ in range(5):
            current_url = str(response.url)
            text = response.text
            if _looks_like_mfa_or_captcha(current_url, text):
                raise YikatongLoginError("校园卡登录需要验证码或二次验证，请在 App 内重新确认登录")

            form = _find_login_form(text, current_url)
            if form is None:
                response = await _continue_without_form(client, current_url)
                session = await _build_yikatong_session(client, response)
                if session is not None:
                    return session
                session = await _probe_yikatong_session(client)
                if session is not None:
                    return session
                continue

            credentials = form.with_credentials(username, password)
            encrypted_credentials = dict(credentials)
            encrypted_credentials[form.password_field] = _encrypt_password(password)
            serialized_credentials = _serialize_ajax_credentials(form, credentials, password)

            response = await _post_login_ajax(
                client=client,
                ajax_url=_ajax_login_url(current_url, credentials),
                serialized_credentials=serialized_credentials,
                referer=current_url,
            )
            session = await _build_yikatong_session(client, response)
            if session is not None:
                return session

            if _looks_like_mfa_or_captcha(str(response.url), response.text):
                raise YikatongLoginError("校园卡登录需要验证码或二次验证，请在 App 内重新确认登录")

            ajax_payload = _json_payload(response)
            if ajax_payload is None:
                continue

            if _ajax_requires_interaction(ajax_payload):
                raise YikatongLoginError("校园卡登录需要验证码或二次验证，请在 App 内重新确认登录")

            if _ajax_login_succeeded(ajax_payload):
                response = await client.post(
                    form.action,
                    data=encrypted_credentials,
                    headers={
                        **_default_headers(),
                        "Accept": (
                            "text/html,application/xhtml+xml,application/xml;q=0.9,"
                            "image/avif,image/webp,image/apng,*/*;q=0.8"
                        ),
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Origin": _origin(form.action),
                        "Referer": current_url,
                    },
                )
                session = await _build_yikatong_session(client, response)
                if session is not None:
                    return session
                session = await _probe_yikatong_session(client)
                if session is not None:
                    return session
            elif _ajax_login_failed(ajax_payload):
                raise YikatongLoginError(
                    "校园卡用户名或密码错误，或已失效，请在 App 内重新确认邮件推送凭据"
                )

        raise YikatongLoginError("校园卡登录未拿到 synjones-auth 令牌")


async def fetch_campus_card_balance(session: YikatongSession) -> CampusCardBalanceSummary:
    async with httpx.AsyncClient(headers=_default_headers(), timeout=25) as client:
        headers = {
            **_default_headers(),
            "Accept": "application/json, text/plain, */*",
            "synjones-auth": f"bearer {session.bearer_token}",
            "synaccesssource": "h5",
            "Referer": YIKATONG_WALLET_URL,
        }
        if session.cookie_header:
            headers["Cookie"] = session.cookie_header
        response = await client.get(YIKATONG_BALANCE_API, headers=headers)
        if response.status_code in {401, 403}:
            raise YikatongLoginError("校园卡凭证已失效，请在 App 内重新确认邮件推送凭据")
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise RuntimeError(payload.get("msg") or "校园卡余额接口返回异常")
        cards = (((payload.get("data") or {}).get("card")) or [])
        if not cards:
            raise RuntimeError("校园卡余额接口未返回卡片信息")
        card = cards[0]
        balance_cents = int(card.get("elec_accamt") or 0)
        return CampusCardBalanceSummary(
            balance_yuan=balance_cents / 100.0,
            account=str(card.get("account") or ""),
            owner_name=str(card.get("name") or ""),
            card_identifier=str(card.get("card_name_en") or ""),
            captured_at=datetime.now(UTC),
        )


async def _build_yikatong_session(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> YikatongSession | None:
    token = _extract_token_from_response(response)
    if not token:
        ticket = _extract_ticket_from_response(response)
        if ticket:
            token = await _exchange_ticket_for_token(client, ticket, _extract_target_url(response))
    cookie_header = _yikatong_cookie_header(client)
    if token:
        logger.info(
            "yikatong session ready token_len=%s cookie_names=%s",
            len(token),
            _cookie_names_for_log(cookie_header),
        )
        return YikatongSession(cookie_header=cookie_header, bearer_token=token)
    return None


def _extract_token_from_response(response: httpx.Response) -> str | None:
    for candidate in _response_urls(response):
        token = _extract_token_from_url(candidate)
        if token:
            return token
    match = re.search(r"synjones-auth=([^&\"'\\s]+)", response.text)
    if match:
        raw = httpx.QueryParams(f"synjones-auth={match.group(1)}").get("synjones-auth") or ""
        token = raw.removeprefix("bearer ").strip()
        return token or None
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        token = str(payload.get("access_token") or "").strip()
        token = token.removeprefix("bearer ").strip()
        return token if len(token) >= 32 else None
    return None


def _extract_ticket_from_response(response: httpx.Response) -> str | None:
    for candidate in _response_urls(response):
        parsed = urlparse(candidate)
        raw = httpx.QueryParams(parsed.query).get("ticket")
        if raw:
            return raw
    return None


def _extract_target_url(response: httpx.Response) -> str | None:
    for candidate in reversed(_response_urls(response)):
        parsed = urlparse(candidate)
        raw = httpx.QueryParams(parsed.query).get("targetUrl")
        if raw:
            return raw
    return None


async def _exchange_ticket_for_token(
    client: httpx.AsyncClient,
    ticket: str,
    target_url: str | None = None,
) -> str | None:
    payload = {
        "username": ticket,
        "password": ticket,
        "grant_type": "password",
        "scope": "all",
        "loginFrom": "app",
        "logintype": "sso",
        "device_token": "h5",
    }
    logger.info("yikatong exchanging loginTransit ticket for token ticket_len=%s", len(ticket))
    response = await client.post(
        YIKATONG_TOKEN_URL,
        content=urlencode(payload),
        headers={
            **_default_headers(),
            "Accept": "application/json, text/plain, */*",
            "Authorization": YIKATONG_MOBILE_BASIC_AUTH,
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": YIKATONG_BASE_URL,
            "Referer": target_url or YIKATONG_WALLET_URL,
        },
    )
    if response.status_code in {401, 403}:
        raise YikatongLoginError("校园卡 ticket 换取 token 被拒绝，请重新确认邮件推送凭据")
    response.raise_for_status()
    token = _extract_token_from_response(response)
    if token:
        logger.info(
            "yikatong exchanged ticket token_len=%s cookie_names=%s",
            len(token),
            _cookie_names_for_log(_yikatong_cookie_header(client)),
        )
    return token


def _response_urls(response: httpx.Response) -> list[str]:
    urls = [str(item.url) for item in response.history]
    urls.append(str(response.url))
    return urls


def _extract_token_from_url(url: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    raw = (query.get("synjones-auth") or [None])[0]
    if not raw:
        return None
    token = raw.removeprefix("bearer ").strip()
    return token or None


def _yikatong_cookie_header(client: httpx.AsyncClient) -> str:
    cookie = SimpleCookie()
    for item in client.cookies.jar:
        host = (item.domain or "").lower()
        if "pay-yikatong.tongji.edu.cn" not in host:
            continue
        cookie[item.name] = item.value
    return "; ".join(f"{morsel.key}={morsel.value}" for morsel in cookie.values())


def _cookie_names_for_log(cookie_header: str) -> str:
    if not cookie_header:
        return ""
    names = []
    for item in cookie_header.split(";"):
        name = item.strip().split("=", 1)[0]
        if name:
            names.append(name)
    return ",".join(sorted(set(names)))


async def _resolve_sso_entry_url() -> str:
    async with httpx.AsyncClient(headers=_default_headers(), timeout=20) as client:
        response = await client.get(YIKATONG_FRONT_INFO_URL)
        response.raise_for_status()
        payload = response.json()
        raw_front_config = ((payload.get("data") or {}).get("getFrontConfig")) or ""
        if not raw_front_config:
            return YIKATONG_LOGIN_ENTRY_URL
        try:
            front_config = json.loads(raw_front_config)
            login_types = json.loads(front_config.get("loginType") or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return YIKATONG_LOGIN_ENTRY_URL

        for item in login_types:
            if str(item.get("key") or "").lower() == "sso":
                url = str(item.get("url") or "").strip()
                if url:
                    return url
        return YIKATONG_LOGIN_ENTRY_URL


async def _continue_without_form(
    client: httpx.AsyncClient,
    current_url: str,
) -> httpx.Response:
    """登录页没有可见表单时，尝试继续推进校园卡回跳链路。

    校园卡站点会在 IAM 成功后再回跳一次 `bamboocloud -> loginTransit -> /plat/...`，
    单纯反复 GET 当前 IAM 页容易原地打转，因此这里优先重新触发校园卡侧入口。
    """
    if "iam.tongji.edu.cn" in current_url or "ids.tongji.edu.cn" in current_url:
        return await client.get(YIKATONG_LOGIN_ENTRY_URL)
    return await client.get(YIKATONG_WALLET_URL)


async def _probe_yikatong_session(client: httpx.AsyncClient) -> YikatongSession | None:
    """在可能已具备 IAM 登录态时，主动探测校园卡回跳结果并提取 token。"""
    probe_urls = (
        YIKATONG_LOGIN_ENTRY_URL,
        "https://pay-yikatong.tongji.edu.cn/plat/?name=loginTransit",
        YIKATONG_WALLET_URL,
    )
    for url in probe_urls:
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            continue
        session = await _build_yikatong_session(client, response)
        if session is not None:
            return session
    return None
