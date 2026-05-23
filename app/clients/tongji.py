from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from urllib.parse import urljoin

import httpx

logger = logging.getLogger("jinitaimei.tongji")

ONE_BASE_URL = "https://1.tongji.edu.cn"
WORKBENCH_URL = f"{ONE_BASE_URL}/workbench"
TEACHING_NOTICE_LIST_URL = (
    f"{ONE_BASE_URL}/api/commonservice/commonMsgPublish/findMyCommonMsgPublish"
)
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)


class TongjiLoginError(RuntimeError):
    """一系统登录失败。"""


@dataclass(frozen=True)
class TongjiSession:
    cookie_header: str
    x_token: str


@dataclass(frozen=True)
class TeachingNoticeSummary:
    id: int
    title: str
    publish_time: datetime | None
    publish_time_text: str
    top_status: str | None


async def login_tongji(username: str, password: str) -> TongjiSession:
    """用用户显式授权保存的账号密码登录一系统。

    只处理普通用户名密码链路；遇到 MFA、验证码或页面结构变化时直接失败，
    避免绕过学校风控。
    """
    async with httpx.AsyncClient(
        headers=_default_headers(),
        follow_redirects=True,
        timeout=25,
    ) as client:
        response = await client.get(WORKBENCH_URL)
        if _session_ready(client):
            return _build_session(client)

        for _ in range(4):
            current_url = str(response.url)
            text = response.text
            if _looks_like_mfa_or_captcha(current_url, text):
                raise TongjiLoginError("一系统需要验证码或二次验证，请在 App 内重新确认登录")

            form = _find_login_form(text, current_url)
            if form is None:
                if "iam.tongji.edu.cn" not in current_url and "1.tongji.edu.cn" not in current_url:
                    raise TongjiLoginError("一系统登录跳转到了未知页面")
                response = await client.get(current_url)
                continue

            response = await client.post(
                form.action,
                data=form.with_credentials(username, password),
                headers={
                    **_default_headers(),
                    "Origin": _origin(form.action),
                    "Referer": current_url,
                },
            )
            if _session_ready(client):
                return _build_session(client)

        if _looks_like_mfa_or_captcha(str(response.url), response.text):
            raise TongjiLoginError("一系统需要验证码或二次验证，请在 App 内重新确认登录")
        raise TongjiLoginError("一系统登录未拿到 sessionid")


async def fetch_teaching_notices(
    session: TongjiSession,
    pages: int = 3,
) -> list[TeachingNoticeSummary]:
    async with httpx.AsyncClient(headers=_default_headers(), timeout=25) as client:
        result: list[TeachingNoticeSummary] = []
        for page in range(1, pages + 1):
            response = await client.post(
                TEACHING_NOTICE_LIST_URL,
                json={"total": 0, "pageNum_": page, "pageSize_": 10},
                headers={
                    **_default_headers(),
                    "Accept": "application/json, text/plain, */*",
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": ONE_BASE_URL,
                    "Referer": WORKBENCH_URL,
                    "Cookie": session.cookie_header,
                    "X-Token": session.x_token,
                },
            )
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("code", -1)) != 200:
                raise RuntimeError(f"教务通知列表返回异常 code={payload.get('code')}")
            items = ((payload.get("data") or {}).get("list")) or []
            if not items:
                break
            result.extend(_parse_notice(item) for item in items if item.get("id"))
        return result


def latest_notice(notices: list[TeachingNoticeSummary]) -> TeachingNoticeSummary | None:
    valid = [notice for notice in notices if notice.publish_time is not None]
    if not valid:
        return notices[0] if notices else None
    return max(
        valid,
        key=lambda item: (item.publish_time or datetime.min.replace(tzinfo=UTC), item.id),
    )


@dataclass(frozen=True)
class _LoginForm:
    action: str
    fields: dict[str, str]
    username_field: str
    password_field: str

    def with_credentials(self, username: str, password: str) -> dict[str, str]:
        fields = dict(self.fields)
        fields[self.username_field] = username
        fields[self.password_field] = password
        return fields


