"""`flows.market_minute` 처리기 — 가짜 KIS(1분마다 누적이 느는 장중) + 메모리 저장소(P5 F2 §4).

정상 하루(정규장 391분) 빈 분 0, 실패한 분만 정확히 빈다(뒤에서 채울 수 없는 TR — 오늘 누적 한 줄,
맥이 잠든 동안의 분도 깨어난 뒤 지금 누적으로 채우지 않는다), 행 ts = 슬롯 분, 제때 ok·45초 넘으면
estimated, 같은 분 재실행 멱등, 재시도는 못 받은 조합만, 장전은 not_ready, 검산 실패 분은 invalid 로
쓰고 어긋난 칸별 수를 남긴다. 현물·주식선물 조합은 KIS 공식 코드(KSP·0001, KSQ·1001, 999·S001)로
부른다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from kbj.config.markets import MarketsConfig, parse_markets
from kbj.core.quality import Quality
from kbj.data.spec import DataKey
from kbj.services.collectors import flows_minute
from tests.fakes.kis_server import FakeKisServer
from tests.unit.collectors.p3_fakes import Clock, DirectKis, ctx, kst, load_cfg, new_repos

DAY = date(2026, 11, 17)
K2I = [("K2I", "F001"), ("K2I", "OC01"), ("K2I", "OP01")]


def key(as_of: str) -> DataKey:
    return DataKey("KIS", "market_investor_intraday", as_of)


def at(h: int, m: int) -> str:
    return f"2026-11-17T{h:02d}:{m:02d}"


def spot_cfg() -> MarketsConfig:
    """코스피 조합을 합성 자리표시 코드로 켠 사본(시험 전용 — 운영은 #39 전 꺼짐)."""
    data: dict[str, Any] = load_cfg().model_dump(mode="json")
    for p in data["flows"]["minute"]["pairs"]:
        if p["id"] == "kospi":
            p.update(market="SYNK", sector="SYN0", enabled=True)
    return parse_markets(data)


def setup(clock_at: datetime, hook: Any = None) -> tuple[Any, FakeKisServer, Clock, DirectKis]:
    repos = new_repos()
    clock = Clock(clock_at)
    server = FakeKisServer(clock)
    return repos, server, clock, DirectKis(server, hook=hook)


def run(repos: Any, kis: DirectKis, as_of: str, now: datetime, **kw: Any) -> Any:
    return flows_minute.run(
        ctx("flows.market_minute", as_of, [key(as_of)], now=now, repos=repos, kis=kis, **kw)
    )


def test_one_minute_writes_twelve_rows_per_on_pair_at_the_slot_minute() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 13, 0, 10))
    res = run(repos, kis, at(13, 0), clock())
    assert res.status == "ok", res.detail
    rows = repos.flows.investor_minutes(DAY, K2I)
    assert len(rows) == 3 * 12
    assert {r.ts for r in rows} == {kst(2026, 11, 17, 13, 0)}  # 호출 시각이 아니라 슬롯 분
    assert {r.quality for r in rows} == {Quality.OK}
    # 켠 조합만 — K2I 3 + 현물 2 + 주식선물 1 + 위클리 4. 야간은 꺼짐
    assert kis.count("FHPTJ04030000") == 10
    assert res.collected == (key(at(13, 0)),)
    assert res.detail["pairs"] == 10 and res.detail["pairs_written"] == 10


def test_spot_and_stock_futures_use_official_kis_codes() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 13, 0, 10))
    run(repos, kis, at(13, 0), clock())
    sent = {(c[1]["FID_INPUT_ISCD"], c[1]["FID_INPUT_ISCD_2"]) for c in kis.calls}
    assert {("KSP", "0001"), ("KSQ", "1001"), ("999", "S001")} <= sent
    spot = repos.flows.investor_minutes(DAY, [("KSP", "0001"), ("KSQ", "1001")])
    assert len(spot) == 2 * 12 and {r.quality for r in spot} == {Quality.OK}  # 검산 ① 통과


def test_late_minute_is_estimated_with_reason() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 13, 0, 50))
    res = run(repos, kis, at(13, 0), clock())
    assert res.status == "ok"
    assert {r.quality for r in repos.flows.investor_minutes(DAY, K2I)} == {Quality.ESTIMATED}
    assert res.detail["late_s"] == 50


def test_preopen_all_zero_is_not_ready_and_writes_nothing() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 8, 59, 5))
    res = run(repos, kis, at(8, 59), clock())
    assert res.status == "not_ready", res.detail
    assert repos.flows.investor_minutes(DAY, K2I) == []


