"""P4 저장소 규칙 시험 묶음 — 메모리(단위)와 Postgres(통합)가 **같은 시험**을 돈다.

`tests/unit/store/test_repos_p4_memory.py` 가 메모리로, `tests/integration/test_repos_p4_pg.py` 가
실제
TimescaleDB(0001~0013 적용)로 `CASES` 를 돌린다(docs/p4_design.md §1.1 시험 칸). 값은 모두 합성이다
(로그인 등급 실데이터 fixture 금지 — CLAUDE.md §2). 시계는 고정 시각.

이 파일 이름은 `test_` 로 시작하지 않아 pytest 가 직접 모으지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import pytest

from kbj.core import fin_rows as fr
from kbj.core.quality import Quality
from kbj.store.repos import Repos
from kbj.store.repos.consensus import with_perf

OK, EST, INV = Quality.OK, Quality.ESTIMATED, Quality.INVALID
NOW = datetime(2026, 11, 16, 1, 0, tzinfo=UTC)  # 10:00 KST
LATER = NOW + timedelta(hours=6)
JOB = "test.job"
CORP, CORP2 = "99400010", "99400020"
CODE, PREF, CODE2 = "994010", "994015", "994020"
D = date(2026, 11, 13)


def rc(n: int, day: date = D) -> str:
    return f"{day:%Y%m%d}{n:06d}"


def disc(n: int, *, corp: str = CORP, code: str | None = CODE, kind: str = "prelim",
         day: date = D, state: str = "pending", seen: datetime = NOW,
         title: str = "연결재무제표기준영업(잠정)실적(공정공시)") -> fr.DisclosureRow:  # fmt: skip
    return fr.DisclosureRow(
        rcept_no=rc(n, day), corp_code=corp, stock_code=code, corp_name="합성전자", corp_cls="Y",
        title=title, base_title=title.replace("[기재정정]", ""), filer="합성전자", rcept_dt=day,
        remark=None, kind=kind, is_correction=title.startswith("["), first_seen_at=seen,
        parse_state=state,
    )  # fmt: skip


def quarter(y: int, q: int, *, rcept: date | None, corp: str = CORP, fs: str = "CFS",
            revenue: int = 1_000_000_000_000) -> fr.QuarterRow:  # fmt: skip
    start = date(y, 3 * q - 2, 1)
    end = date(y + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1)
    return fr.QuarterRow(
        corp_code=corp, fiscal_year=y, fiscal_q=q, fs_div=fs, period_start=start,  # type: ignore[arg-type]
        period_end=end, rcept_dt=rcept, tier="full", revenue=revenue, op=revenue // 10,
        ni_owner=revenue // 20, eps_basic=1234.5, reported_3m={"revenue": revenue},
        diff3m_max_pct=0.0, notes=("합성",),
    )  # fmt: skip


# ── 공시 ─────────────────────────────────────────────────────────────────────────────────


def case_disclosure_upsert_keeps_first_seen_and_parse_state(r: Repos) -> None:
    f = r.filings
    assert f.upsert_disclosures([disc(1), disc(2, kind="other", state="none")],
                                loaded_by=JOB, now=NOW) == 2  # fmt: skip
    assert f.seen([rc(1), rc(2), rc(3)]) == {rc(1), rc(2)}
    assert f.mark_parsed(rc(1), "failed", note="표를 못 찾았다", now=NOW) == 1
    assert f.mark_parsed(rc(1), "failed", now=NOW) == 2
    # 같은 공시를 다시 훑어도(07:00 다시 훑기) 처음 본 시각·파싱 상태·시도 수는 그대로
    again = replace(disc(1, seen=LATER), title="연결재무제표기준영업(잠정)실적(공정공시) ")
    f.upsert_disclosures([again], loaded_by=JOB, now=LATER)
    row = f.disclosure(rc(1))
    assert row is not None
    assert row.first_seen_at == NOW and row.parse_state == "failed" and row.parse_tries == 2
    assert row.title.endswith(" ")  # 목록 칸은 덮는다
    assert [d.rcept_no for d in f.pending(kinds=["prelim"])] == [rc(1)]
    assert f.pending(max_tries=2) == []  # 시도 상한
    assert f.mark_parsed(rc(1), "na", note="3회 실패", now=NOW) == 2
    assert f.pending() == []
    with pytest.raises(KeyError):
        f.mark_parsed(rc(99), "done", now=NOW)
    with pytest.raises(ValueError):
        f.mark_parsed(rc(1), "weird", now=NOW)


def case_disclosure_reads_filter_and_order(r: Repos) -> None:
    f = r.filings
    rows = [
        disc(1), disc(2, kind="overhang", code=CODE2, corp=CORP2), disc(3, kind="periodic"),
        disc(4, day=date(2026, 11, 12), kind="periodic"),
    ]  # fmt: skip
    f.upsert_disclosures(rows, loaded_by=JOB, now=NOW)
    got = f.disclosures(start=D, end=D)
    assert [d.rcept_no for d in got] == [rc(3), rc(2), rc(1)]  # 최근 것부터
    assert [d.rcept_no for d in f.disclosures(start=D, end=D, stock_code=CODE2)] == [rc(2)]
    assert [
        d.rcept_no for d in f.disclosures(start=D, end=D, corp_code=CORP, kinds={"prelim"})
    ] == [rc(1)]
    assert len(f.disclosures(start=date(2026, 11, 1), end=D, limit=2)) == 2
    unloaded = f.fin_unloaded()
    assert [d.rcept_no for d in unloaded] == [rc(4, date(2026, 11, 12)), rc(3)]
    assert f.mark_fin_loaded([rc(3), rc(99)], now=NOW) == 1
    assert [d.rcept_no for d in f.fin_unloaded()] == [rc(4, date(2026, 11, 12))]
    assert f.set_scores({rc(1): (10, ["잠정"]), rc(99): (1, [])}) == 1
    scored = f.disclosure(rc(1))
    assert scored is not None and scored.kw_score == 10 and scored.kw_matched == ("잠정",)
    assert f.changed_corps(NOW) == {CORP, CORP2}
    assert f.changed_corps(LATER) == set()


def case_profiles_prelims_and_schedule(r: Repos) -> None:
    f = r.filings
    prof = fr.CorpProfile(CORP, CODE, 3, "26410", "Y", date(1990, 1, 1), corp_name="합성전자")
    f.upsert_profiles([prof], loaded_by=JOB, now=NOW)
    f.upsert_profiles([replace(prof, acc_mt=12)], loaded_by=JOB, now=NOW)
    assert f.profiles()[CORP].acc_mt == 12 and f.profiles([CORP2]) == {}
    pre = fr.PrelimRow(
        rcept_no=rc(1), corp_code=CORP, rcept_dt=D, scope=fr.PrelimScope.CONSOLIDATED,
        fiscal_year=2026, fiscal_q=3, is_annual=False, period_end=date(2026, 9, 30),
        period_inferred=False, unit_mult=100_000_000, revenue_cur=7_000_000_000_000,
        revenue_prev_y=6_000_000_000_000, op_cur=-120_000_000, eps_cur=-1.5,
        yoy_reported={"revenue": 16.7, "op": None}, notes=("본문 값과 0.2%p 차이",),
    )  # fmt: skip
    f.put_prelims([pre], loaded_by=JOB, now=NOW)
    got = f.prelim(rc(1))
    assert got == pre
    assert f.prelims(start=D, end=D, corp_code=CORP2) == []
    sched = [
        fr.ScheduleRow(CORP, date(2026, 9, 30), "statutory", date(2026, 11, 16), 2026, 3,
                       quality=EST),
        fr.ScheduleRow(CORP, date(2026, 9, 30), "pre_announce", date(2026, 11, 14), 2026, 3,
                       rcept_no=rc(5)),
        fr.ScheduleRow(CORP2, date(2026, 9, 30), "statutory", date(2026, 11, 16), 2026, 3,
                       quality=EST),
    ]  # fmt: skip
    f.replace_schedule([CORP, CORP2], sched, loaded_by=JOB, now=NOW)
    got_s = f.schedule(start=date(2026, 11, 1), end=date(2026, 11, 30))
    assert [(s.planned_date, s.corp_code, s.kind) for s in got_s] == [
        (date(2026, 11, 14), CORP, "pre_announce"),
        (date(2026, 11, 16), CORP, "statutory"),
        (date(2026, 11, 16), CORP2, "statutory"),
    ]
    # 정기보고서가 접수돼 그 기간 행이 사라지면 통째로 바꾼 결과에서도 사라진다
    f.replace_schedule([CORP], [], loaded_by=JOB, now=NOW)
    assert [s.corp_code for s in f.schedule(start=D, end=date(2026, 12, 31))] == [CORP2]
    with pytest.raises(ValueError, match="밖"):
        f.replace_schedule([CORP], sched[2:], loaded_by=JOB, now=NOW)


def case_overhang_state_is_replaced_per_day(r: Repos) -> None:
    f = r.filings
    ev = fr.OverhangEvent(
        rc(1, date(2026, 3, 2)), CORP, date(2026, 3, 2), fr.Instrument.CB, 30_000_000_000,
        10_000.0, 3_000_000, 7_000.0, date(2027, 3, 2), date(2029, 2, 2), date(2029, 3, 2),
        None, "공모", "api",
    )  # fmt: skip
    adj = fr.OverhangAdjust(
        rc(2, date(2026, 6, 1)), CORP, date(2026, 6, 1), ev.rcept_no, "price_adjust", 8_000.0,
        None, None, date(2026, 6, 2),
    )  # fmt: skip
    lost = fr.OverhangAdjust(
        rc(3, date(2026, 6, 3)), CORP, date(2026, 6, 3), None, "exercise", None, 1, 1, None,
        note="대상을 잇지 못했다", quality=INV,
    )  # fmt: skip
    f.put_overhang_events([ev], loaded_by=JOB, now=NOW)
    f.put_overhang_adjusts([adj, lost], loaded_by=JOB, now=NOW)
    assert f.overhang_events(CORP) == [ev] and f.overhang_events(start=D) == []
    assert [a.rcept_no for a in f.overhang_adjusts(CORP)] == [adj.rcept_no, lost.rcept_no]
    st = fr.OverhangState(CORP, D, ev.rcept_no, fr.Instrument.CB, 30_000_000_000, 8_000.0,
                          3_750_000, True, False)  # fmt: skip
    sm = fr.OverhangSummary(CORP, D, 3_750_000, 0, 0, 50_000_000, "dart_share_count", 7.5, 0.0)
    assert f.put_overhang_state(CORP, D, [st], sm, loaded_by=JOB, now=NOW) == 2
    nxt = date(2026, 11, 16)
    st2 = replace(st, as_of=nxt, exercisable=False, active=False, potential_shares=0)
    f.put_overhang_state(CORP, nxt, [st2], None, loaded_by=JOB, now=NOW)
    assert f.overhang_state(CORP) == [st2]  # 가장 최근 as_of
    assert f.overhang_state(CORP, D) == [st]
    assert f.overhang_summary(CORP) == sm  # 최근 요약은 D 뿐
    assert f.overhang_summary(CORP, nxt) is None
    assert f.overhang_summaries(D) == {CORP: sm}
    f.put_overhang_state(CORP, D, [], None, loaded_by=JOB, now=NOW)  # 다시 계산 → 사라짐
    assert f.overhang_state(CORP, D) == [] and f.overhang_summary(CORP, D) is None
    with pytest.raises(ValueError, match="다른"):
        f.put_overhang_state(CORP2, nxt, [st2], None, loaded_by=JOB, now=NOW)
    assert f.overhang_state(CORP2) == []


def case_insiders_buybacks_and_share_counts(r: Repos) -> None:
    f = r.filings
    reps = [
        fr.InsiderReport(rc(i), CORP, D - timedelta(days=10 * i), f"보고자{i}", "이사", True,
                         False, 1000 - i, -100 * i, 1.0, -0.1, reason="장내매도")
        for i in range(1, 4)
    ]  # fmt: skip
    f.put_insider_reports(reps, loaded_by=JOB, now=NOW)
    assert [x.rcept_no for x in f.insider_reports(CORP, start=D - timedelta(days=20))] == [
        rc(1), rc(2)
    ]  # fmt: skip
    major = fr.MajorHolderReport(rc(9), CORP, D, "합성운용", "일반", 5_000_000, 100_000, 5.1, 0.1)
    f.put_major_holder_reports([major], loaded_by=JOB, now=NOW)
    assert f.major_holder_reports(CORP) == [major]
    wins = [fr.InsiderWindow(CORP, D, d, -300, 0, 300, 2, 2, -0.1, 100.0) for d in (90, 30)]
    f.put_insider_windows(CORP, D, wins, loaded_by=JOB, now=NOW)
    assert [w.days for w in f.insider_windows(CORP)] == [30, 90]
    assert [w.corp_code for w in f.insider_windows_on(D, 30)] == [CORP]
    bb = fr.BuybackEvent(rc(7), CORP, D, "acquire", 100_000, 5_000_000_000, D,
                         date(2027, 2, 12), "장내매수")  # fmt: skip
    f.put_buybacks([bb], loaded_by=JOB, now=NOW)
    assert f.buybacks(CORP) == [bb]  # start·end ↔ start_date·end_date
    sc1 = fr.ShareCount(CORP, date(2026, 6, 30), rc(1, date(2026, 8, 14)), 10_000_000, 0,
                        100_000, 0, rcept_dt=date(2026, 8, 14))  # fmt: skip
    sc2 = fr.ShareCount(CORP, date(2026, 9, 30), rc(1), 10_500_000, 0, 100_000, 0, rcept_dt=D)
    f.put_share_counts([sc2, sc1], loaded_by=JOB, now=NOW)
    assert f.share_counts(CORP) == [sc1, sc2]
    assert f.latest_share_count(CORP) == sc2
    assert f.latest_share_count(CORP, upto=date(2026, 11, 1)) == sc1  # PIT
    assert f.latest_share_count(CORP2) is None


# ── 재무 ─────────────────────────────────────────────────────────────────────────────────


def case_fin_raw_quarters_pit_and_ratios(r: Repos) -> None:
    fin = r.fin
    raw = fr.StatementRaw(
        CORP, 2026, "11014", "CFS", rc(1),
        ({"account_id": "ifrs-full_Revenue", "thstrm_amount": "1000", "ord": "1"},),
    )  # fmt: skip
    fin.put_raw(raw, loaded_by=JOB, now=NOW)
    fin.put_raw(replace(raw, rows=(*raw.rows, {"account_id": "x"})), loaded_by=JOB, now=NOW)
    got = fin.raw(CORP, 2026, "11014", "CFS")
    assert got is not None and got.n_rows == 2 and got.rows[0]["thstrm_amount"] == "1000"
    assert fin.raw(CORP, 2026, "11014", "OFS") is None
    assert fin.raw_keys(CORP) == {(2026, "11014", "CFS")}
    q2 = quarter(2026, 2, rcept=date(2026, 8, 14))
    q3 = quarter(2026, 3, rcept=D)
    unknown = quarter(2026, 1, rcept=None)
    ofs = quarter(2026, 3, rcept=D, fs="OFS")
    fin.upsert_quarters([q3, q2, unknown, ofs], loaded_by=JOB, now=NOW)
    assert [(q.fiscal_q, q.fs_div) for q in fin.quarters(CORP)] == [
        (1, "CFS"), (2, "CFS"), (3, "CFS"), (3, "OFS"),
    ]  # fmt: skip
    assert fin.quarters(CORP, fs_div="CFS", upto_rcept=date(2026, 11, 12)) == [q2]  # PIT
    assert fin.quarters(CORP, fs_div="CFS", upto_rcept=D) == [q2, q3]
    assert fin.quarters(CORP2) == [] and fin.quarter_corps() == {CORP}
    assert fin.quarters(CORP, fs_div="CFS")[2] == q3  # 왕복 같은 값(int 원·float·jsonb)
    ratio = fr.RatioRow(CORP, 2026, 3, "CFS", ttm_revenue=4_000_000_000_000, opm=10.0,
                        roe=None, na_reasons={"roe": "capital_impairment"})  # fmt: skip
    fin.upsert_ratios([ratio], loaded_by=JOB, now=NOW)
    assert fin.ratios(CORP) == [ratio]


def case_main_tier_never_overwrites_full_tier(r: Repos) -> None:
    """주요계정 층(main) 행은 같은 키의 전체 재무제표 층(full) 행을 덮지 않는다(D-P4-5 — 상세 대상도
    주요계정 묶음에 들어 있을 수 있다). full 은 main 을 덮고, main 은 main 을 덮는다."""
    fin = r.fin
    full = replace(quarter(2026, 3, rcept=D), d_and_a=40_000_000_000)
    main = replace(quarter(2026, 3, rcept=D, revenue=999_000_000_000), tier="main", d_and_a=None)
    assert fin.upsert_quarters([full], loaded_by=JOB, now=NOW) == 1
    fin.upsert_quarters([main], loaded_by=JOB, now=LATER)
    (got,) = fin.quarters(CORP)
    assert got == full  # D&A 가 지워지지 않았다
    # main → main 은 덮고, full 은 main 을 덮는다
    m2 = replace(quarter(2026, 2, rcept=date(2026, 8, 14)), tier="main")
    m2b = replace(m2, revenue=7)
    f2 = replace(quarter(2026, 2, rcept=date(2026, 8, 14), revenue=8), d_and_a=1)
    fin.upsert_quarters([m2], loaded_by=JOB, now=NOW)
    fin.upsert_quarters([m2b], loaded_by=JOB, now=NOW)
    assert fin.quarters(CORP)[0] == m2b
    fin.upsert_quarters([f2], loaded_by=JOB, now=NOW)
    assert fin.quarters(CORP)[0] == f2
    # 한 번에 섞어 넣어도 같은 규칙(full 다음 main → full 이 남는다)
    other = replace(quarter(2026, 1, rcept=date(2026, 5, 15)), d_and_a=5)
    fin.upsert_quarters([other, replace(other, tier="main", d_and_a=None)], loaded_by=JOB, now=NOW)
    assert fin.quarters(CORP)[0] == other


def case_reports_and_segments(r: Repos) -> None:
    fin = r.fin

    def rep(day: date, payload: dict[str, object]) -> fr.ReportRow:
        return fr.ReportRow(
            CODE, day, CORP, 2023, 2026, "CFS", payload, b"PK\x03\x04" + day.isoformat().encode(),
            ("경고 하나",), "dr-test", f"digest-{day}", NOW,
        )  # fmt: skip

    days = [date(2026, 11, d) for d in (10, 11, 12, 13, 16)]
    for d in days:
        fin.put_report(rep(d, {"blocks": {"day": d.isoformat()}}), loaded_by=JOB)
    latest = fin.report(CODE)
    assert latest is not None and latest.built_on == days[-1]
    assert latest.payload == {"blocks": {"day": "2026-11-16"}} and latest.xlsx is not None
    assert latest.xlsx_sha256 == rep(days[-1], {}).xlsx_sha256
    assert fin.report(CODE, built_on=days[0]) is not None
    assert fin.prune_reports(keep=4) == 1
    assert fin.report_dates(CODE) == days[1:]
    assert fin.report(CODE2) is None
    seg = fr.SegmentRow(CORP, 2025, "반도체", {"revenue": 1_000_000}, rc(1))
    fin.put_segments([seg], loaded_by=JOB, now=NOW)
    assert fin.segments(CORP) == [seg] and fin.segments(CORP, 2024) == []
    assert seg.quality is EST


# ── 컨센서스·밸류(로그인) ──────────────────────────────────────────────────────────────────


def case_consensus_and_derived_replace(r: Repos) -> None:
    c = r.consensus
    snaps = [
        fr.ConsensusRow(CODE, date(2026, 11, d), "202612", True, 10**12, 10**11, 8 * 10**10,
                        8 * 10**10, 1500.0, extra={"roe": 12.0})
        for d in (6, 13)
    ]  # fmt: skip
    c.put_snapshot(snaps, loaded_by=JOB, now=NOW)
    assert c.snapshots(CODE, date(2026, 11, 1), date(2026, 11, 30)) == snaps
    ops = [
        fr.OpinionRow(CODE, "합성증권", date(2026, 11, 2), "매수", "매수", 90_000.0),
        fr.OpinionRow(CODE, "가상증권", date(2026, 11, 3), "중립", None, None),
    ]
    c.put_opinions(ops, loaded_by=JOB, now=NOW)
    assert [o.broker for o in c.opinions(CODE, date(2026, 11, 1), D)] == ["합성증권", "가상증권"]
    t = fr.TargetConsensus(CODE, D, 90_000.0, 90_000.0, 90_000.0, 90_000.0, 1, 90, quality=EST)
    c.put_targets([t], loaded_by=JOB, now=NOW)
    assert c.target(CODE) == t and c.target(CODE2) is None
    revs = [
        fr.RevisionRow(CODE, D, "op", "202612", 7, 1.1e11, 1.0e11, date(2026, 11, 6), 10.0, "UP"),
        fr.RevisionRow(CODE, D, "eps", "202612", 30, -1.0, 1.0, date(2026, 10, 13), None, "TURN"),
    ]
    c.put_revisions(CODE, D, revs, loaded_by=JOB, now=NOW)
    c.put_revisions(CODE, D, revs[:1], loaded_by=JOB, now=NOW)  # 같은 as_of 다시 → 통째로
    assert c.revisions(CODE) == revs[:1] and c.revisions_on(D) == revs[:1]
    with pytest.raises(ValueError, match="섞였다"):
        c.put_revisions(CODE2, D, revs, loaded_by=JOB, now=NOW)


CASES_SCN: tuple[tuple[Literal["bear", "base", "bull"], float, float], ...] = (
    ("bull", 1.2, 12.0),
    ("bear", 0.7, 9.0),
    ("base", 1.0, 10.0),
)


def case_valuation_bands_and_scenarios(r: Repos) -> None:
    c = r.consensus
    days = [
        fr.ValuationDay(CODE, date(2026, 11, d), 10**13, 50_000.0, "2026Q3:CFS", per=12.5,
                        pbr=1.1, ev=11 * 10**12, flags={"na": {"psr": "no_revenue"}})
        for d in (12, 13)
    ]  # fmt: skip
    c.put_valuation_days(days, loaded_by=JOB, now=NOW)
    c.put_valuation_days([replace(days[1], per=13.0)], loaded_by=JOB, now=NOW)
    got = c.valuation_days(CODE, date(2026, 11, 1), D)
    assert [v.per for v in got] == [12.5, 13.0] and got[0].flags == days[0].flags
    assert c.last_valuation_day(CODE) == D and c.last_valuation_day(CODE2) is None
    band = fr.BandRow(CODE, D, "per", 8.0, 9.0, 10.0, 12.0, 14.0, 6.0, 20.0, 1240, 3, 13.0,
                      70.0, date(2021, 11, 15))  # fmt: skip
    short = fr.BandRow(CODE, D, "pbr", None, None, None, None, None, None, None, 20, 0, 1.1,
                       None, None, na_reason=fr.NaReason.SHORT_HISTORY)  # fmt: skip
    c.put_bands(CODE, D, [band, short], loaded_by=JOB, now=NOW)
    assert c.bands(CODE) == [short, band]  # metric 순(pbr < per)
    assert c.bands(CODE, date(2026, 11, 12)) == []
    sc = [
        fr.ScenarioRow(CODE, D, case, "PER", 5000.0 * m, "CONSENSUS", mult, "5Y_P50",
                       5000.0 * m * mult, 10.0 * m, 50_000.0)
        for case, m, mult in CASES_SCN
    ]  # fmt: skip
    c.put_scenarios(CODE, D, sc, loaded_by=JOB, now=NOW)
    assert [s.case for s in c.scenarios(CODE)] == ["bear", "base", "bull"]


def case_surprises_perf_and_share_classes(r: Repos) -> None:
    c = r.consensus
    s1 = fr.SurpriseRow(rc(1), CODE, CORP, "2026Q3", fr.PrelimScope.CONSOLIDATED, "YOY_BIG_UP", 1,
                        op_yoy=25.0, alerted_at=NOW, notify_ticket="alert.earnings:k")  # fmt: skip
    s2 = fr.SurpriseRow(rc(2), CODE2, CORP2, "2026Q3", fr.PrelimScope.STANDALONE, "YOY_INLINE", 5)
    c.put_surprises([s1, s2], loaded_by=JOB, now=NOW)
    assert c.surprises_known([rc(1), rc(3)]) == {rc(1)}
    assert c.surprise(rc(2)) == s2 and c.surprises(corp_code=CORP) == [s1]
    assert c.perf_pending() == [s1]
    done = with_perf(s1, price_at_alert=50_000.0, perf=(1.5, None, 2.0), done=True)
    c.put_surprises([done], loaded_by=JOB, now=LATER)
    assert c.perf_pending() == [] and c.surprise(rc(1)) == done
    classes = [
        fr.ShareClass(CODE, CORP, "common", CODE),
        fr.ShareClass(PREF, None, "pref", CODE),
    ]
    c.put_share_classes(classes, loaded_by=JOB, now=NOW)
    assert c.share_classes() == {CODE: classes[0], PREF: classes[1]}
    assert c.share_classes([PREF]) == {PREF: classes[1]} and c.share_classes([]) == {}


# ── 관심종목(개인) ─────────────────────────────────────────────────────────────────────────


def case_watchlist_is_replaced_whole(r: Repos) -> None:
    j = r.journal
    items = [
        fr.WatchItem("main", CODE2, "합성둘", NOW, 1, source="import:sd"),
        fr.WatchItem("main", CODE, "합성하나", None, 0, note="메모", source="import:sd"),
    ]
    assert j.replace_watchlist("main", items, loaded_by=JOB, now=NOW) == 2
    assert [w.code for w in j.watchlist()] == [CODE, CODE2]  # sort 순
    assert j.watch_codes() == {CODE, CODE2} and j.watchlist("other") == []
    j.replace_watchlist("main", items[:1], loaded_by=JOB, now=LATER)
    assert j.watch_codes("main") == {CODE2}
    with pytest.raises(ValueError, match="두 번"):
        j.replace_watchlist("main", [items[0], items[0]], loaded_by=JOB, now=NOW)
    with pytest.raises(ValueError, match="다른 목록"):
        j.replace_watchlist("other", items, loaded_by=JOB, now=NOW)
    with pytest.raises(ValueError, match="naive"):
        j.replace_watchlist("main", items, loaded_by=JOB, now=NOW.replace(tzinfo=None))
    assert j.watch_codes() == {CODE2}  # 실패한 바꾸기는 아무것도 바꾸지 않는다


def case_writes_need_loader_and_aware_time(r: Repos) -> None:
    with pytest.raises(ValueError, match="loaded_by"):
        r.filings.upsert_disclosures([disc(1)], loaded_by=" ", now=NOW)
    with pytest.raises(ValueError, match="naive"):
        r.fin.upsert_quarters([quarter(2026, 3, rcept=D)], loaded_by=JOB,
                              now=NOW.replace(tzinfo=None))  # fmt: skip
    with pytest.raises(ValueError, match="loaded_by"):
        r.consensus.put_share_classes([], loaded_by="", now=NOW)
    assert r.filings.disclosure(rc(1)) is None


CASES: list[Callable[[Repos], None]] = [
    case_disclosure_upsert_keeps_first_seen_and_parse_state,
    case_disclosure_reads_filter_and_order,
    case_profiles_prelims_and_schedule,
    case_overhang_state_is_replaced_per_day,
    case_insiders_buybacks_and_share_counts,
    case_fin_raw_quarters_pit_and_ratios,
    case_main_tier_never_overwrites_full_tier,
    case_reports_and_segments,
    case_consensus_and_derived_replace,
    case_valuation_bands_and_scenarios,
    case_surprises_perf_and_share_classes,
    case_watchlist_is_replaced_whole,
    case_writes_need_loader_and_aware_time,
]
CASE_IDS = [c.__name__.removeprefix("case_") for c in CASES]
