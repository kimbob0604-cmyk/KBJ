"""`python -m kbj.services.notifier [run | setup-webhook | webhook-info | check]`.

- `run`(기본): 대기열 소비·발송·받은 업데이트 처리·웹훅 점검·하트비트(compose `notifier`,
  `KBJ_SERVICE=notifier`). `KBJ_NOTIFY_ENABLED=false`(기본)면 보내지 않고 기록만(`suppressed`).
  필요: `KBJ_REDIS_URL`·`KBJ_DATABASE_URL`(발송 기록 `ops.notify_log` — 0002·0005 적용 뒤).
- `setup-webhook [--confirm]`: setWebhook(`KBJ_PUBLIC_BASE_URL` + `/telegram/webhook`,
  secret_token = `KBJ_TELEGRAM_WEBHOOK_SECRET`, `drop_pending_updates=false`). **받는 HTTP 서버는
  P3(D4)** 라 지금 걸면 텔레그램이 보낼 곳이 없다 — `--confirm` 없이는 할 일만 보여 준다. 전환
  순서는 설계 §5.11.
- `webhook-info`: getWebhookInfo 요약(주소는 호스트만).
- `check`: getMe·getChat — 메시지를 보내지 않는다(ET `check`).

출력·로그에 토큰·시크릿·chat id 를 싣지 않는다. 설정 오류는 종료 코드 2, 실행 실패는 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from kbj.config.settings import Settings
from kbj.core.masking import mask_text
from kbj.core.time import utcnow
from kbj.services.notifier.client import set_default_client
from kbj.services.notifier.policy import load_notify_config
from kbj.services.notifier.service import SERVICE, build_service, run
from kbj.services.notifier.store import PgInboxStore, PgNotifyLogStore
from kbj.services.notifier.telegram_api import TelegramApi, TelegramApiError
from kbj.services.notifier.webhook import ALLOWED_UPDATES, WEBHOOK_PATH
from kbj.services.runtime import (
    Heartbeater,
    LogHealthSink,
    connect_redis,
    install_stop,
    log_event,
    setup_logging,
)

log = logging.getLogger("kbj.services.notifier")


def _say(msg: str) -> None:
    sys.stdout.write(mask_text(msg) + "\n")


def cmd_webhook_info(settings: Settings) -> int:
    api = TelegramApi(settings.telegram_bot_token)
    try:
        info = api.get_webhook_info()
    except TelegramApiError as e:
        _say(f"getWebhookInfo 실패: {e.reason}")
        return 1
    url = str(info.get("url") or "")
    out: dict[str, Any] = {
        "url_set": bool(url),
        "host": urlsplit(url).hostname if url else None,
        "pending_update_count": info.get("pending_update_count"),
        "last_error_date": info.get("last_error_date"),
        "last_error_message": info.get("last_error_message"),
        "allowed_updates": info.get("allowed_updates"),
    }
    _say(json.dumps(out, ensure_ascii=False))
    return 0


def cmd_setup_webhook(settings: Settings, *, confirm: bool) -> int:
    base = (settings.public_base_url or "").rstrip("/")
    if not base.startswith("https://"):
        _say("KBJ_PUBLIC_BASE_URL 이 없거나 https 가 아니다")
        return 2
    secret = settings.telegram_webhook_secret
    if secret is None:
        _say("KBJ_TELEGRAM_WEBHOOK_SECRET 이 없다")
        return 2
    url = base + WEBHOOK_PATH
    plan = f"setWebhook → {urlsplit(url).hostname}{WEBHOOK_PATH} · 업데이트 {list(ALLOWED_UPDATES)}"
    if not confirm:
        _say(f"(미실행) {plan} — 받는 서버는 P3. 걸려면 --confirm (설계 §5.11 순서)")
        return 0
    api = TelegramApi(settings.telegram_bot_token)
    try:
        api.set_webhook(url, secret, ALLOWED_UPDATES, drop_pending=False)
    except TelegramApiError as e:
        _say(f"setWebhook 실패: {e.reason}")
        return 1
    _say(f"완료: {plan}")
    return 0


def cmd_check(settings: Settings) -> int:
    chat = settings.telegram_chat_id
    ok, why = TelegramApi(settings.telegram_bot_token).check(
        chat.get_secret_value() if chat is not None else None
    )
    _say(why)
    return 0 if ok else 1


def cmd_run(settings: Settings) -> int:  # pragma: no cover — 진입점(실제 Redis·DB·텔레그램)
    if settings.service != SERVICE:
        log_event(
            log, logging.ERROR, SERVICE, "config_error", error="KBJ_SERVICE 가 notifier 가 아니다"
        )
        return 2
    if settings.redis_url is None or settings.database_url is None:
        log_event(
            log, logging.ERROR, SERVICE, "config_error", error="KBJ_REDIS_URL·KBJ_DATABASE_URL 필요"
        )
        return 2
    from kbj.store.db import connect

    config = load_notify_config(settings=settings)
    redis = connect_redis(settings.redis_url)
    conn = connect(settings, autocommit=True, service=SERVICE, retries=3)
    service = build_service(
        settings,
        config=config,
        redis=redis,
        log_store=PgNotifyLogStore(conn),
        inbox_store=PgInboxStore(conn),
        now=utcnow,
        health=LogHealthSink(),
    )
    if service.router is not None:
        set_default_client(service.router.client)  # 이 프로세스 안의 notify() 도 같은 대기열로
    hb = Heartbeater(redis, SERVICE)
    stop = threading.Event()
    install_stop(stop)
    log_event(log, logging.INFO, SERVICE, "start", enabled=settings.notify_enabled)

    def beat(_st: dict[str, Any]) -> None:
        hb.beat(**service.health())

    run(service, stop, on_step=beat)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m kbj.services.notifier")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="대기열 소비·발송·수신 처리(기본)")
    sw = sub.add_parser("setup-webhook", help="setWebhook(수동)")
    sw.add_argument("--confirm", action="store_true", help="실제로 건다")
    sub.add_parser("webhook-info", help="getWebhookInfo 요약")
    sub.add_parser("check", help="getMe·getChat(메시지 안 보냄)")
    args = parser.parse_args(argv)
    setup_logging()
    try:
        settings = Settings()
    except Exception as e:  # 설정 오류 문구에 값이 실릴 수 있다 — 종류만
        _say(f"설정 오류: {type(e).__name__}")
        return 2
    cmd = args.cmd or "run"
    if cmd == "setup-webhook":
        return cmd_setup_webhook(settings, confirm=bool(args.confirm))
    if cmd == "webhook-info":
        return cmd_webhook_info(settings)
    if cmd == "check":
        return cmd_check(settings)
    return cmd_run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
