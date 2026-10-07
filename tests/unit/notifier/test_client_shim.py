"""알림 넣기·legacy shim(kbj.services.notifier.client) — 설계 §1.6·§5.5·§5.9.

- legacy 반환 모양 그대로: `legacy_send*` → `(ok, 사유)`(ET 식, SD 는 `[0]`)
- 발송 꺼짐(기본): legacy `(False, "발송 꺼짐(KBJ_NOTIFY_ENABLED=false) — 기록만")`, kbj
  `notify()` → `NotifyTicket(ok=True, reason="suppressed")` — 넣기는 그대로 한다(notifier 가 기록)
- Redis 가 없으면 `(False, 사유)` — 조용히 버리지 않는다
- kind 를 안 넘긴 legacy 호출은 호출 스택의 함수 이름 → `legacy_kinds`
- 정적 검사: 설계 §5.9 표의 shim 대상 7곳이 전부 `legacy_send*` 를 부른다(묶음 H 재배선 뒤 통과)
"""

from __future__ import annotations

import ast
from pathlib import Path

import fakeredis
import pytest

from kbj.services.notifier import client as client_mod
from kbj.services.notifier.client import (
    DISABLED_REASON,
    NotifyClient,
    legacy_send,
    notify,
    set_default_client,
    webhook_status_from,
)
from kbj.services.notifier.outbox import Outbox
from kbj.services.notifier.policy import NotifyConfig
from kbj.services.runtime.heartbeat import Heartbeat
from kbj.store.redis_keys import NOTIFY_WEBHOOK_INFO, heartbeat_key
from tests.fakes.clock import FakeClock

ROOT = Path(__file__).resolve().parents[3]


def _client(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, *, enabled: bool
) -> NotifyClient:
    return NotifyClient(config, enabled, outbox=Outbox(redis, now=clock), now=clock)


def _queued(redis: fakeredis.FakeRedis, clock: FakeClock) -> list[str]:
    return [e.message.kind for e in Outbox(redis, now=clock).read("t", 50) if e.message]


# ── legacy 반환 모양 ─────────────────────────────────────────────────────────────────────


