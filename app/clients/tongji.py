from __future__ import annotations

import html
import logging
import re
import urllib.parse
from dataclasses import dataclass
from datetime import UTC, datetime
from http.cookies import SimpleCookie
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

logger = logging.getLogger("jinitaimei.tongji")

ONE_BASE_URL = "https://1.tongji.edu.cn"
WORKBENCH_URL = f"{ONE_BASE_URL}/workbench"
LOGIN_ENTRY_URL = f"{ONE_BASE_URL}/api/ssoservice/system/loginIn"
TEACHING_NOTICE_LIST_URL = (
    f"{ONE_BASE_URL}/api/commonservice/commonMsgPublish/findMyCommonMsgPublish"
)
SESSION_LOGIN_URL = f"{ONE_BASE_URL}/api/sessionservice/session/login"
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)
IAM_PASSWORD_PUBLIC_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC9t16RqQWUE/J1IyOfoNHc4r/h
6RPnXcWTJ4IbhQVUsEqMMm65F0hiytAgozXmVw68yPJywbpblDrx9zl1wdRcdHCo
UvmPdr9/oCQtpQyVc7BXZIN6wJlD6MTeMeni+N0toNPxfXjiAawjNHGZZuT8wQpN
EMwsVyJ/lonXaVdGZwIDAQAB
-----END PUBLIC KEY-----"""


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
        response = await client.get(LOGIN_ENTRY_URL)
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

            credentials = form.with_credentials(username, password)
            serialized_credentials = _serialize_ajax_credentials(form, credentials, password)
            response = await _post_login_ajax(
                client=client,
                ajax_url=_ajax_login_url(current_url, credentials),
                serialized_credentials=serialized_credentials,
                referer=current_url,
            )
            if _session_ready(client):
                return _build_session(client)

            ajax_payload = _json_payload(response)
            if ajax_payload is not None:
                if _ajax_login_failed(ajax_payload):
                    raise TongjiLoginError(
                        "一系统用户名或密码错误，或已失效，请在 App 内重新确认邮件推送凭据"
                    )

                if _ajax_requires_interaction(ajax_payload):
                    raise TongjiLoginError("一系统需要验证码或二次验证，请在 App 内重新确认登录")

                if _ajax_login_succeeded(ajax_payload):
                    response = await client.post(
                        form.action,
                        data=credentials,
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
                    if _session_ready(client):
                        return _build_session(client)
                    ssologin_session = await _try_exchange_ssologin_session(client, response)
                    if ssologin_session is not None:
                        return ssologin_session
                continue

            if _looks_like_mfa_or_captcha(str(response.url), response.text):
                raise TongjiLoginError("一系统需要验证码或二次验证，请在 App 内重新确认登录")

            logger.warning("一系统 AJAX 响应无法解析为 JSON，继续跟随下一跳：%s", current_url)
            ssologin_session = await _try_exchange_ssologin_session(client, response)
            if ssologin_session is not None:
                return ssologin_session

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


async def _post_login_ajax(
    client: httpx.AsyncClient,
    ajax_url: str,
    serialized_credentials: str,
    referer: str,
) -> httpx.Response:
    return await client.post(
        ajax_url,
        content=serialized_credentials,
        headers={
            **_default_headers(),
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": _origin(ajax_url),
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
        },
    )


def _ajax_login_url(current_url: str, fields: dict[str, str]) -> str:
    authn_lc_key = fields.get("authnLcKey", "")
    if "authcenter/ActionAuthChain" in current_url and authn_lc_key:
        return re.sub(
            r"authnLcKey=[^&]+",
            f"authnLcKey={authn_lc_key}",
            current_url,
            count=1,
        )
    return current_url


def _encrypt_password(password: str) -> str:
    public_key = serialization.load_pem_public_key(IAM_PASSWORD_PUBLIC_KEY_PEM.encode("utf-8"))
    encrypted = public_key.encrypt(password.encode("utf-8"), padding.PKCS1v15())
    return _b64encode_ascii(encrypted)


def _b64encode_ascii(data: bytes) -> str:
    from base64 import b64encode

    return b64encode(data).decode("ascii")


def _json_payload(response: httpx.Response) -> dict | None:
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" not in content_type and not response.text.strip().startswith("{"):
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _ajax_requires_interaction(payload: dict) -> bool:
    view = str(payload.get("view") or "")
    if view.isdigit():
        return True
    return view in {
        "biometrics",
        "frexcept",
        "faceexcept",
        "voiceexcept",
        "gestureexcept",
        "face",
        "voice",
        "bindCertUid",
        "bindWechatUid",
        "bindEyekeyUid",
        "modify_password",
        "remind_password",
        "certificationView",
    }


def _ajax_login_succeeded(payload: dict) -> bool:
    login_failed_raw = payload.get("loginFailed")
    login_failed = str(login_failed_raw or "").lower()
    view = str(payload.get("view") or "").lower()
    return login_failed_raw in {None, ""} or login_failed == "false" or view == "none"


def _ajax_login_failed(payload: dict) -> bool:
    login_failed = str(payload.get("loginFailed") or "").lower()
    return login_failed == "true"


def _session_ready(client: httpx.AsyncClient) -> bool:
    return bool(client.cookies.get("sessionid"))


def _build_session(client: httpx.AsyncClient) -> TongjiSession:
    sessionid = client.cookies.get("sessionid")
    if not sessionid:
        raise TongjiLoginError("一系统 sessionid 为空")
    cookie_header = _cookie_header(client)
    return TongjiSession(cookie_header=cookie_header, x_token=sessionid)


async def _try_exchange_ssologin_session(
    client: httpx.AsyncClient,
    response: httpx.Response,
) -> TongjiSession | None:
    params = _extract_ssologin_params(response)
    if params is None:
        return None

    logger.info("命中 ssologin 回跳，开始补做 session/login 交换")
    exchange = await client.post(
        SESSION_LOGIN_URL,
        json=params,
        headers={
            **_default_headers(),
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": ONE_BASE_URL,
            "Referer": _ssologin_referer(params),
        },
    )
    exchange.raise_for_status()
    payload = _json_payload(exchange)
    if payload is None:
        raise TongjiLoginError("一系统 session/login 响应无法解析")

    code = int(payload.get("code", -1))
    if code == 313:
        raise TongjiLoginError("一系统账号已冻结，请在学校系统中处理后再开启邮件推送")
    if code != 200:
        message = str(payload.get("msg") or "").strip()
        raise TongjiLoginError(message or f"一系统 session/login 返回异常 code={code}")

    data = payload.get("data") or {}
    sessionid = str(data.get("sessionid") or "").strip()
    if not sessionid:
        raise TongjiLoginError("一系统 session/login 未返回 sessionid")

    cookie_header = _cookie_header(client)
    return TongjiSession(cookie_header=cookie_header, x_token=sessionid)


def _extract_ssologin_params(response: httpx.Response) -> dict[str, str] | None:
    chain = list(response.history) + [response]
    for item in reversed(chain):
        url = item.url
        if "/ssologin" not in str(url):
            continue
        uid = url.params.get("uid")
        token = url.params.get("token")
        ts = url.params.get("ts")
        if uid and token:
            return {"uid": uid, "token": token, "ts": ts or ""}
    return None


def _ssologin_referer(params: dict[str, str]) -> str:
    query = urllib.parse.urlencode(params)
    return f"{ONE_BASE_URL}/ssologin?{query}"


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
        _hydrate_auth_chain_code(text, fields)
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


def _hydrate_auth_chain_code(page_html: str, fields: dict[str, str]) -> None:
    if fields.get("spAuthChainCode"):
        return

    auth_method_id = _first_match(
        page_html,
        [
            r"var\s+authMethodIDs\s*=\s*'([^']+)'",
            r'id="authMethodIDs"\s+value=([0-9]+)',
            r'id="authMethodIDs"\s+value="([0-9]+)"',
        ],
    )
    if not auth_method_id:
        return

    auth_chain_code = _first_match(
        page_html,
        [
            rf'\$\(spCode\)\.val\(\'([0-9a-f]+)\'\)',
            rf'\$\("#spAuthChainCode{re.escape(auth_method_id)}"\)\.val\(\'([0-9a-f]+)\'\)',
            rf'\$\("#spAuthChainCode{re.escape(auth_method_id)}"\)\.val\("([0-9a-f]+)"\)',
        ],
    )
    if auth_chain_code:
        fields["spAuthChainCode"] = auth_chain_code


def _serialize_ajax_credentials(
    form: _LoginForm,
    credentials: dict[str, str],
    password: str,
) -> str:
    serialized = urllib.parse.urlencode(credentials)
    encoded_plain_password = urllib.parse.quote_plus(password)
    encrypted_password = _encrypt_password(password)
    password_pair = f"{form.password_field}={encoded_plain_password}"
    if password_pair in serialized:
        return serialized.replace(
            password_pair,
            f"{form.password_field}={encrypted_password}",
            1,
        )
    return serialized


def _first_match(text: str, patterns: list[str]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return html.unescape(match.group(1))
    return None


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