def test_same_minute_rerun_is_idempotent() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 10, 0, 5))
    run(repos, kis, at(10, 0), clock())
    first = repos.flows.investor_minutes(DAY, K2I)
    run(repos, kis, at(10, 0), clock())
    assert repos.flows.investor_minutes(DAY, K2I) == first


def test_pair_failure_is_isolated_and_retry_calls_only_missing_pairs() -> None:
    repos, server, clock, kis = setup(kst(2026, 11, 17, 11, 0, 5))
    server.inject("HTTP429", 1, tr_id="FHPTJ04030000")  # 첫 조합(선물)만 실패
    res = run(repos, kis, at(11, 0), clock())
    assert res.status == "failed"
    assert "k200_fut" in res.detail["pair_errors"]
    assert len(repos.flows.investor_minutes(DAY, K2I)) == 2 * 12  # 받은 두 조합은 썼다
    assert res.collected == ()  # 키는 미완 — 슬롯 안 재시도
    before = kis.count("FHPTJ04030000")
    clock.at = kst(2026, 11, 17, 11, 0, 20)
    res2 = run(repos, kis, at(11, 0), clock(), attempt=2)
    assert res2.status == "ok", res2.detail
    assert kis.count("FHPTJ04030000") - before == 1  # 못 받은 조합 하나만
    assert res2.detail["pairs_already"] == 9  # K2I 둘 + 현물 둘 + 주식선물 + 위클리 넷
    assert len(repos.flows.investor_minutes(DAY, K2I)) == 3 * 12


def test_timeout_is_a_failure_not_silence() -> None:
    repos, server, clock, kis = setup(kst(2026, 11, 17, 11, 1, 5))
    server.inject("TIMEOUT", 3, tr_id="FHPTJ04030000")
    res = run(repos, kis, at(11, 1), clock())
    assert res.status == "failed"
    assert set(res.detail["pair_errors"]) == {"k200_fut", "k200_call", "k200_put"}
    assert "ReadTimeout" in res.detail["pair_errors"]["k200_fut"]


def test_no_pairs_on_is_ok_with_zero_rows() -> None:
    data: dict[str, Any] = load_cfg().model_dump(mode="json")
    for p in data["flows"]["minute"]["pairs"]:
        p["enabled"] = False
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 11, 0, 5))
    res = run(repos, kis, at(11, 0), clock(), markets=parse_markets(data))
    assert res.status == "ok" and res.rows == 0
    assert kis.count() == 0


def test_spot_pair_check1_failure_marks_the_minute_invalid() -> None:
    def broken(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr == "FHPTJ04030000" and params.get("FID_INPUT_ISCD") == "SYNK":
            row = dict(body["output"][0])
            row["prsn_ntby_tr_pbmn"] = str(int(row["prsn_ntby_tr_pbmn"]) + 100)
            return {**body, "output": [row]}
        return body

    repos, _s, clock, kis = setup(kst(2026, 11, 17, 12, 0, 5), hook=broken)
    res = run(repos, kis, at(12, 0), clock(), markets=spot_cfg())
    assert res.status == "ok"
    spot = repos.flows.investor_minutes(DAY, [("SYNK", "SYN0")])
    assert len(spot) == 12 and {r.quality for r in spot} == {Quality.INVALID}
    assert {r.quality for r in repos.flows.investor_minutes(DAY, K2I)} == {Quality.OK}
    assert res.detail["invalid_slots"] == 1
    assert res.detail["check_failed"] == {"kospi:c1": 1}  # 어긋난 칸별 수(운영 화면에서 원인 확인)


def test_orgn_including_etc_orgt_is_not_marked_invalid() -> None:
    """KIS 기관계가 기타단체를 넣은 정의여도 그 분을 빼지 않는다(기관 = 7세부 + 기타단체)."""

    def with_etc(tr: str, params: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
        if tr != "FHPTJ04030000":
            return body
        row = dict(body["output"][0])
        for sfx in ("seln_vol", "shnu_vol", "seln_tr_pbmn", "shnu_tr_pbmn", "ntby_tr_pbmn"):
            row[f"orgn_{sfx}"] = str(int(row[f"orgn_{sfx}"]) + int(row[f"etc_orgt_{sfx}"]))
        row["orgn_ntby_qty"] = str(int(row["orgn_ntby_qty"]) + int(row["etc_orgt_ntby_vol"]))
        # 개인은 그대로 — 기타단체가 기관계 안으로 옮겨 갔을 뿐 시장 합(0)은 같다
        return {**body, "output": [row]}

    repos, _s, clock, kis = setup(kst(2026, 11, 17, 12, 0, 5), hook=with_etc)
    res = run(repos, kis, at(12, 0), clock())
    assert res.status == "ok", res.detail
    assert "invalid_slots" not in res.detail
    rows = repos.flows.investor_minutes(DAY, [*K2I, ("KSP", "0001")])
    assert {r.quality for r in rows} == {Quality.OK}


def test_spot_pair_balanced_minute_passes_checks() -> None:
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 12, 0, 5))
    res = run(repos, kis, at(12, 0), clock(), markets=spot_cfg())
    assert res.status == "ok" and "invalid_slots" not in res.detail
    assert len(repos.flows.investor_minutes(DAY, [("SYNK", "SYN0")])) == 12


