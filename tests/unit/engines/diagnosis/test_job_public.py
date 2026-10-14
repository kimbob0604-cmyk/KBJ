"""`diag.public`(kbj.services.engine.diag_pub) — 메모리 공개 저장소 + p7_world 합성 입력.

공개 표만 쓴다·시점 입력만(재계산 같음)·이벤트 갈아 끼우기·수출 속보 없음 사유·실적 집중 문턱.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from kbj.config.diag import load_diag
from kbj.config.markets import load_calendar_events
from kbj.core.calendar import TradingCalendar
from kbj.core.p5_rows import MacroSeriesRow
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.core.trade_rows import TenDayRow
from kbj.engines.diagnosis.public import READY_EXPORT
from kbj.services.engine import diag_pub
from kbj.services.engine.diag_pub import KTB3Y, compute_public
from kbj.services.scheduler.handlers import JobContext
from kbj.store.repos.memory import memory_repos
from kbj.store.repos.trade import MemoryTradeRepo
from tests.unit.engines.diagnosis.diag_seed import seed_public, world

CFG = load_diag()
CAL = TradingCalendar.default()
DAY = date(2026, 12, 10)
AT = datetime(2026, 12, 10, 8, 40, tzinfo=KST)


def _seeded():
    r = memory_repos()
    seed_public(r, world(), received_at=AT)
    return r


def _ctx(repos: object, now: datetime = AT) -> JobContext:
    return JobContext(
        job="diag.public", as_of=DAY.isoformat(), run_id="t", attempt=1, now=now,
        resources={"repos": repos, "kr": CAL, "diag_config": CFG},
    )  # fmt: skip


def test_run_writes_public_tables_only() -> None:
    r = _seeded()
    res = diag_pub.run(_ctx(r))
    assert res.status == "ok" and res.rows and res.rows > 0
    rows = r.diag_pub.indicators_on(DAY)
    assert [x.key for x in rows] == sorted(CFG.keys("public")) or {x.key for x in rows} == set(
        CFG.keys("public")
    )
    assert all(x.source == "KBJ:diag.public" for x in rows)
    g = r.diag_pub.gauge_on(DAY)
    assert g is not None and g.label in ("risk_on", "neutral", "risk_off", "hold")
    # 로그인 표는 건드리지 않는다
    assert r.diag.indicators_on(DAY) == [] and r.diag.verdict_on(DAY) is None


def test_point_in_time_and_lag() -> None:
    r = _seeded()
    out = compute_public(r.public(), None, day=DAY, cutoff=AT, cfg=CFG, cal=CAL,
                         mpc=load_calendar_events())  # fmt: skip
    by = {x.key: x for x in out.indicators}
    # ECOS 그날 18:00 공표 — 08:40 엔 전 거래일 값(정상 — 기대도 전 거래일이라 지연 아님)
    assert by["credit_spread"].inputs["obs_day"] == "2026-12-09"
    assert not any("공표 지연" in x for x in by["credit_spread"].reasons)
    # 수출 속보 표가 없으면 '준비 중'
    assert by["export_momentum"].value is None and READY_EXPORT in by["export_momentum"].reasons


def test_recompute_same_after_new_data() -> None:
    r = _seeded()
    mpc = load_calendar_events()
    first = compute_public(r.public(), None, day=DAY, cutoff=AT, cfg=CFG, cal=CAL, mpc=mpc)
    # 그 뒤에 공표된 값(그날 18:00 국고 3년)이 들어와도 같은 cutoff 로 다시 돌리면 같다
    late = datetime(2026, 12, 10, 18, 0, tzinfo=KST)
    r.macro.upsert_series(
        [MacroSeriesRow(series_id=KTB3Y, period="2026-12-10", value=Decimal("9.99"), unit="%",
                        source="ECOS", as_of=late, quality=Quality.OK, released_at=late)],
        loaded_by="t", received_at=late,
    )  # fmt: skip
    again = compute_public(r.public(), None, day=DAY, cutoff=AT, cfg=CFG, cal=CAL, mpc=mpc)
    assert again == first
    after = compute_public(r.public(), None, day=DAY, cutoff=late, cfg=CFG, cal=CAL, mpc=mpc)
    rd = {x.key: x for x in after.indicators}["rate_direction"]
    assert rd.inputs["obs_day"] == "2026-12-10"


def test_events_replace_and_earnings_threshold() -> None:
    r = _seeded()
    diag_pub.run(_ctx(r))
    ev = r.diag_pub.events_between(date(2026, 12, 1), date(2027, 1, 31))
    kinds = {(e.date, e.kind) for e in ev}
    assert (date(2026, 12, 10), "expiry") in kinds
    assert (date(2026, 12, 11), "earnings_cluster") in kinds
    assert (date(2026, 12, 15), "earnings_cluster") not in kinds  # 9종목 — 문턱 아래
    assert not any(k == "export_flash" for _, k in kinds)  # 수출 속보 표 없음
    # 다시 돌려도 같은 이벤트(갈아 끼우기 — 겹치지 않는다)
    diag_pub.run(_ctx(r))
    assert r.diag_pub.events_between(date(2026, 12, 1), date(2027, 1, 31)) == ev


def _ten(ym: str, seg: int, usd: int, seen: datetime) -> TenDayRow:
    return TenDayRow(
        kind="exp_item", ym=ym, seg=seg, col_no=0, name="총계", usd=Decimal(usd),
        first_usd=Decimal(usd), first_seen_at=seen,
        span_text={1: "01~10", 2: "01~20", 3: "01~30"}[seg], source="DATAGO:15101609",
        as_of=seen, quality=Quality.OK,
    )  # fmt: skip


def test_export_momentum_from_flash_yoy() -> None:
    r = _seeded()
    t = MemoryTradeRepo()
    seen = datetime(2026, 12, 1, 9, 20, tzinfo=KST)
    rows = [
        _ten("2025-11", 3, 100, datetime(2025, 12, 1, 9, 0, tzinfo=KST)),
        _ten("2026-11", 3, 112, seen),
    ]
    t.upsert_ten_day(rows, loaded_by="t", received_at=seen)
    out = compute_public(r.public(), t, day=DAY, cutoff=AT, cfg=CFG, cal=CAL,
                         mpc=load_calendar_events())  # fmt: skip
    em = {x.key: x for x in out.indicators}["export_momentum"]
    assert em.value == Decimal("12.0000") and em.quality is not Quality.OK
    assert "이력 부족" in " ".join(em.reasons)
    # 이벤트 창(그날 −7일 ~ +30일)의 공표일 — 아직 안 본 구간은 seen_at 이 없다
    flash = [e for e in out.events if e.kind == "export_flash"]
    assert [e.date for e in flash] == [date(2026, 12, 11), date(2026, 12, 21), date(2027, 1, 1)]
    assert all("seen_at" not in e.detail for e in flash)
