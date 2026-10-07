"""발송 규칙(kbj.services.notifier.policy) — config/notify.yaml 내용과 중복 키(설계 §5.2·§5.3)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from kbj.services.notifier.policy import (
    BODY_TTL_S,
    DAILY_TTL_S,
    SUBJECT_TTL_S,
    KindPolicy,
    NotifyConfig,
    NotifyConfigError,
    Topic,
    UnknownKind,
    dedup_key,
    default_as_of,
    key_part,
    parse_notify_config,
    sha16,
)

KST = ZoneInfo("Asia/Seoul")
D = date(2026, 10, 6)
AT = datetime(2026, 10, 6, 16, 40, tzinfo=KST)
BODY = "a" * 64


# ── config/notify.yaml 내용 ──────────────────────────────────────────────────────────────


def test_u2_morning_and_closing_are_once_a_day(config: NotifyConfig) -> None:
    morning, closing = config.policy("brief.morning"), config.policy("brief.closing")
    assert morning.once_per_day and closing.once_per_day
    assert morning.topic is Topic.MARKET and closing.topic is Topic.MARKET
    assert closing.catch_up_until == time(20, 30)  # 캐치업 20:30 까지(§5.2)
    assert morning.gate_ttl_s() == DAILY_TTL_S


def test_design_table_kinds_are_all_there(config: NotifyConfig) -> None:
    """§5.2 표의 14 종류 + 전환 기간 종류."""
    table = {
        "brief.morning": (Topic.MARKET, "daily"),
        "brief.closing": (Topic.MARKET, "daily"),
        "flows.report": (Topic.MARKET, "daily"),
        "etf.report": (Topic.MARKET, "daily"),
        "board.note": (Topic.NEWHIGH, "body"),
        "board.manual": (Topic.OPS, "body"),
        "alert.rule": (Topic.ALERT, "cooldown"),
        "alert.earnings": (Topic.ALERT, "subject"),
        "alert.revision": (Topic.ALERT, "daily"),
        "alert.gex_level": (Topic.ALERT, "cooldown"),
        "ops.watchdog": (Topic.OPS, "cooldown"),
        "ops.job_failed": (Topic.OPS, "subject"),
        "ops.universe": (Topic.OPS, "daily"),
        "cmd.reply": (Topic.REPLY, "subject"),
    }
    for kind, (topic, mode) in table.items():
        p = config.policy(kind)
        assert (p.topic, p.dedup) == (topic, mode), kind
    assert config.policy("alert.rule").cooldown_s == 3600  # SD _alert_cooldown_ok 60분
    assert config.policy("alert.gex_level").cooldown_s == 600  # GX 설계 10분
    assert config.policy("ops.watchdog").cooldown_s == 1800
    for k in ("board.rankings", "board.draft", "board.signals", "board.files", "board.us"):
        assert k in config.kinds  # ET 가 넘기는 kind(§5.10 #23~#29)


def test_topic_threads_are_unset_until_the_supergroup_exists(config: NotifyConfig) -> None:
    """[확인 필요] — 비어 있으면 일반 대화(R16)."""
    assert all(config.thread_for(t) is None for t in Topic)


def test_legacy_kinds_map_sd_senders(config: NotifyConfig) -> None:
    lk = config.legacy_kinds
    assert lk["alert_morning_briefing"] == "brief.morning"  # #3
    assert lk["send_closing_market_summary"] == "brief.closing"  # #9
    assert lk["check_alert_rules"] == "alert.rule"  # #12
    assert lk["_watchdog_notify"] == "ops.watchdog"  # #18
    assert lk["cmd_us_send"] == "board.us"  # ET #29
    assert lk["send_text"] == "flows.report"  # ET #40
    assert config.legacy_default_kind == "legacy.other"
    assert set(lk.values()) <= set(config.kinds)


def test_unknown_kind_is_an_error(config: NotifyConfig) -> None:
    with pytest.raises(UnknownKind, match=r"notify\.yaml"):
        config.policy("brief.lunch")


# ── 해석기 검증 ─────────────────────────────────────────────────────────────────────────


def _minimal(**over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "version": 1,
        "topics": {"market": None, "newhigh": None, "alert": None, "ops": None},
        "kinds": {"legacy.other": {"topic": "ops", "dedup": "body"}},
        "legacy_default_kind": "legacy.other",
    }
    data.update(over)
    return data


def test_minimal_config_parses() -> None:
    cfg = parse_notify_config(_minimal())
    assert cfg.delivery.max_attempts == 3 and cfg.inbox.keep_days == 14


@pytest.mark.parametrize(
    "bad",
    [
        {"kinds": {"legacy.other": {"topic": "ops", "dedup": "body", "typo": 1}}},
        {"kinds": {"legacy.other": {"topic": "ops", "dedup": "cooldown"}}},
        {"kinds": {"legacy.other": {"topic": "ops", "dedup": "body", "cooldown_s": 60}}},
        {"kinds": {"Legacy": {"topic": "ops", "dedup": "body"}}},
        {"legacy_kinds": {"alert_x": "no.such"}},
        {"legacy_kinds": {"not a name": "legacy.other"}},
        {"topics": {"market": None}},
        {"topics": {"market": 1, "newhigh": 2, "alert": 3, "ops": 4, "reply": 5}},
        {"legacy_default_kind": "no.such"},
        {"version": 2},
        {"surprise": True},
    ],
)
def test_bad_config_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(NotifyConfigError):
        parse_notify_config(_minimal(**bad))


def test_catch_up_time_read_as_minutes_by_yaml_is_accepted() -> None:
    """YAML 1.1 은 따옴표 없는 20:30 을 60진수(1230)로 읽는다."""
    kinds = {
        "legacy.other": {"topic": "ops", "dedup": "body"},
        "brief.closing": {"topic": "market", "dedup": "daily", "catch_up_until": 1230},
    }
    cfg = parse_notify_config(_minimal(kinds=kinds))
    assert cfg.policy("brief.closing").catch_up_until == time(20, 30)


# ── 중복 키(§5.3) ───────────────────────────────────────────────────────────────────────


def _pol(**kw: Any) -> KindPolicy:
    return KindPolicy.model_validate({"kind": "x.y", "topic": "ops", **kw})


def test_daily_key_and_ttl() -> None:
    p = _pol(dedup="daily")
    assert dedup_key(p, as_of=D, subject=None, body_sha256=BODY, now=AT) == (
        "x.y:20261006",
        "x.y:20261006",
    )
    gate, _ = dedup_key(p, as_of=D, subject="kr", body_sha256=BODY, now=AT)
    assert gate == "x.y:20261006:kr"
    assert p.gate_ttl_s() == DAILY_TTL_S == 36 * 3600


def test_daily_key_counts_each_legacy_origin_once() -> None:
    p = _pol(dedup="daily")
    a, _ = dedup_key(p, as_of=D, subject=None, body_sha256=BODY, now=AT, origin="sd.f1.text")
    b, _ = dedup_key(p, as_of=D, subject=None, body_sha256=BODY, now=AT, origin="sd.f2.text")
    assert a != b and a.startswith("x.y:20261006:")


def test_body_key_hashes_the_body() -> None:
    p = _pol(dedup="body")
    gate, log = dedup_key(p, as_of=D, subject="ignored", body_sha256="f" * 64, now=AT)
    assert gate == log == "x.y:20261006:" + "f" * 16
    assert p.gate_ttl_s() == BODY_TTL_S


def test_subject_key_lives_thirty_days() -> None:
    p = _pol(dedup="subject")
    assert dedup_key(p, as_of=D, subject="20261006000123", body_sha256=BODY, now=AT)[0] == (
        "x.y:20261006000123"
    )
    assert p.gate_ttl_s() == SUBJECT_TTL_S


def test_subject_missing_falls_back_to_body_hash() -> None:
    """legacy 처럼 대상을 모르는 호출이 서로 다른 알림을 한 쿨다운에 묶지 않게."""
    p = _pol(dedup="cooldown", cooldown_s=3600)
    a, _ = dedup_key(p, as_of=D, subject=None, body_sha256="1" * 64, now=AT)
    b, _ = dedup_key(p, as_of=D, subject=None, body_sha256="2" * 64, now=AT)
    assert a != b and a == "x.y:b" + "1" * 16


def test_cooldown_gate_slides_and_log_key_is_bucketed() -> None:
    p = _pol(dedup="cooldown", cooldown_s=3600)
    gate, log = dedup_key(p, as_of=D, subject="005930", body_sha256=BODY, now=AT)
    assert gate == "x.y:005930"  # 문지기 — 수명 cooldown_s(마지막 발송부터)
    assert log == f"x.y:005930:{int(AT.timestamp()) // 3600}"
    later = AT + timedelta(hours=1)
    _, log2 = dedup_key(p, as_of=D, subject="005930", body_sha256=BODY, now=later)
    assert log2 != log  # 쿨다운이 지나 다시 받아들인 것은 기록 키가 다르다
    assert p.gate_ttl_s() == 3600


def test_ttl_override() -> None:
    assert _pol(dedup="subject", ttl_s=172800).gate_ttl_s() == 172800


def test_expired_after_catch_up_time() -> None:
    p = _pol(dedup="daily", catch_up_until="20:30")
    assert not p.expired(D, datetime(2026, 10, 6, 20, 29, tzinfo=KST))
    assert p.expired(D, datetime(2026, 10, 6, 20, 30, tzinfo=KST))
    assert p.expired(D, datetime(2026, 10, 7, 8, 0, tzinfo=KST))
    assert not _pol(dedup="daily").expired(D, datetime(2030, 1, 1, tzinfo=KST))


def test_key_part_has_no_spaces_and_bounded_length() -> None:
    assert key_part("  a b\tc ") == "a_b_c"
    assert key_part("") == "-"
    long = key_part("x" * 200)
    assert long.startswith("h") and len(long) == 17


def test_naive_times_are_refused() -> None:
    p = _pol(dedup="daily")
    with pytest.raises(ValueError):
        dedup_key(p, as_of=D, subject=None, body_sha256=BODY, now=datetime(2026, 10, 6))  # noqa: DTZ001
    with pytest.raises(ValueError):
        default_as_of(datetime(2026, 10, 6))  # noqa: DTZ001


def test_default_as_of_is_the_kst_date() -> None:
    utc_evening = datetime(2026, 10, 5, 15, 30, tzinfo=ZoneInfo("UTC"))  # = 10-06 00:30 KST
    assert default_as_of(utc_evening) == D


def test_sha16() -> None:
    assert sha16("x") == sha16(b"x") and len(sha16("x")) == 16
