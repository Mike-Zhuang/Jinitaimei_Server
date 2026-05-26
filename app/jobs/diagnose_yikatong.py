from __future__ import annotations

import asyncio
import logging
import os

from app.clients.yikatong import YikatongLoginError, fetch_campus_card_balance, login_yikatong
from app.database import init_database

logger = logging.getLogger("jinitaimei.yikatong.diagnose")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
    await init_database()

    username = os.environ.get("YIKATONG_DEBUG_USERNAME", "").strip()
    password = os.environ.get("YIKATONG_DEBUG_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit(
            "请通过环境变量 YIKATONG_DEBUG_USERNAME / YIKATONG_DEBUG_PASSWORD 提供诊断账号"
        )

    masked_user = f"{username[:2]}***{username[-2:]}" if len(username) >= 4 else "***"
    logger.info("start yikatong diagnosis user=%s", masked_user)
    try:
        session = await login_yikatong(username, password)
        logger.info(
            "login ok token_len=%s cookie_present=%s",
            len(session.bearer_token),
            bool(session.cookie_header),
        )
        balance = await fetch_campus_card_balance(session)
        logger.info(
            "balance ok amount=%.2f account_present=%s owner_present=%s",
            balance.balance_yuan,
            bool(balance.account),
            bool(balance.owner_name),
        )
    except YikatongLoginError as exc:
        logger.error("yikatong diagnosis login failed: %s", exc)
        raise


if __name__ == "__main__":
    asyncio.run(main())
