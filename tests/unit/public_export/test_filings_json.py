"""P4 공시 목록 공개 파일 — filings_feed·earnings_prelim·overhang_recent·insider_recent·
earnings_schedule.

합성 공개 표(`pub_rows`)를 같은 `Query` 이름의 메모리 읽기로 준다(DB 없음 — SQL 자체의 Postgres
대조는 통합 시험 몫). 핵심 단언: 원천은 DART 하나, 상장사만, 관심종목·시세 칸 0,
기간·정렬·as_of·품질 규칙.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest

from kbj.config.fin import load_fin
from kbj.core import fin_rows as fr
from kbj.engines.filings import earnings as engine_earnings
from kbj.services.public_export.corp_index import load_corps
from kbj.services.public_export.filings_json import (
    DART_VIEWER,
    FEED_NAME,
    INSIDER_NAME,
    NAMES,
    OVERHANG_NAME,
    PRELIM_NAME,
    SCHEDULE_NAME,
    FilingsOut,
    build_filings,
    pct_change,
    prelim_label,
)
from kbj.services.public_export.manifest import (
    DART_ONLY_NAMES,
    PublicFile,
    check_payload,
    non_dart_source_paths,
    watch_trace_paths,
)
from tests.unit.public_export.pub_rows import (
    BIO,
    CODES,
    DUP_CODE,
    ELEC,
    HOLD,
    NOW,
    SEEN,
    MemReader,
    build_world,
    reader,
)

CFG = load_fin()


def _build(r: MemReader | None = None, now: Any = NOW) -> FilingsOut:
    rd = r or reader()
    return build_filings(rd, load_corps(rd), now=now, cfg=CFG)


def _data(out: FilingsOut, name: str) -> dict[str, Any]:
    f = next(f for f in out.files if f.name == name)
    return json.loads(f.render(NOW))


@pytest.fixture(scope="module")
def out() -> FilingsOut:
    return _build()


def test_five_files_dart_only_and_clean(out: FilingsOut) -> None:
    assert [f.name for f in out.files] == list(NAMES)
    assert set(NAMES) <= DART_ONLY_NAMES
    assert out.skipped == ()
    for f in out.files:
        text = f.render(NOW)
        assert f.source == "DART"
        assert check_payload(f.name, text) == [], f.name
        raw = json.loads(text)
        assert non_dart_source_paths(raw) == []
        assert watch_trace_paths(raw) == []  # 관심종목 흔적 없음
        for word in ("is_watch", "login_score", "importance", "amount", "itm", "signal", "KRX"):
            assert f'"{word}"' not in text, (f.name, word)


def test_feed_two_filing_days_listed_only(out: FilingsOut) -> None:
    env = _data(out, FEED_NAME)
    d = env["data"]
    # 월요일 05:30 — 그날 공시는 아직 없다: 공시가 있는 최근 2일
    assert d["dates"] == ["2026-11-13", "2026-11-12"]
    codes = {r["stock_code"] for r in d["rows"]}
    assert codes == {CODES[ELEC], CODES[BIO], CODES[HOLD]}  # 비상장·겹친 코드 없음
    assert DUP_CODE not in json.dumps(env)
    assert d["n_total"] == len(d["rows"]) == 4
    assert d["by_kind"] == {"other": 1, "overhang": 1, "prelim": 1, "supply": 1}
    # 처음 본 시각이 늦은 것부터(같은 날 안)
    first13 = [r["first_seen_at"] for r in d["rows"] if r["rcept_dt"] == "2026-11-13"]
    assert first13 == sorted(first13, reverse=True)
    top = d["rows"][0]
    assert top["kw_score"] == 8 and top["kw_level"] == "high"
    assert top["link"] == DART_VIEWER + top["rcept_no"]
    hold = next(r for r in d["rows"] if r["stock_code"] == CODES[HOLD])
    assert hold["kw_score"] is None and hold["kw_level"] is None  # 점수 매기기 전은 null
    assert next(r for r in d["rows"] if r["kind"] == "prelim")["is_correction"] is True
    assert env["as_of"] == SEEN.isoformat()
    assert env["quality"] == "ok"


def test_feed_level_follows_config_thresholds() -> None:
    lv = CFG.filings.importance_levels
    assert (lv.level(10), lv.level(6), lv.level(4), lv.level(2)) == (
        "critical",
        "high",
        "medium",
        "low",
    )


def test_stale_after_seven_days() -> None:
    later = NOW + timedelta(days=8)
    out = _build(now=later)
    for f in out.files:
        if f.name in (FEED_NAME, PRELIM_NAME, OVERHANG_NAME):
            assert f.quality == "stale", f.name
            assert any("7일" in n for n in f.notes)


def test_no_listed_filings_makes_no_files() -> None:
    w = build_world()
    w.repos.filings.disclosure_t.rows.clear()
    out = _build(MemReader(w))
    assert out.files == ()
    assert len(out.skipped) == len(NAMES)
    assert all("만들지 않았다" in s for s in out.skipped)


def test_prelims_window_values_and_yoy(out: FilingsOut) -> None:
    d = _data(out, PRELIM_NAME)["data"]
    assert (d["from"], d["to"]) == ("2026-11-03", "2026-11-16")
    rows = d["rows"]
    assert [r["rcept_dt"] for r in rows] == ["2026-11-13", "2026-11-10"]  # 기간 밖·비상장 없음
    fix, orig = rows
    assert fix["is_correction"] is True and orig["is_correction"] is False
    assert orig["revenue_cur"] == 1_200_000_000_000  # 원(본문 단위 × 배수)
    assert orig["rev_yoy"] == pytest.approx(20.0)
    assert orig["op_yoy"] == pytest.approx(700.0)  # 전년 적자 → 분모 |전년|
    assert orig["rev_qoq"] == pytest.approx(100 / 11)
    assert orig["op_qoq"] == pytest.approx(20.0)
    assert orig["eps_cur"] == pytest.approx(812.5)
    assert orig["yoy_reported"] == {"revenue": 20.0}
    assert (orig["period"], orig["label"], orig["market"]) == ("2026Q3", "2026 3Q", "KOSPI")
    assert orig["notes"] == ["합성"]


@pytest.mark.parametrize(
    ("cur", "base"), [(120, 100), (60, -10), (5, 0), (None, 3), (3, None), (-5, -10)]
)
def test_pct_change_matches_engine(cur: int | None, base: int | None) -> None:
    assert pct_change(cur, base) == engine_earnings.pct_change(cur, base)


@pytest.mark.parametrize(
    ("q", "end", "annual"),
    [
        (3, date(2026, 9, 30), False),
        (2, date(2026, 9, 30), False),  # 3월 결산 2분기(7~9월)
        (1, date(2026, 6, 30), False),
        (4, date(2025, 12, 31), True),
        (1, date(2026, 6, 15), False),  # 월말이 아닌 이상한 행
    ],
)
def test_prelim_label_matches_engine(q: int, end: date, annual: bool) -> None:
    row = fr.PrelimRow(
        rcept_no="20261113000001",
        corp_code=ELEC,
        rcept_dt=date(2026, 11, 13),
        scope=fr.PrelimScope.CONSOLIDATED,
        fiscal_year=end.year,
        fiscal_q=q,
        is_annual=annual,
        period_end=end,
        period_inferred=False,
        unit_mult=1,
    )
    assert prelim_label(end.year, q, end, is_annual=annual) == engine_earnings.period_label(row)


def test_overhang_last_30_days_with_latest_ratio(out: FilingsOut) -> None:
    d = _data(out, OVERHANG_NAME)["data"]
    assert d["days"] == 30
    rows = d["rows"]
    assert [r["rcept_dt"] for r in rows] == ["2026-11-13", "2026-11-05"]  # 1월 결정은 기간 밖
    assert {r["ratio_pct"] for r in rows} == {6.25}  # 최근 요약(11-13)
    assert rows[0]["instrument"] == "cb" and rows[0]["shares"] == 2_000_000
    assert rows[0]["price"] == pytest.approx(5000.0)


def test_insider_ranked_by_abs_ratio_and_fresh_only(out: FilingsOut) -> None:
    env = _data(out, INSIDER_NAME)
    d = env["data"]
    assert d["label"] == "보고 기준 순증감(사유 미구분 포함)"
    assert [r["stock_code"] for r in d["rows"]] == [CODES[BIO], CODES[ELEC]]
    # 보고 0(합성홀딩스)·7일 넘은 창(합성글로벌)은 없다
    assert d["n_total"] == 2
    assert d["rows"][0]["ratio_pct"] == pytest.approx(-0.78)
    assert d["rows"][1]["as_of"] == "2026-11-13"  # 회사별 최신 창
    assert all('"reporter"' not in json.dumps(r) for r in d["rows"])  # 보고자 이름 칸 없음
    assert env["quality"] == "estimated"  # 합성전자 창이 추정


def test_schedule_next_30_days_listed_only(out: FilingsOut) -> None:
    d = _data(out, SCHEDULE_NAME)["data"]
    assert (d["from"], d["to"]) == ("2026-11-16", "2026-12-15")
    got = [(r["stock_code"], r["planned_date"], r["kind"]) for r in d["rows"]]
    assert got == [
        (CODES[ELEC], "2026-11-16", "statutory"),
        (CODES[BIO], "2026-11-20", "pre_announce"),
    ]


def test_public_files_are_public_file_instances(out: FilingsOut) -> None:
    for f in out.files:
        assert isinstance(f, PublicFile)
        assert f.compact  # 공백 없는 JSON(크기)
        assert "\n " not in f.render(NOW)
