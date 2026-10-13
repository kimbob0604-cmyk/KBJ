"""`fin/<코드>.json`·`reports/<코드>.json` — 상장사 DART 재무 공개 파일(docs/p4_design.md §7·§8.6).

핵심 단언(§8.6): `prv_` 읽기 0(질의 이름·SQL — `test_sql_schemas.py`), `source` 전부 DART, 크기 상한
초과 → 실패(`test_export.py`), 관심종목 흔적 0. 재무 줄 고르기·합·YoY 는 로그인
API(`readers/fin.py`)와 같은 결과인지 같은 행으로 대조한다(이 패키지는 API 를 import 하지 못한다 —
계약 ⑧).
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from kbj.config.fin import load_fin
from kbj.core.time import KST
from kbj.engines.financials.ratios import series as engine_series
from kbj.services.api.readers.fin import (
    _annual_all,  # pyright: ignore[reportPrivateUsage]
    _annual_lines,  # pyright: ignore[reportPrivateUsage]
    _quarter_lines,  # pyright: ignore[reportPrivateUsage]
    _ratio,  # pyright: ignore[reportPrivateUsage]
    _rows,  # pyright: ignore[reportPrivateUsage]
)
from kbj.services.public_export.corp_index import corps_from_rows, load_corps
from kbj.services.public_export.fin_json import (
    FILING_COLUMNS,
    FIN_ROW_COLUMNS,
    Q_QUARTERS,
    Q_REPORTS,
    FinOut,
    annual_all,
    annual_lines,
    annual_prev_of,
    build_fin,
    fin_rows,
    pick_series,
    quarter_lines,
    quarter_prev_of,
    table,
)
from kbj.services.public_export.manifest import (
    check_payload,
    non_dart_source_paths,
    sha256_bytes,
    watch_trace_paths,
)
from kbj.services.public_export.pub_read import Query, Row
from tests.unit.public_export.pub_rows import (
    BIO,
    CODES,
    DUP_CODE,
    ELEC,
    GLOBAL,
    HOLD,
    NOW,
    REIT,
    REPORT_CODE,
    XLSX,
    MemReader,
    build_world,
    reader,
)


def _build(r: MemReader | None = None, demo: tuple[str, ...] = (REPORT_CODE,)) -> FinOut:
    rd = r or reader()
    return build_fin(rd, load_corps(rd), now=NOW, demo=demo)


@pytest.fixture(scope="module")
def out() -> FinOut:
    return _build()


def _env(out: FinOut, name: str) -> dict[str, Any]:
    f = next(f for f in out.files if f.name == name)
    return json.loads(f.render(NOW))


def _objects(tbl: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(zip(tbl["columns"], row, strict=True)) for row in tbl["rows"]]


def test_files_for_listed_companies_with_data(out: FinOut) -> None:
    names = sorted(f.name for f in out.files)
    assert names == [
        f"fin/{CODES[ELEC]}.json",
        f"fin/{CODES[BIO]}.json",
        f"fin/{CODES[HOLD]}.json",  # 재무 없음, 공시는 있다
        f"fin/{CODES[GLOBAL]}.json",
        f"reports/{REPORT_CODE}.json",
    ]
    assert f"fin/{CODES[REIT]}.json" not in names  # 재무·공시 모두 없으면 파일 없음
    assert DUP_CODE not in json.dumps(names)
    assert out.fin_codes == {CODES[ELEC], CODES[BIO], CODES[HOLD], CODES[GLOBAL]}
    assert out.report_codes == {REPORT_CODE}


def test_every_file_is_dart_envelope_without_watch_traces(out: FinOut) -> None:
    for f in out.files:
        text = f.render(NOW)
        assert check_payload(f.name, text) == [], f.name
        raw = json.loads(text)
        assert raw["source"] == "DART"
        assert non_dart_source_paths(raw) == []
        assert watch_trace_paths(raw) == []
        for word in ("KRX", "KIS", "consensus", "valuation", "per_", "mktcap", "prv_"):
            assert word not in text, (f.name, word)


def test_elec_sections(out: FinOut) -> None:
    env = _env(out, f"fin/{CODES[ELEC]}.json")
    d = env["data"]
    assert (d["code"], d["corp_code"], d["corp_name"], d["market"], d["acc_mt"]) == (
        CODES[ELEC],
        ELEC,
        "합성전자",
        "KOSPI",
        12,
    )
    fin = d["financials"]
    assert fin["quarters"]["columns"] == list(FIN_ROW_COLUMNS)
    qs = _objects(fin["quarters"])
    assert [q["period"] for q in qs] == [
        f"{y}Q{q}" for y in (2023, 2024, 2025, 2026) for q in (1, 2, 3, 4)
    ][2:14]  # 마지막 12분기(2023Q3~2026Q2)
    assert [a["period"] for a in _objects(fin["annual"])] == [
        "2022FY",
        "2023FY",
        "2024FY",
        "2025FY",
    ]
    assert fin["fs_div"] == "CFS" and fin["tier"] == "full" and fin["currency"] == "KRW"
    assert "연결(CFS)이 없는 분기는 별도(OFS)로 채웠다" in fin["notes"]
    assert fin["checks"] == {"diff3m_max_pct": 2.5, "n_derived": 0, "n_estimated": 1}
    assert fin["quality"] == "estimated"
    assert fin["ratios"]["period"] == "2026Q2"  # 가장 최근 분기
    assert fin["ratios"]["na_reasons"] == {"op_yoy": "missing_input"}
    # 공시: 최근 20(최근 것부터), 링크는 뷰어 주소 + 접수번호(한 번만 싣는다)
    fl = _objects(d["filings"])
    assert d["filings"]["columns"] == list(FILING_COLUMNS)
    assert len(fl) == 20
    assert [x["rcept_dt"] for x in fl] == sorted((x["rcept_dt"] for x in fl), reverse=True)
    assert fl[0]["rcept_no"] == "20261113000002"  # 같은 날은 접수번호 큰 것부터
    assert d["dart_viewer"].startswith("https://dart.fss.or.kr/")
    seg = d["segments"]
    assert seg["year"] == 2025 and [s["segment"] for s in seg["rows"]] == ["DS", "DX"]
    assert seg["rows"][0]["amounts"] == {"2025": 2_000_000_000, "2024": None}
    assert seg["quality"] == "estimated"
    assert d["overhang"] is None
    ins = d["insider"]
    assert ins["as_of"] == "2026-11-13" and [w["days"] for w in ins["windows"]] == [30, 90, 180]
    assert [(s["planned_date"], s["kind"]) for s in d["schedule"]] == [("2026-11-16", "statutory")]
    assert d["report"] is True
    assert env["as_of"] == "2026-11-13T00:00:00+09:00"  # 섹션 날짜 중 가장 늦은 것(공시일)
    assert env["quality"] == "estimated"


def test_bio_ofs_main_tier_noncalendar(out: FinOut) -> None:
    d = _env(out, f"fin/{CODES[BIO]}.json")["data"]
    fin = d["financials"]
    assert fin["fs_div"] == "OFS" and fin["tier"] == "main"
    assert "별도(OFS) — 연결재무제표 미작성" in fin["notes"]
    assert any("주요계정 층" in n for n in fin["notes"])
    q = _objects(fin["quarters"])[-1]
    assert q["label"] == "FY26 1Q (26.04~26.06)"  # 3월 결산 — 비12월 라벨
    loss = next(x for x in _objects(fin["quarters"]) if x["period"] == "2024Q1")
    assert loss["opm"] is None  # 매출 ≤ 0 → 비율 없음
    assert d["overhang"]["ratio_pct"] == 6.25 and d["overhang"]["as_of"] == "2026-11-13"
    assert fin["ratios"]["na_reasons"] == {"opm": "no_revenue"}


def test_hold_has_filings_but_no_financials(out: FinOut) -> None:
    env = _env(out, f"fin/{CODES[HOLD]}.json")
    d = env["data"]
    assert d["financials"] is None and d["segments"] is None
    assert any("분기 재무 없음" in n for n in env["notes"])
    assert len(d["filings"]["rows"]) == 1
    assert d["market"] == "KONEX" and d["acc_mt"] is None


def test_foreign_currency_not_converted(out: FinOut) -> None:
    fin = _env(out, f"fin/{CODES[GLOBAL]}.json")["data"]["financials"]
    assert fin["currency"] == "USD"
    assert "재무 통화 USD — 환산하지 않는다" in fin["notes"]
    assert _objects(fin["quarters"])[0]["revenue"] == 50_000_000


def test_report_and_xlsx(out: FinOut) -> None:
    env = _env(out, f"reports/{REPORT_CODE}.json")
    d = env["data"]
    assert d["built_on"] == "2026-11-15"  # 가장 최근 것
    assert d["xlsx_available"] is True and d["xlsx_sha256"] == sha256_bytes(XLSX)
    assert set(d["blocks"]) == {"quarterly", "annual_costs", "bridge", "orders", "quarterly_costs"}
    assert d["warnings"] == ["주석 표 1개 못 찾음"]
    assert env["as_of"] == "2026-11-15T21:05:00+09:00"
    assert [b.name for b in out.blobs] == [f"reports/{REPORT_CODE}.xlsx"]
    assert out.blobs[0].body == XLSX


def test_report_missing_or_bad_hash_is_skipped_not_faked() -> None:
    w = build_world()
    stored = next(iter(w.repos.fin.report_t.rows.values()))
    for s in w.repos.fin.report_t.rows.values():
        if s.row.built_on == date(2026, 11, 15):
            stored = s
    stored.extra["xlsx_sha256"] = "0" * 64  # 본문과 다른 해시
    out = _build(MemReader(w), demo=(REPORT_CODE, CODES[BIO]))
    assert out.report_codes == frozenset() and out.blobs == ()
    assert any(f"reports/{REPORT_CODE}: " in s and "해시" in s for s in out.skipped)
    assert any(CODES[BIO] in s and "아직 없다" in s for s in out.skipped)
    fin_elec = next(f for f in out.files if f.name == f"fin/{CODES[ELEC]}.json")
    assert fin_elec.data["report"] is False


def test_jsonb_objects_have_sorted_keys_whatever_the_source(out: FinOut) -> None:
    """jsonb 객체(부문 금액·리포트 블록·na_reasons)는 Postgres 가 키 순서를 바꿔 돌려준다 — 키를
    정렬해 실어 원천(Pg·메모리)에 따라 파일 글자가 달라지지 않는다(합성 세계는 '2025','2024' 순으로
    넣었다)."""
    d = _env(out, f"fin/{CODES[ELEC]}.json")["data"]
    for row in d["segments"]["rows"]:
        assert list(row["amounts"]) == sorted(row["amounts"])
    blocks = _env(out, f"reports/{REPORT_CODE}.json")["data"]["blocks"]
    assert list(blocks["quarterly"]) == sorted(blocks["quarterly"])  # 넣은 순서는 unit, rows

    def reorder(v: Any) -> Any:  # 키를 거꾸로 — Pg 가 다른 순서로 준 것과 같다
        if isinstance(v, dict):
            return {k: reorder(v[k]) for k in reversed(list(v))}  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        return v

    w = build_world()
    rd = MemReader(w)

    def flipped(q: Query, p: Any) -> list[Row]:
        return [{k: reorder(v) for k, v in r.items()} for r in rd(q, p)]

    again = build_fin(flipped, load_corps(rd), now=NOW, demo=(REPORT_CODE,))
    assert [f.render(NOW) for f in again.files] == [f.render(NOW) for f in out.files]


def test_no_demo_no_report_query() -> None:
    r = reader()
    out = _build(r, demo=())
    assert out.report_codes == frozenset()
    assert Q_REPORTS.name not in [c[0] for c in r.calls]


def test_no_corps_makes_nothing() -> None:
    out = build_fin(reader(), corps_from_rows([]), now=NOW, demo=())
    assert out.files == () and out.skipped


# ── 로그인 API(readers/fin.py)·재무 엔진과 같은 결과 ─────────────────────────────────────────


def _api_rows(corp: str) -> list[Any]:
    w = build_world()
    return w.repos.fin.quarters(corp)


@pytest.mark.parametrize("corp", [ELEC, BIO, GLOBAL])
def test_quarter_and_annual_rows_match_login_api(corp: str) -> None:
    r = reader()
    mine_rows = [x for x in r(Q_QUARTERS, {"from_year": 2000}) if x["corp_code"] == corp]
    picked = pick_series(mine_rows)
    api_series = engine_series(_api_rows(corp))
    assert [(x["fiscal_year"], x["fiscal_q"], x["fs_div"]) for x in picked] == [
        (x.fiscal_year, x.fiscal_q, x.fs_div) for x in api_series
    ]
    api_rows = _api_rows(corp)
    q_prev = {(r.fs_div, r.fiscal_year, r.fiscal_q): (r.revenue, r.op) for r in api_rows}
    api_years = _annual_all(api_rows)
    a_prev = {(fs, y, None): (x.revenue, x.op) for (fs, y), x in api_years.items()}
    years = annual_all(mine_rows)
    assert sorted(years) == sorted(api_years)
    for n in (4, 12, 40):
        mine_q = fin_rows(quarter_lines(picked), n, quarter_prev_of(mine_rows))
        api_q = [m.model_dump(mode="json") for m in _rows(_quarter_lines(api_series), n, q_prev)]
        assert mine_q == pytest.approx(api_q)
        mine_a = fin_rows(annual_lines(years), n, annual_prev_of(years))
        api_a = [m.model_dump(mode="json") for m in _rows(_annual_lines(api_years), n, a_prev)]
        assert mine_a == pytest.approx(api_a)


def test_sign_change_yoy_is_note_not_ratio(out: FinOut) -> None:
    fin = _env(out, f"fin/{CODES[BIO]}.json")["data"]["financials"]
    rows = {r["period"]: r for r in _objects(fin["quarters"])}
    assert rows["2024Q1"]["rev_yoy"] is None and "매출 적자전환" in rows["2024Q1"]["notes"]
    assert rows["2025Q1"]["rev_yoy"] is None and "매출 흑자전환" in rows["2025Q1"]["notes"]
    assert rows["2025Q2"]["rev_yoy"] == pytest.approx(0.0)


def test_annual_does_not_mix_bases(out: FinOut) -> None:
    """합성전자 2024 는 2분기 CFS 가 없다 → 그해 연간은 OFS 네 분기(기준을 섞지 않는다)."""
    fin = _env(out, f"fin/{CODES[ELEC]}.json")["data"]["financials"]
    annual = {r["period"]: r for r in _objects(fin["annual"])}
    q24 = [
        x
        for x in reader()(Q_QUARTERS, {"from_year": 2024})
        if x["corp_code"] == ELEC and x["fiscal_year"] == 2024 and x["fs_div"] == "OFS"
    ]
    assert annual["2024FY"]["revenue"] == sum(x["revenue"] for x in q24)
    # 2025 는 CFS 네 분기 — 전년(2024)은 OFS 라 같은 기준의 전년이 없다 → YoY 없음
    assert annual["2025FY"]["rev_yoy"] is None


def test_ratio_view_matches_login_api(out: FinOut) -> None:
    w = build_world()
    api = _ratio(w.repos.fin.ratios(ELEC)[-1]).model_dump(mode="json")
    mine = _env(out, f"fin/{CODES[ELEC]}.json")["data"]["financials"]["ratios"]
    assert mine.pop("na_reasons") == api.pop("na_reasons")
    assert mine == pytest.approx(api)


def test_table_helper() -> None:
    assert table(("a", "b"), [{"a": 1, "b": 2, "c": 3}]) == {
        "columns": ["a", "b"],
        "rows": [[1, 2]],
    }


# ── 크기(§7 — 종목당 약 6~9 KB × 약 2,600, 상한 fin_max_total_mb) ─────────────────────────────


def _big_reader(n: int) -> tuple[list[Row], Any]:
    """상장사 n 곳 — 모든 섹션이 꽉 찬 최악(분기 27·공시 20·부문 4·창 3·요약·일정)."""
    rnd = random.Random(20261116)  # noqa: S311 — 합성 값(고정 시드)
    rec = datetime(2026, 11, 16, 3, tzinfo=KST)
    titles = [
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "주식등의대량보유상황보고서(일반)",
        "[기재정정]단일판매ㆍ공급계약체결",
        "기타경영사항(자율공시)",
    ]
    corps: list[Row] = []
    data: dict[str, list[Row]] = {
        k: []
        for k in (
            "quarters",
            "ratios",
            "segments",
            "overhang_summary",
            "insider_windows",
            "recent_filings",
            "schedule",
            "reports",
        )
    }
    for i in range(n):
        corp, code = f"98{i:06d}", f"{i:06d}"
        corps.append(
            {
                "stock_code": code,
                "corp_code": corp,
                "corp_name": f"합성회사{i}",
                "received_at": rec,
                "quality": "ok",
                "acc_mt": 12,
                "induty_code": "264",
                "profile_cls": "Y",
                "disclosure_cls": "Y",
            }
        )
        for y in range(2020, 2027):
            for q in (1, 2, 3, 4):
                if (y, q) > (2026, 3):
                    continue
                end = date(y + (q == 4), (3 * q) % 12 + 1, 1) - timedelta(days=1)
                rev = rnd.randint(10**10, 10**13)
                data["quarters"].append(
                    {
                        "corp_code": corp,
                        "fiscal_year": y,
                        "fiscal_q": q,
                        "fs_div": "CFS",
                        "period_start": date(y, 3 * q - 2, 1),
                        "period_end": end,
                        "rcept_dt": end + timedelta(days=44),
                        "tier": "full",
                        "revenue": rev,
                        "op": rev // rnd.randint(5, 30),
                        "ni": rev // rnd.randint(10, 40),
                        "ni_owner": rev // rnd.randint(10, 40),
                        "derived": False,
                        "diff3m_max_pct": Decimal(repr(rnd.random())),
                        "currency": "KRW",
                        "notes": [],
                        "quality": "ok",
                    }
                )
        data["ratios"].append(
            {
                "corp_code": corp,
                "fiscal_year": 2026,
                "fiscal_q": 3,
                "fs_div": "CFS",
                "ttm_revenue": 4 * 10**12,
                "ttm_op": 4 * 10**11,
                "ttm_ni_owner": 2 * 10**11,
                **{
                    k: Decimal(repr(rnd.uniform(-50, 50)))
                    for k in (
                        "opm",
                        "npm",
                        "roe",
                        "roa",
                        "debt_ratio",
                        "net_debt_ratio",
                        "rev_yoy",
                        "op_yoy",
                        "eps_ttm",
                        "bps",
                    )
                },
                "shares_basis": 123_456_789,
                "na_reasons": {},
                "notes": [],
                "quality": "ok",
            }
        )
        for s in ("부문가", "부문나", "부문다", "기타"):
            data["segments"].append(
                {
                    "corp_code": corp,
                    "bsns_year": 2025,
                    "segment": s,
                    "rcept_no": "20260310000001",
                    "amounts": {
                        "2025": rnd.randint(10**9, 10**12),
                        "2024": rnd.randint(10**9, 10**12),
                    },
                    "quality": "estimated",
                }
            )
        data["overhang_summary"].append(
            {
                "corp_code": corp,
                "as_of": date(2026, 11, 13),
                "potential_new_shares": 1_234_567,
                "exercisable_shares": 234_567,
                "exchange_shares": 0,
                "denominator": 98_765_432,
                "denom_source": "dart_share_count",
                "ratio_pct": Decimal(repr(rnd.random() * 10)),
                "exercisable_ratio_pct": Decimal(repr(rnd.random())),
                "notes": [],
                "quality": "ok",
            }
        )
        for days in (30, 90, 180):
            data["insider_windows"].append(
                {
                    "corp_code": corp,
                    "as_of": date(2026, 11, 13),
                    "days": days,
                    "net_shares": 12_345,
                    "buy_shares": 23_456,
                    "sell_shares": 11_111,
                    "n_reports": 4,
                    "n_reporters": 3,
                    "ratio_pct": Decimal(repr(rnd.random())),
                    "reason_known_pct": Decimal("66.6"),
                    "quality": "ok",
                }
            )
        for k in range(20):
            dd = date(2026, 11, 13) - timedelta(days=k * 9)
            data["recent_filings"].append(
                {
                    "corp_code": corp,
                    "rcept_no": f"{dd:%Y%m%d}{k:06d}",
                    "rcept_dt": dd,
                    "title": rnd.choice(titles),
                    "kind": "other",
                    "is_correction": False,
                    "kw_score": 2,
                    "quality": "ok",
                }
            )
        data["schedule"].append(
            {
                "corp_code": corp,
                "period_end": date(2026, 9, 30),
                "kind": "statutory",
                "fiscal_year": 2026,
                "fiscal_q": 3,
                "planned_date": date(2026, 11, 16),
                "rcept_no": None,
                "note": "분기말 + 45일",
                "quality": "estimated",
                "computed_at": rec,
            }
        )

    def read(q: Query, _p: Any) -> list[Row]:
        return data[q.name]

    return corps, read


def test_full_universe_fits_size_cap() -> None:
    """상장사 2,600 곳이 모든 섹션을 꽉 채워도 `fin/` 합계가 상한 안(실측 약 21 MB — 25 MB)."""
    n = 2600
    corps, read = _big_reader(n)
    out = build_fin(read, corps_from_rows(corps), now=NOW, demo=())
    sizes = [len(f.render(NOW).encode("utf-8")) for f in out.files]
    cap = load_fin().public_export.fin_max_total_mb * 1024 * 1024
    assert len(sizes) == n
    assert sum(sizes) < cap, f"합계 {sum(sizes) / 2**20:.1f} MB, 종목당 {sum(sizes) / n:.0f} B"
    assert max(sizes) < 10_000