def _default_headers() -> dict[str, str]:
    return {
        "User-Agent": IPHONE_USER_AGENT,
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


def _session_ready(client: httpx.AsyncClient) -> bool:
    return bool(client.cookies.get("sessionid"))


def _build_session(client: httpx.AsyncClient) -> TongjiSession:
    sessionid = client.cookies.get("sessionid")
    if not sessionid:
        raise TongjiLoginError("一系统 sessionid 为空")
    cookie_header = _cookie_header(client)
    return TongjiSession(cookie_header=cookie_header, x_token=sessionid)


def _cookie_header(client: httpx.AsyncClient) -> str:
    cookie = SimpleCookie()
    for item in client.cookies.jar:
        cookie[item.name] = item.value
    return "; ".join(f"{morsel.key}={morsel.value}" for morsel in cookie.values())


def _looks_like_mfa_or_captcha(url: str, text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered or marker in url.lower()
        for marker in (
            "captcha",
            "验证码",
            "动态码",
            "otp",
            "mfa",
            "二次验证",
            "ActionAuthChain".lower(),
        )
    ) and not _has_password_input(text)


def _has_password_input(text: str) -> bool:
    return "type=\"password\"" in text.lower() or "type='password'" in text.lower()


def _find_login_form(text: str, base_url: str) -> _LoginForm | None:
    forms = re.findall(r"<form\b[^>]*>.*?</form>", text, flags=re.IGNORECASE | re.DOTALL)
    for form_html in forms:
        password_input = _find_input(form_html, input_type="password")
        if not password_input:
            continue
        username_input = _find_username_input(form_html)
        if not username_input:
            continue
        action = _form_action(form_html, base_url)
        fields = _hidden_and_text_fields(form_html)
        return _LoginForm(
            action=action,
            fields=fields,
            username_field=username_input,
            password_field=password_input,
        )
    return None


def _form_action(form_html: str, base_url: str) -> str:
    match = re.search(r"\baction=[\"']([^\"']+)[\"']", form_html, flags=re.IGNORECASE)
    if not match:
        return base_url
    return urljoin(base_url, html.unescape(match.group(1)))


def _find_input(form_html: str, input_type: str) -> str | None:
    for input_html in re.findall(r"<input\b[^>]*>", form_html, flags=re.IGNORECASE):
        attrs = _attrs(input_html)
        if attrs.get("type", "").lower() == input_type:
            return attrs.get("name") or attrs.get("id")
    return None


def _find_username_input(form_html: str) -> str | None:
    preferred = ("username", "user", "j_username", "loginName", "account", "uid")
    candidates: list[str] = []
    for input_html in re.findall(r"<input\b[^>]*>", form_html, flags=re.IGNORECASE):
        attrs = _attrs(input_html)
        input_type = attrs.get("type", "text").lower()
        name = attrs.get("name") or attrs.get("id")
        if name and input_type in {"text", "email", "tel", ""}:
            candidates.append(name)
    for key in preferred:
        for candidate in candidates:
            if key.lower() in candidate.lower():
                return candidate
    return candidates[0] if candidates else None


def _hidden_and_text_fields(form_html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for input_html in re.findall(r"<input\b[^>]*>", form_html, flags=re.IGNORECASE):
        attrs = _attrs(input_html)
        name = attrs.get("name")
        if not name:
            continue
        fields[name] = attrs.get("value", "")
    return fields


def _attrs(tag_html: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for key, _, value in re.findall(r"([\w:-]+)\s*=\s*([\"'])(.*?)\2", tag_html, flags=re.DOTALL):
        attrs[key.lower()] = html.unescape(value)
    return attrs


def _origin(url: str) -> str:
    parsed = httpx.URL(url)
    return f"{parsed.scheme}://{parsed.host}"


def _parse_notice(item: dict) -> TeachingNoticeSummary:
    publish_time_text = str(item.get("publishTime") or item.get("createTime") or "")
    return TeachingNoticeSummary(
        id=int(item.get("id")),
        title=str(item.get("title") or "未命名通知"),
        publish_time=_parse_school_datetime(publish_time_text),
        publish_time_text=publish_time_text[:16],
        top_status=str(item.get("topStatus")) if item.get("topStatus") is not None else None,
    )


def _parse_school_datetime(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    logger.warning("无法解析学校时间：%s", normalized)
    return None
