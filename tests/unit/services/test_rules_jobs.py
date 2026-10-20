"""`rules.intraday`·`rules.close` 처리기(묶음 EVL — §8 '실시간' 행): 슬롯
처리(정지·잘림·한 종목 실패
격리)·장중 수급 판단 불가·쿨다운·멱등·발송 모양·16:00 묶음. 합성 P8 세계 + 메모리 저장소 + 가짜 KIS
(FHKST01010100 슬롯 시세) + 가짜 notify."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

import pytest

from kbj.config.rules_cfg import load_rules_cfg
from kbj.core.calendar import TradingCalendar
from kbj.core.rules_rows import SignalRow
from kbj.engines.board.config import BoardConfig
from kbj.services.engine import rules as R
from kbj.store.repos.memory import MemoryRepos
from tests.fixtures.synthetic.p8_world import _slot_ts  # pyright: ignore[reportPrivateUsage]
from tests.unit.collectors.p3_fakes import Clock
from tests.unit.services.rules_world import (
    SPLIT_RATIO,
    FakeNotify,
    Ticket,
    add_rules,
    job_ctx,
    kis_for,
    put_live_flows,
    repos_with_world,
    slot_as_of,
    world,
)

W = world()
INTRADAY = {k: v for k, v in W.examples.items() if k in ("E8", "E9", "E10")}


def _sigs(repos: MemoryRepos, k: int, mode: str | None = None) -> list[SignalRow]:
    d = W.days[k]
    return repos.rules.signals(d, d, mode=mode)  # type: ignore[arg-type]


def run_slot(
    repos: MemoryRepos, k: int, slot: int, *, notify: Any = None, live: bool = True, **res: Any
) -> Any:
    ts = _slot_ts(W.days[k], slot)
    clock = Clock(ts + timedelta(seconds=40))
    if live:
        put_live_flows(repos, W, ts)
    _, kis = kis_for(W, clock)
    if notify is not None:
        res["notify"] = notify
    res.setdefault("kis", kis)
    return R.intraday(job_ctx("rules.intraday", slot_as_of(ts), now=clock(), repos=repos, **res))


def run_close(repos: MemoryRepos, k: int, *, notify: Any = None, **res: Any) -> Any:
    now = datetime.combine(W.days[k], datetime.min.time(), tzinfo=W.clock.tzinfo) + timedelta(
        hours=16
    )
    if notify is not None:
        res["notify"] = notify
    return R.close(job_ctx("rules.close", W.days[k].isoformat(), now=now, repos=repos, **res))


def test_split_ratio_matches_board_config() -> None:
    assert BoardConfig.load()["integrity"]["split_guard_ratio"] == SPLIT_RATIO


# ── 장중 ──────────────────────────────────────────────────────────────────────────────────────


def test_intraday_planted_slot_sends_e8_e9_and_stores_slot_quotes() -> None:
    repos = repos_with_world(W)
    ids = add_rules(repos, INTRADAY)
    n = FakeNotify()
    res = run_slot(repos, 317, 20, notify=n)
    assert res.status == "ok", res.detail
    ts = _slot_ts(W.days[317], 20)
    sent = {(s["subject"].split(":")[1], s["kind"]) for s in n.sent}
    assert sent == {("Q80015", "alert.rule")}
    assert len(n.sent) == 2  # E8·E9 둘
    assert all(s["parse_mode"] is None and s["as_of"] == W.days[317] for s in n.sent)
    assert {s["subject"].split(":")[0] for s in n.sent} <= set(ids)
    rows = [r for r in _sigs(repos, 317, "intraday") if r.status == "sent"]
    assert {(r.code, r.eval_ts, r.mode) for r in rows} == {("Q80015", ts, "intraday")}
    assert all(r.entry_price is not None and r.ticket == "t1" for r in rows)
    assert all(r.evidence["partial"] is True for r in rows)
    # 저장된 슬롯 시세 = 세계의 슬롯(장중 재생이 같은 입력을 읽는다)
    stored = {s.code: s for s in repos.rules.quote_slots(None, ts, ts + timedelta(minutes=10))}
    world_slot = next(s for s in W.slots_for("Q80015", W.days[317]) if s.slot_ts == ts)
    got = stored["Q80015"]
    assert (got.open, got.high, got.low, got.price, got.volume) == (
        world_slot.open,
        world_slot.high,
        world_slot.low,
        world_slot.price,
        world_slot.volume,
    )
    assert got.quality.value == "estimated" and len(stored) == 20


def test_intraday_cooldown_next_slot_and_rerun_is_idempotent() -> None:
    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    n = FakeNotify()
    run_slot(repos, 317, 20, notify=n)
    first = len(n.sent)
    run_slot(repos, 317, 20, notify=n)  # 같은 슬롯 다시(재기동) — 다시 보내지 않는다
    assert len(n.sent) == first
    res = run_slot(repos, 317, 21, notify=n)
    assert len(n.sent) == first  # 24시간 쿨다운
    assert res.detail["signals"].get("cooldown", 0) >= 2
    st = Counter((r.code, r.status) for r in _sigs(repos, 317, "intraday"))
    assert st[("Q80015", "sent")] == 2 and st[("Q80015", "cooldown")] >= 2


def test_intraday_no_live_flow_is_skipped_not_sent() -> None:
    repos = repos_with_world(W)
    rule = {
        "v": 1,
        "name": "수급 확인",
        "universe": {"watchlist": "main"},
        "mode": "intraday",
        "when": {
            "all": [
                {"ind": "close", "op": ">", "value": 1},
                {"ind": "foreign_streak", "op": ">=", "value": 0},
            ]
        },
    }
    add_rules(repos, [rule])
    n = FakeNotify()
    res = run_slot(repos, 316, 5, notify=n)
    assert res.status == "ok"
    rows = _sigs(repos, 316, "intraday")
    q14 = [r for r in rows if r.code == "Q80014"]
    assert [(r.status, r.skip_reason) for r in q14] == [("skipped", "no_live_flow")]
    assert "Q80014" not in {s["subject"].split(":")[1] for s in n.sent}
    # 가집계가 있는 종목은 보냈다(분할 의심 종목 제외)
    assert {r.code for r in rows if r.status == "sent"} == {f"Q8{i:04d}" for i in range(1, 21)} - {
        "Q80014",
        "Q80002",
    }
    # 회차 전체에 가집계가 없으면(flows.intraday 실패) 수급 규칙은 하나도 보내지 않는다
    repos2 = repos_with_world(W)
    add_rules(repos2, [rule])
    n2 = FakeNotify()
    run_slot(repos2, 316, 6, notify=n2, live=False)
    assert n2.sent == []


def test_intraday_flow_uses_the_latest_settled_round_and_names_it() -> None:
    """가집계는 하루 4회 입력(공식 예제) — 구분마다 확실히 들어온 가장 최근 회차 값만 쓰고 근거에 그
    회차 시각(ADR 0031 §6). 첫 회차 전 슬롯은 수급 조건 판단 불가(발송 없음)."""
    repos = repos_with_world(W)
    rule = {
        "v": 1,
        "name": "수급 회차",
        "universe": {"watchlist": "main"},
        "mode": "intraday",
        "when": {"all": [{"ind": "close", "op": ">", "value": 1},
                         {"ind": "foreign_streak", "op": ">=", "value": 0}]},
    }  # fmt: skip
    add_rules(repos, [rule])
    early = FakeNotify()
    res0 = run_slot(repos, 316, 2, notify=early)  # 09:20 — 외국인 09:30 회차 전
    assert res0.status == "ok" and early.sent == [] and res0.detail["flow_rounds"] == {}
    rows0 = [r for r in _sigs(repos, 316, "intraday") if r.status != "false"]
    assert rows0 and {r.skip_reason for r in rows0} <= {"no_live_flow", "split_suspect"}
    n = FakeNotify()
    res = run_slot(repos, 316, 20, notify=n)  # 12:20 — 외국인·기관 11:20 회차
    assert res.status == "ok"
    assert res.detail["flow_rounds"] == {"foreign": "11:20", "inst": "11:20"}
    assert n.sent and all("(장중 잠정 11:20 회차)" in s["text"] for s in n.sent)


def test_intraday_truncates_targets_and_records_them() -> None:
    repos = repos_with_world(W)
    add_rules(repos, {"E9": INTRADAY["E9"]})
    cfg = load_rules_cfg().model_copy(update={"intraday_max_codes": 5})
    res = run_slot(repos, 317, 20, rules_config=cfg)
    assert res.detail["targets"] == 5 and res.detail["truncated"] == 15
    rows = _sigs(repos, 317, "intraday")
    assert sum(1 for r in rows if r.skip_reason == "truncated") == 15
    # 관심 순서 앞 5종목만 KIS 를 부른다 → Q80015 는 잘려 신호 없음
    assert not any(r.status in ("sent", "dry") for r in rows)


def test_intraday_one_quote_failure_is_isolated() -> None:
    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    ts = _slot_ts(W.days[317], 20)
    clock = Clock(ts + timedelta(seconds=40))
    from tests.unit.collectors.p3_fakes import DirectKis

    server, _ = kis_for(W, clock)

    def hook(tr_id: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if params.get("FID_INPUT_ISCD") == "Q80007":
            return {"rt_cd": "1", "msg_cd": "EGW00999", "msg1": "합성 오류"}
        return body

    put_live_flows(repos, W, ts)
    n = FakeNotify()
    res = R.intraday(
        job_ctx(
            "rules.intraday",
            slot_as_of(ts),
            now=clock(),
            repos=repos,
            kis=DirectKis(server, hook=hook),
            notify=n,
        )
    )
    assert res.status == "ok"
    assert res.detail["quote_errors"] == 1 and res.detail["quote_error_codes"] == ["Q80007"]
    rows = _sigs(repos, 317, "intraday")
    assert {r.skip_reason for r in rows if r.code == "Q80007"} == {"error"}
    assert len(n.sent) == 2  # 다른 종목은 계속


def test_intraday_dry_without_notify() -> None:
    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    res = run_slot(repos, 317, 20)  # notify 자원 없음 → 발송 꺼짐(dry)
    assert res.status == "ok"
    st = {r.status for r in _sigs(repos, 317, "intraday") if r.code == "Q80015"}
    assert st == {"dry"}


def test_intraday_halt_flag_skips_code() -> None:
    from tests.unit.collectors.p3_fakes import DirectKis

    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    ts = _slot_ts(W.days[317], 20)
    clock = Clock(ts + timedelta(seconds=40))
    server, _ = kis_for(W, clock)

    def hook(tr_id: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if params.get("FID_INPUT_ISCD") == "Q80015":
            body = {**body, "output": {**body["output"], "temp_stop_yn": "Y"}}
        return body

    put_live_flows(repos, W, ts)
    R.intraday(
        job_ctx(
            "rules.intraday",
            slot_as_of(ts),
            now=clock(),
            repos=repos,
            kis=DirectKis(server, hook=hook),
        )
    )
    rows = [r for r in _sigs(repos, 317, "intraday") if r.code == "Q80015"]
    assert {(r.status, r.skip_reason) for r in rows} == {("skipped", "halted")}
    slot = repos.rules.quote_slots(["Q80015"], ts, ts + timedelta(minutes=10))[0]
    assert "halted" in slot.flags


def test_intraday_notify_failure_marks_error_and_fails_run() -> None:
    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    res = run_slot(repos, 317, 20, notify=FakeNotify(result=Ticket(ok=False, reason="redis 없음")))
    assert res.status == "failed"
    rows = [r for r in _sigs(repos, 317, "intraday") if r.code == "Q80015"]
    assert {(r.status, r.skip_reason) for r in rows} == {("skipped", "error")}
    # 다음 실행이 다시 판정한다(오류 행은 쿨다운에 세지 않는다)
    n = FakeNotify()
    run_slot(repos, 317, 21, notify=n)
    assert len(n.sent) == 2


def test_intraday_status_mapping_duplicate_and_disabled() -> None:
    assert R.status_of(None) == "dry"
    assert R.status_of(Ticket(reason="suppressed")) == "dry"
    assert R.status_of(Ticket(duplicate=True)) == "suppressed"
    assert R.status_of(Ticket()) == "sent"
    assert R.status_of(Ticket(ok=False)) is None


def test_intraday_no_rules_calls_no_kis_and_holiday_skips() -> None:
    repos = repos_with_world(W)
    ts = _slot_ts(W.days[317], 20)
    clock = Clock(ts)
    server, kis = kis_for(W, clock)
    before = len(server.calls)
    res = R.intraday(job_ctx("rules.intraday", slot_as_of(ts), now=clock(), repos=repos, kis=kis))
    # 'ok' 면 선점한 시세 키를 안 받아 실행기가 실패로 센다(2026-10-20 — 10분마다 실패)
    assert res.status == "skipped" and "규칙 없음" in res.detail["reason"]
    assert len(server.calls) == before
    sat = TradingCalendar.default().next_weekday(W.days[-1])  # 다음 평일 — 휴장 아님일 수 있다
    hol = W.days[-1] + timedelta(days=1)  # 토요일
    assert hol.weekday() == 5 and sat.weekday() < 5
    res = R.intraday(job_ctx("rules.intraday", f"{hol:%Y-%m-%d}T10:00", now=clock(), repos=repos))
    assert res.status == "skipped"


def test_intraday_split_suspect_is_skipped() -> None:
    repos = repos_with_world(W)
    rule = {
        "v": 1,
        "name": "분할",
        "universe": {"codes": ["Q80002"]},
        "mode": "intraday",
        "when": {"all": [{"ind": "close", "op": ">", "value": 1}]},
    }
    add_rules(repos, [rule])
    run_slot(repos, 317, 0)
    rows = _sigs(repos, 317, "intraday")
    # 분할 칸 200 은 마지막 260칸 창 안 → split_suspect
    assert [(r.status, r.skip_reason) for r in rows] == [("skipped", "split_suspect")]


# ── 마감 ──────────────────────────────────────────────────────────────────────────────────────


def test_close_planted_day_one_digest_for_all_rules() -> None:
    repos = repos_with_world(W)
    add_rules(repos, {k: W.examples[k] for k in ("E1", "E3")})
    n = FakeNotify()
    res = run_close(repos, 300, notify=n)
    assert res.status == "ok", res.detail
    assert len(n.sent) == 1
    msg = n.sent[0]
    assert msg["kind"] == "alert.rule_close" and msg["as_of"] == W.days[300]
    assert "subject" not in msg or msg.get("subject") is None
    assert msg["text"].startswith(f"[규칙 마감] {W.days[300]:%m-%d} — 16:00 기준")
    assert "RSI 과매도" in msg["text"] and "볼린저 하단 터치" in msg["text"]
    rows = [r for r in _sigs(repos, 300, "close") if r.status == "sent"]
    assert {("Q80010", "sent")} <= {(r.code, r.status) for r in rows}
    assert all(r.eval_ts == R.close_eval_ts(W.days[300]) for r in rows)
    assert all(r.entry_price == r.evidence["price"] for r in rows)


def test_close_rerun_does_not_resend_and_zero_hits_sends_nothing() -> None:
    repos = repos_with_world(W)
    add_rules(repos, {"E1": W.examples["E1"]})
    n = FakeNotify()
    run_close(repos, 300, notify=n)
    before = [(r.signal_id, r.status) for r in _sigs(repos, 300, "close")]
    run_close(repos, 300, notify=n)  # 캐치업 재실행
    assert len(n.sent) == 1
    assert [(r.signal_id, r.status) for r in _sigs(repos, 300, "close")] == before
    # 다음 날: 24시간 = 경계 포함 → 다시 참이면 허락, 거짓이면 0건 → 보내지 않는다
    n2 = FakeNotify()
    rule = {
        "v": 1,
        "name": "없음",
        "universe": {"watchlist": "main"},
        "mode": "close",
        "when": {"all": [{"ind": "rsi", "op": "<", "value": 0}]},
    }
    repos2 = repos_with_world(W)
    add_rules(repos2, [rule])
    res = run_close(repos2, 300, notify=n2)
    assert res.status == "ok" and n2.sent == [] and res.detail["digest"] == "none"


def test_close_notify_failure_records_error() -> None:
    repos = repos_with_world(W)
    add_rules(repos, {"E1": W.examples["E1"]})
    res = run_close(repos, 300, notify=FakeNotify(raise_exc=RuntimeError("redis down")))
    assert res.status == "failed"
    assert {r.status for r in _sigs(repos, 300, "close")} <= {"skipped"}


def test_close_market_universe_and_holiday() -> None:
    repos = repos_with_world(W)
    rule = {
        "v": 1,
        "name": "시장 전체",
        "universe": {"market": "KOSDAQ"},
        "mode": "close",
        "when": {"all": [{"ind": "change_pct", "op": ">", "value": -100}]},
    }
    add_rules(repos, [rule])
    res = run_close(repos, 319)  # 유니버스 기준일(세계 마지막 날)
    assert res.status == "ok"
    rows = _sigs(repos, 319, "close")
    # 코스닥(짝수 번호) 40종목만 평가 — 분할 의심(Q80002)은 건너뜀
    assert {int(r.code[2:]) % 2 for r in rows} == {0} and len(rows) == 40
    assert {r.code for r in rows if r.status == "skipped"} == {"Q80002"}
    sat = W.days[-1] + timedelta(days=1)
    res = R.close(job_ctx("rules.close", sat.isoformat(), now=W.clock, repos=repos))
    assert res.status == "skipped"


def test_close_both_mode_shares_cooldown_with_intraday() -> None:
    repos = repos_with_world(W)
    rule = W.examples["E9"].model_dump(mode="json", exclude_none=True)
    rule["mode"] = "both"
    add_rules(repos, [rule])
    run_slot(repos, 317, 20)
    run_close(repos, 317)
    st = {(r.mode, r.status) for r in _sigs(repos, 317) if r.code == "Q80015"}
    assert ("intraday", "dry") in st and ("close", "cooldown") in st


def test_invalid_stored_dsl_is_isolated() -> None:
    repos = repos_with_world(W)
    add_rules(repos, {"E1": W.examples["E1"]})
    cfg = load_rules_cfg().model_copy(
        update={"dsl": load_rules_cfg().dsl.model_copy(update={"max_conditions": 0})}
    )
    with pytest.raises(Exception):  # noqa: B017 — 설정 검사가 0 을 막는지 확인
        load_rules_cfg().dsl.model_validate({"max_depth": 3, "max_conditions": 0})
    res = run_close(repos, 300, rules_config=cfg)
    assert res.detail["rules"] == 0 and res.detail["dsl_invalid"] == 1


def test_command_reader_lists_today() -> None:
    repos = repos_with_world(W)
    add_rules(repos, INTRADAY)
    run_slot(repos, 317, 20)
    read = R.command_reader(repos, lambda: _slot_ts(W.days[317], 25))
    text = read()
    assert "규칙 신호" in text and "Q80015" in text and "12:20 장중" in text