def _day(fail_at: set[str]) -> tuple[Any, int]:
    """정규장 391분을 1분마다 돌린다(재시도 없음). fail_at 분은 KIS 가 429."""
    repos, server, clock, kis = setup(kst(2026, 11, 17, 9, 0, 5))
    t = kst(2026, 11, 17, 9, 0)
    for _ in range(391):
        clock.at = t + timedelta(seconds=5)
        as_of = t.strftime("%Y-%m-%dT%H:%M")
        if as_of in fail_at:
            server.inject("HTTP429", 1, tr_id="FHPTJ04030000")
        run(repos, kis, as_of, clock())
        t += timedelta(minutes=1)
    rows = repos.flows.investor_minutes(DAY, [("K2I", "F001")])
    return rows, len({r.ts for r in rows})


def test_full_session_has_no_empty_minute() -> None:
    _, minutes = _day(set())
    assert minutes == 391


def test_asleep_minutes_stay_empty_and_wake_writes_only_its_own_slot() -> None:
    """맥이 10:00~12:59 잠들었다가 13:00 에 깨어남 — 그 사이를 지금 누적으로 채우지 않는다."""
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 9, 0, 5))
    t = kst(2026, 11, 17, 9, 0)
    awake = [t + timedelta(minutes=i) for i in range(60)] + [kst(2026, 11, 17, 13, 0)]
    for slot in awake:
        clock.at = slot + timedelta(seconds=5)
        run(repos, kis, slot.strftime("%Y-%m-%dT%H:%M"), clock())
    got = sorted({r.ts for r in repos.flows.investor_minutes(DAY, [("K2I", "F001")])})
    assert got == awake  # 빈 180분은 행이 없다(화면이 앞뒤를 점선으로 잇는다)


def test_failed_minutes_stay_empty_exactly() -> None:
    fails = {at(9, 30), at(11, 7), at(14, 59)}
    rows, minutes = _day(fails)
    assert minutes == 391 - len(fails)
    got = {r.ts.strftime("%Y-%m-%dT%H:%M") for r in rows}
    assert not (got & fails)


def test_after_spot_close_tail_only_derivative_pairs_are_called() -> None:
    """ADR 0044 §2-1 — 15:41~15:55 는 tail_min 25 인 파생 조합(K2I 3·주식선물·위클리 4)만,
    현물은 15:40 까지."""
    repos, _s, clock, kis = setup(kst(2026, 11, 17, 15, 40, 5))
    run(repos, kis, at(15, 40), clock())
    assert kis.count("FHPTJ04030000") == 10  # 15:40 은 현물 포함 전부
    clock.at = kst(2026, 11, 17, 15, 50, 5)
    res = run(repos, kis, at(15, 50), clock())
    assert res.status == "ok", res.detail
    assert kis.count("FHPTJ04030000") == 10 + 8
    sent = {(c[1]["FID_INPUT_ISCD"], c[1]["FID_INPUT_ISCD_2"]) for c in kis.calls[10:]}
    assert ("KSP", "0001") not in sent and ("K2I", "F001") in sent
    rows = repos.flows.investor_minutes(DAY, K2I)
    assert kst(2026, 11, 17, 15, 50) in {r.ts for r in rows}


def test_pairs_at_uses_each_pair_tail() -> None:
    pairs = flows_minute.on_pairs(load_cfg().flows.minute.pairs)
    close = kst(2026, 11, 17, 15, 30)
    assert len(flows_minute.pairs_at(pairs, kst(2026, 11, 17, 15, 40), close)) == 10
    tail = {p.id for p in flows_minute.pairs_at(pairs, kst(2026, 11, 17, 15, 55), close)}
    assert tail == {
        "k200_fut", "k200_call", "k200_put", "stock_fut",
        "wkm_call", "wkm_put", "wki_call", "wki_put",
    }  # fmt: skip
    assert flows_minute.pairs_at(pairs, kst(2026, 11, 17, 15, 56), close) == []
