from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

STAR_ACTIVITY_LIST_URL = "https://star.tongji.edu.cn/api/app-api/activity/index/list"
STAR_BASE_URL = "https://star.tongji.edu.cn"
IPHONE_USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1"
)

STAR_MODULE_NAMES = {
    "lixing": "力行之星",
    "qiusuo": "求索之星",
    "hongwen": "弘文之星",
    "mingde": "明德之星",
    "shizhi": "矢志之星",
}


@dataclass(frozen=True)
class StarActivitySummary:
    id: int
    title: str
    module_code: str | None
    module_name: str | None
    progress_name: str | None
    points: int
    activity_start_time: datetime | None
    location: str | None
    link: str

    @property
    def is_registration_open(self) -> bool:
        return bool(self.progress_name and "报名进行中" in self.progress_name)


async def fetch_public_activities(pages: int = 3, page_size: int = 10) -> list[StarActivitySummary]:
    async with httpx.AsyncClient(headers=_headers(), timeout=25) as client:
        result: list[StarActivitySummary] = []
        for page in range(1, pages + 1):
            response = await client.get(
                STAR_ACTIVITY_LIST_URL,
                params={"pageNo": page, "pageSize": page_size, "recommend": 1},
            )
            response.raise_for_status()
            payload = response.json()
            if int(payload.get("code", -1)) != 0:
                raise RuntimeError(f"STAR 活动列表返回异常 code={payload.get('code')}")
            items = ((payload.get("data") or {}).get("list")) or []
            if not items:
                break
            result.extend(_parse_activity(item) for item in items if item.get("id"))
        return result


def _headers() -> dict[str, str]:
    return {
        "User-Agent": IPHONE_USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Platform": "h5",
        "Referer": f"{STAR_BASE_URL}/app/",
    }


def _parse_activity(item: dict) -> StarActivitySummary:
    module_code = _string(item, "module")
    progress = item.get("progress") if isinstance(item.get("progress"), dict) else {}
    points = _number(item, "points")
    return StarActivitySummary(
        id=int(item.get("id")),
        title=_string(item, "title") or "未命名活动",
        module_code=module_code,
        module_name=STAR_MODULE_NAMES.get(module_code or "") or module_code,
        progress_name=_string(progress, "name"),
        points=int(points) if points is not None else 0,
        activity_start_time=_timestamp_ms(item, "activityStartTime"),
        location=_string(item, "addr"),
        link=f"{STAR_BASE_URL}/app/pages-home/detail/huodong?id={int(item.get('id'))}",
    )


def _string(mapping: dict, key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(mapping: dict, key: str) -> float | None:
    value = mapping.get(key)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _timestamp_ms(mapping: dict, key: str) -> datetime | None:
    value = mapping.get(key)
    if value is None:
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    if milliseconds <= 0:
        return None
    return datetime.fromtimestamp(milliseconds / 1000, tz=UTC)