def test_legacy_send_enabled_returns_ok_and_reason(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    ok, why = c.legacy_send("본문", source="sd.send_telegram")
    assert ok is True and isinstance(why, str) and "대기열" in why
    assert _queued(redis, clock) == ["legacy.other"]


def test_legacy_send_disabled_is_false_with_reason_but_still_recorded(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    """SD send_telegram 의 TELEGRAM_ENABLED 꺼짐 반환(False)과 같은 뜻 — 그래도 기록은 남긴다."""
    c = _client(config, redis, clock, enabled=False)
    assert c.legacy_send("본문", source="sd.send_telegram") == (False, DISABLED_REASON)
    assert _queued(redis, clock) == ["legacy.other"]


def test_notify_disabled_is_ok_suppressed(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=False)
    t = c.notify("<b>아침</b>", kind="brief.morning", source="job.brief.morning")
    assert t.ok and t.reason == "suppressed" and not t.duplicate and t.id


def test_notify_duplicate_same_day(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    first = c.notify("하나", kind="brief.morning", source="job")
    second = c.notify("둘", kind="brief.morning", source="job")  # U2 — 하루 1회
    assert first.ok and not first.duplicate
    assert second.ok and second.duplicate and second.reason == "duplicate"
    ok, why = c.legacy_send("x", source="s", kind="brief.morning")
    assert ok and "대기열" in why  # legacy 는 부른 곳(origin)마다 따로 센다


def test_legacy_report_of_several_messages_is_not_cut_to_the_first(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    """kind 를 넘긴 legacy 가 한 번에 여러 통을 보내도(ET ETF `send_report(msgs)`·수급 종목별 차트)
    둘째 통부터 duplicate 로 빠지지 않는다. 같은 내용을 다시 보내면(재실행) 그것만 막는다."""
    c = _client(config, redis, clock, enabled=True)
    msgs = ["ETF 리포트 1/3", "ETF 리포트 2/3", "ETF 리포트 3/3"]
    got = [c.legacy_send(m, source="et.etf", kind="etf.report") for m in msgs]
    assert all(ok and "대기열" in why for ok, why in got)
    ok, why = c.legacy_send(msgs[0], source="et.etf", kind="etf.report")  # 같은 날 재실행
    assert ok and "중복" in why
    charts = []
    for code in ("005930", "000660"):
        p = tmp_path / f"{code}.png"
        p.write_bytes(code.encode())
        charts.append(p)
    first = c.legacy_send_media(
        [charts[0]], "삼성전자 005930", source="et.flow", kind="flows.report"
    )
    second = c.legacy_send_media(
        [charts[1]], "SK하이닉스 000660", source="et.flow", kind="flows.report"
    )
    again = c.legacy_send_media(
        [charts[0]], "삼성전자 005930", source="et.flow", kind="flows.report"
    )
    assert "대기열" in first[1] and "대기열" in second[1] and "중복" in again[1]
    assert _queued(redis, clock) == ["etf.report"] * 3 + ["flows.report"] * 2
    # kbj 작업(origin 없음)은 그대로 종류당 하루 1회(U2)
    assert c.notify("하나", kind="etf.report", source="job").ok
    assert c.notify("둘", kind="etf.report", source="job").duplicate


def test_no_redis_is_a_reason_not_silence(config: NotifyConfig, clock: FakeClock) -> None:
    c = NotifyClient(config, True, now=clock)  # 대기열 없음(KBJ_REDIS_URL 없음)
    ok, why = c.legacy_send("본문", source="sd.send_telegram")
    assert ok is False and "REDIS" in why.upper()
    t = c.notify("본문", kind="brief.morning", source="job")
    assert not t.ok and "보내지 않았다" in t.reason


def test_redis_down_is_a_reason(config: NotifyConfig, clock: FakeClock) -> None:
    server = fakeredis.FakeServer()
    c = NotifyClient(config, True, outbox=Outbox(fakeredis.FakeRedis(server=server), now=clock))
    server.connected = False
    ok, why = c.legacy_send("본문", source="sd.send_telegram")
    assert ok is False and "Redis" in why


def test_unknown_explicit_kind_is_refused(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    ok, why = c.legacy_send("x", source="et.board", kind="board.nope")
    assert not ok and "board.nope" in why
    assert not c.notify("x", kind="board.nope", source="s").ok


def test_empty_body_is_refused(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    assert c.legacy_send("  ", source="s")[0] is False
    assert not c.notify("", kind="brief.morning", source="s").ok


def test_missing_document_is_named(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    c = _client(config, redis, clock, enabled=True)
    ok, why = c.legacy_send_document(tmp_path / "없는.xlsx", source="et.board.files")
    assert not ok and "없다" in why and "없는.xlsx" in why


def test_document_and_media_are_queued(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    c = _client(config, redis, clock, enabled=True)
    doc = tmp_path / "rankings.xlsx"
    doc.write_bytes(b"xlsx")
    pngs = [tmp_path / "a.png", tmp_path / "b.png"]
    for p in pngs:
        p.write_bytes(b"png")
    assert c.legacy_send_document(doc, "엑셀", source="et.board.files", kind="board.files")[0]
    assert c.legacy_send_media(pngs, "차트", source="et.flow", kind="flows.report")[0]
    assert _queued(redis, clock) == ["board.files", "flows.report"]


def test_attachment_cap(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock, tmp_path: Path
) -> None:
    small = config.model_copy(
        update={"delivery": config.delivery.model_copy(update={"max_attachment_bytes": 3})}
    )
    c = _client(small, redis, clock, enabled=True)
    p = tmp_path / "big.html"
    p.write_bytes(b"1234")
    t = c.notify_document(p, kind="board.files", source="s")
    assert not t.ok and "상한" in t.reason


def test_parse_mode_none_stays_plain(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)
    c.legacy_send("평문", source="et.board", parse_mode=None, kind="board.us")
    c.notify("정책", kind="board.rankings", source="s")  # parse_mode 생략 → 정책(Markdown)
    msgs = [e.message for e in Outbox(redis, now=clock).read("t", 5) if e.message]
    assert [m.parse_mode for m in msgs] == [None, "Markdown"]


# ── kind 없는 legacy 호출 → 호출 스택의 함수 이름 ──────────────────────────────────────────


def test_legacy_kind_found_from_the_calling_function(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    c = _client(config, redis, clock, enabled=True)

    def send_telegram(message: str) -> bool:  # SD shim 모양(설계 §5.9)
        return c.legacy_send(message, source="sd.send_telegram")[0]

    def alert_morning_briefing() -> bool:
        return send_telegram("아침")

    def check_alert_rules() -> bool:
        return send_telegram("규칙")

    assert alert_morning_briefing() and check_alert_rules() and send_telegram("그 밖")
    assert _queued(redis, clock) == ["brief.morning", "alert.rule", "legacy.other"]


def test_module_level_shim_uses_the_default_client(
    config: NotifyConfig, redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    set_default_client(_client(config, redis, clock, enabled=True))
    try:

        def alert_closing_summary() -> tuple[bool, str]:
            return legacy_send("마감", source="sd.send_telegram")

        assert alert_closing_summary()[0]
        assert notify("x", kind="ops.universe", source="job").ok
        assert _queued(redis, clock) == ["brief.closing", "ops.universe"]
    finally:
        set_default_client(None)


def test_module_level_shim_reports_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> NotifyClient:
        raise FileNotFoundError("config/notify.yaml")

    set_default_client(None)
    monkeypatch.setattr(client_mod, "_build_default", broken)
    ok, why = legacy_send("x", source="s")
    assert not ok and "설정" in why
    assert not notify("x", kind="brief.morning", source="s").ok


# ── webhook_status ───────────────────────────────────────────────────────────────────────


def test_webhook_status_reads_notifier_summary(
    redis: fakeredis.FakeRedis, clock: FakeClock
) -> None:
    st = webhook_status_from(redis, clock.now())
    assert not st["ok"] and "점검" in st["reason"]
    hb = Heartbeat(service="notifier", at=clock.now(), pid=1, status={"enabled": False})
    redis.set(heartbeat_key("notifier"), hb.model_dump_json())
    redis.set(NOTIFY_WEBHOOK_INFO, '{"url_set": true, "pending_update_count": 2, "error": null}')
    st = webhook_status_from(redis, clock.now())
    assert st["ok"] and st["pending_update_count"] == 2 and st["notify_enabled"] is False
    redis.set(NOTIFY_WEBHOOK_INFO, '{"url_set": false, "error": null}')
    assert "setup-webhook" in webhook_status_from(redis, clock.now())["reason"]
    # 발송 꺼짐이라 점검을 건너뛴 기록은 '웹훅 없음' 으로 단정하지 않는다
    redis.set(
        NOTIFY_WEBHOOK_INFO,
        '{"checked": false, "url_set": null, "error": null, "note": "발송 꺼짐 — 점검 생략"}',
    )
    st = webhook_status_from(redis, clock.now())
    assert not st["ok"] and "점검 안 함" in st["reason"] and "setup-webhook" not in st["reason"]


# ── 정적 검사: shim 대상 7곳(§5.9) ────────────────────────────────────────────────────────

SHIM_TARGETS = (
    ("legacy/stock_dashboard/server.py", "send_telegram"),
    ("legacy/stock_dashboard/server.py", "send_telegram_long"),
    ("legacy/stock_dashboard/earnings_telegram_sender.py", "send_telegram_message"),
    ("legacy/etf_traker/board/report/telegram.py", "send"),
    ("legacy/etf_traker/board/report/telegram.py", "send_document"),
    ("legacy/etf_traker/etf_tracker_v9/tracker.py", "send_telegram"),
    ("legacy/etf_traker/monitor/flow/telegram.py", "send_photos"),
)
SHIM_NAMES = {"legacy_send", "legacy_send_document", "legacy_send_media"}


def shim_violations(root: Path) -> list[str]:
    """각 대상 함수가 legacy_send* 를 부르고 api.telegram.org 를 직접 담지 않는지."""
    bad: list[str] = []
    for rel, name in SHIM_TARGETS:
        path = root / rel
        if not path.exists():
            bad.append(f"{rel}: 파일 없음")
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        fns = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == name
        ]
        if not fns:
            bad.append(f"{rel}:{name}: 함수 없음")
            continue
        fn = fns[0]
        calls = {
            (c.func.id if isinstance(c.func, ast.Name) else getattr(c.func, "attr", ""))
            for c in ast.walk(fn)
            if isinstance(c, ast.Call)
        }
        body = ast.get_source_segment(src, fn) or ""
        if not calls & SHIM_NAMES:
            bad.append(f"{rel}:{name}: legacy_send* 를 부르지 않는다")
        if "api.telegram.org" in body:
            bad.append(f"{rel}:{name}: api.telegram.org 를 직접 담는다")
    return bad


def test_shim_check_catches_a_direct_sender(tmp_path: Path) -> None:
    for rel, name in SHIM_TARGETS:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_text("", encoding="utf-8")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"\ndef {name}(text, **kw):\n    return legacy_send(text, source='x')\n")
    assert shim_violations(tmp_path) == []
    first = tmp_path / SHIM_TARGETS[0][0]
    first.write_text(
        "def send_telegram(m):\n    return post('https://api.telegram.org/bot/x', m)\n"
        "def send_telegram_long(m):\n    return legacy_send(m, source='x')\n",
        encoding="utf-8",
    )
    got = shim_violations(tmp_path)
    assert len(got) == 2 and all("send_telegram:" in g for g in got)


def test_legacy_shim_targets_call_legacy_send() -> None:
    assert shim_violations(ROOT) == []
