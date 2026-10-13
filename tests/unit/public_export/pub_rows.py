"""공개 내보내기 시험용 합성 공개 표 — `kbj.core.fin_rows` 행으로 만들고, 같은 `Query` 에 같은
뜻으로 답하는 메모리 읽기(`MemReader`).

- 값은 모두 합성이다(종목코드 `Q0000n` — 실제 코드와 겹치지 않는 영문 단축코드 모양, corp_code
  `991000nn`). 공개 등급(DART) 표만 흉내 낸다 — 로그인 등급 원천은 없다.
- 행은 묶음 M 의 메모리 저장소(`kbj.store.repos.public.MemoryPublicRepos`)에 넣고, 열 이름 dict 로
  바꿀 때도 그 저장소의 표 명세(`Table.columns`·`values`)를 쓴다 — Postgres 가 돌려줄 열
  이름·모양(jsonb 는 파이썬 값, numeric 은 `Decimal`)과 같게.
- `MemReader` 의 각 질의 구현이 `kbj/services/public_export/*` 의 SQL 과 같은 뜻이어야 한다(같은
  거르기· 정렬). Postgres 대조는 통합 시험 몫(묶음 S 요청 — 이 파일의 `build_world` 를 그대로 Pg 에
  넣고 비교).

이 파일 이름은 `test_` 로 시작하지 않아 pytest 가 직접 모으지 않는다.
"""

from __future__ import annotations

import hashlib
import tempfile
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from kbj.config.fin import load_reports
from kbj.config.markets import CalendarEvent, CalendarEvents
from kbj.core import fin_rows as fr
from kbj.core.calendar import TradingCalendar
from kbj.core.quality import Quality
from kbj.core.time import KST
from kbj.services.public_export.export import export_all
from kbj.services.public_export.manifest import DART_ONLY_NAMES, MANIFEST_NAME, Manifest
from kbj.services.public_export.pub_read import Query, Row
from kbj.store.repos import filings as filings_repo
from kbj.store.repos import fin as fin_repo
from kbj.store.repos._table import Table
from kbj.store.repos.public import MemoryPublicRepos, memory_public_repos
from tests.fixtures.synthetic.p4_world import fiscal_quarter

NOW = datetime(
    2026, 11, 16, 5, 30, tzinfo=KST
)  # 월요일 05:30(public.export) — 그날 공시는 아직 없다
TODAY = NOW.date()
JOB = "test.pub"
SEEN = datetime(2026, 11, 13, 18, 0, tzinfo=KST)
EST, OK, STALE = Quality.ESTIMATED, Quality.OK, Quality.STALE

ELEC, BIO, HOLD, REIT, GLOBAL, DUP_A, DUP_B, PRIVATE = (
    "99100001",
    "99100002",
    "99100003",
    "99100004",
    "99100005",
    "99100006",
    "99100007",
    "99100009",
)
CODES = {ELEC: "Q00001", BIO: "Q00002", HOLD: "Q00003", REIT: "Q00004", GLOBAL: "Q00005"}
DUP_CODE = "Q00006"  # 두 회사에 붙은 종목코드(원천 이상) — 뺀다
REPORT_CODE = CODES[ELEC]
XLSX = b"PK\x03\x04 synthetic xlsx bytes"


def rc(d: date, n: int) -> str:
    return f"{d:%Y%m%d}{n:06d}"


@dataclass
class PubWorld:
    repos: MemoryPublicRepos
    corp_code_rows: list[Row]  # pub_filings.corp_code 행(열 이름 dict)
    rcepts: dict[str, str] = field(default_factory=dict[str, str])  # 이름 → 접수번호


def _disc(
    corp: str,
    code: str | None,
    name: str,
    d: date,
    n: int,
    *,
    kind: str = "other",
    title: str = "기타경영사항(자율공시)",
    cls: str | None = "Y",
    seen: datetime | None = None,
    score: int | None = 2,
) -> fr.DisclosureRow:
    return fr.DisclosureRow(
        rcept_no=rc(d, n),
        corp_code=corp,
        stock_code=code,
        corp_name=name,
        corp_cls=cls,
        title=title,
        base_title=title.removeprefix("[기재정정]"),
        filer=name,
        rcept_dt=d,
        remark=None,
        kind=kind,
        is_correction=title.startswith("["),
        first_seen_at=seen or datetime(d.year, d.month, d.day, 9, n % 50, tzinfo=KST),
        kw_score=score,
        kw_matched=("공급계약",) if score and score >= 8 else (),
    )


def quarter(
    corp: str,
    y: int,
    q: int,
    *,
    acc_mt: int = 12,
    fs: str = "CFS",
    tier: str = "full",
    revenue: int | None = 1_000_000_000_000,
    currency: str = "KRW",
    derived: bool = False,
    quality: Quality = OK,
    diff: float | None = 0.0,
    notes: tuple[str, ...] = (),
) -> fr.QuarterRow:
    fp = fiscal_quarter(y, q, acc_mt)
    rev = revenue
    return fr.QuarterRow(
        corp_code=corp,
        fiscal_year=y,
        fiscal_q=q,
        fs_div=fs,  # type: ignore[arg-type]
        period_start=fp.start,
        period_end=fp.end,
        rcept_dt=fp.end + timedelta(days=44),
        tier=tier,  # type: ignore[arg-type]
        revenue=None if derived else rev,
        op=None if derived or rev is None else rev // 10 + q * 1_000_000,
        ni=None if derived or rev is None else rev // 20,
        ni_owner=None if derived or rev is None else rev // 25,
        eps_basic=None if derived else 123.5,
        derived=derived,
        diff3m_max_pct=diff,
        currency=currency,
        notes=notes,
        quality=quality,
    )


def build_world() -> PubWorld:
    """시험용 합성 세계(상장사 5 + 겹친 코드 2 + 비상장 1)."""
    r = memory_public_repos()
    f, fin = r.filings, r.fin
    rec = datetime(2026, 11, 16, 3, 0, tzinfo=KST)
    w = PubWorld(r, [])

    def corp_row(corp: str, code: str | None, name: str) -> None:
        w.corp_code_rows.append(
            {
                "corp_code": corp,
                "stock_code": code,
                "corp_name": name,
                "modify_date": None,
                "source": "DART",
                "quality": "ok",
                "received_at": rec,
            }
        )
        if code is not None:
            f.corp_codes[code] = (corp, name)

    corp_row(ELEC, CODES[ELEC], "합성전자")
    corp_row(BIO, CODES[BIO], "합성바이오")
    corp_row(HOLD, CODES[HOLD], "합성홀딩스")
    corp_row(REIT, CODES[REIT], "합성리츠")
    corp_row(GLOBAL, CODES[GLOBAL], "합성글로벌")
    corp_row(DUP_A, DUP_CODE, "겹침가")
    corp_row(DUP_B, DUP_CODE, "겹침나")
    corp_row(PRIVATE, None, "비상장합성")

    f.upsert_profiles(
        [
            fr.CorpProfile(
                ELEC, CODES[ELEC], 12, "264", "Y", date(1969, 1, 13), corp_name="합성전자"
            ),
            fr.CorpProfile(BIO, CODES[BIO], 3, "21", "K", date(2001, 5, 2), corp_name="합성바이오"),
            fr.CorpProfile(REIT, CODES[REIT], 12, "68", "Y", None, corp_name="합성리츠"),
            fr.CorpProfile(GLOBAL, CODES[GLOBAL], 12, "70", "Y", None, corp_name="합성글로벌"),
        ],
        loaded_by=JOB,
        now=rec,
    )

    # ── 공시: 합성전자 25건(최근 20 자르기), 11-12·11-13 피드, 오래된 날 ─────────────────────
    d13, d12, d11 = date(2026, 11, 13), date(2026, 11, 12), date(2026, 11, 11)
    rows: list[fr.DisclosureRow] = []
    for i in range(25):
        d = date(2026, 6, 1) + timedelta(days=7 * i)
        if d > d11:
            d = d11
        rows.append(_disc(ELEC, CODES[ELEC], "합성전자", d, 100 + i))
    feed = [
        _disc(
            ELEC,
            CODES[ELEC],
            "합성전자",
            d13,
            1,
            kind="supply",
            title="단일판매ㆍ공급계약체결",
            score=8,
            seen=SEEN,
        ),
        _disc(
            ELEC,
            CODES[ELEC],
            "합성전자",
            d13,
            2,
            kind="prelim",
            title="[기재정정]연결재무제표기준영업(잠정)실적(공정공시)",
            score=6,
            seen=SEEN - timedelta(hours=1),
        ),
        _disc(
            BIO,
            CODES[BIO],
            "합성바이오",
            d13,
            3,
            kind="overhang",
            title="전환사채권발행결정",
            cls="K",
            score=8,
            seen=SEEN - timedelta(hours=2),
        ),
        _disc(HOLD, CODES[HOLD], "합성홀딩스", d12, 4, cls="N", score=None),
        _disc(DUP_A, DUP_CODE, "겹침가", d12, 5),
        _disc(PRIVATE, None, "비상장합성", d13, 6, cls="E"),
        _disc(GLOBAL, CODES[GLOBAL], "합성글로벌", d11, 7, kind="periodic", title="분기보고서"),
        _disc(
            BIO,
            CODES[BIO],
            "합성바이오",
            date(2026, 11, 5),
            8,
            kind="overhang",
            title="전환사채권발행결정",
            cls="K",
            score=8,
        ),
        _disc(
            ELEC,
            CODES[ELEC],
            "합성전자",
            date(2026, 11, 10),
            9,
            kind="prelim",
            title="연결재무제표기준영업(잠정)실적(공정공시)",
            score=6,
        ),
    ]
    rows += feed
    f.upsert_disclosures(rows, loaded_by=JOB, now=rec)
    w.rcepts.update(
        supply=rc(d13, 1),
        prelim_fix=rc(d13, 2),
        cb_new=rc(d13, 3),
        cb_old=rc(date(2026, 11, 5), 8),
        prelim=rc(date(2026, 11, 10), 9),
    )

    # ── 잠정실적: 합성전자 2건(원·정정), 비상장 1건, 기간 밖 1건 ───────────────────────────────
    def prelim(corp: str, rcept: str, d: date, *, op: int | None = 60_000_000_000) -> fr.PrelimRow:
        return fr.PrelimRow(
            rcept_no=rcept,
            corp_code=corp,
            rcept_dt=d,
            scope=fr.PrelimScope.CONSOLIDATED,
            fiscal_year=2026,
            fiscal_q=3,
            is_annual=False,
            period_end=date(2026, 9, 30),
            period_inferred=False,
            unit_mult=100_000_000,
            revenue_cur=1_200_000_000_000,
            revenue_prev_q=1_100_000_000_000,
            revenue_prev_y=1_000_000_000_000,
            op_cur=op,
            op_prev_q=50_000_000_000,
            op_prev_y=-10_000_000_000,
            ni_cur=40_000_000_000,
            eps_cur=812.5,
            yoy_reported={"revenue": 20.0},
            notes=("합성",),
        )

    f.put_prelims(
        [
            prelim(ELEC, w.rcepts["prelim"], date(2026, 11, 10)),
            prelim(ELEC, w.rcepts["prelim_fix"], d13, op=61_000_000_000),
            prelim(PRIVATE, rc(d13, 6), d13),
            prelim(ELEC, rc(date(2026, 10, 1), 10), date(2026, 10, 1)),
        ],
        loaded_by=JOB,
        now=rec,
    )

    # ── 실적 일정 ────────────────────────────────────────────────────────────────────────────
    def sch(corp: str, kind: str, q: int, planned: date, **kw: Any) -> fr.ScheduleRow:
        return fr.ScheduleRow(
            corp_code=corp,
            period_end=date(2026, 9, 30),
            kind=kind,  # type: ignore[arg-type]
            planned_date=planned,
            fiscal_year=2026,
            fiscal_q=q,
            **kw,
        )

    sched = [
        sch(ELEC, "statutory", 3, date(2026, 11, 16), note="분기말 + 45일", quality=EST),
        sch(BIO, "pre_announce", 2, date(2026, 11, 20), rcept_no=rc(d12, 11)),
        sch(HOLD, "statutory", 3, date(2027, 1, 30), quality=EST),
        sch(PRIVATE, "statutory", 3, date(2026, 11, 16), quality=EST),
    ]
    f.replace_schedule({s.corp_code for s in sched}, sched, loaded_by=JOB, now=rec)

    # ── 오버행: 합성바이오 CB 2건(새·옛), 요약 2일치 ─────────────────────────────────────────
    def cb(rcept: str, d: date) -> fr.OverhangEvent:
        return fr.OverhangEvent(
            rcept_no=rcept,
            corp_code=BIO,
            rcept_dt=d,
            instrument=fr.Instrument.CB,
            seq="3",
            face_value=10_000_000_000,
            price=5_000.0,
            shares=2_000_000,
            refix_floor=3_500.0,
            exercise_start=d + timedelta(days=365),
            exercise_end=d + timedelta(days=365 * 3),
            maturity=d + timedelta(days=365 * 3 + 30),
            listing_date=None,
            alloc_method="사모",
            parse_source="api",
        )

    f.put_overhang_events(
        [
            cb(w.rcepts["cb_new"], d13),
            cb(w.rcepts["cb_old"], date(2026, 11, 5)),
            cb(rc(date(2026, 1, 5), 12), date(2026, 1, 5)),
        ],
        loaded_by=JOB,
        now=rec,
    )
    for as_of, ratio in ((date(2026, 11, 12), 3.1), (date(2026, 11, 13), 6.25)):
        summ = fr.OverhangSummary(
            corp_code=BIO,
            as_of=as_of,
            potential_new_shares=4_000_000,
            exercisable_shares=0,
            exchange_shares=0,
            denominator=64_000_000,
            denom_source="dart_share_count",
            ratio_pct=ratio,
            exercisable_ratio_pct=0.0,
            notes=("합성",),
            quality=OK,
        )
        f.put_overhang_state(BIO, as_of, [], summ, loaded_by=JOB, now=rec)

    # ── 내부자 창: 합성바이오(큰 비율)·합성전자(작은 비율)·합성홀딩스(보고 0)·오래된 창 ──────
    def windows(corp: str, as_of: date, net: int, ratio: float | None) -> list[fr.InsiderWindow]:
        out: list[fr.InsiderWindow] = []
        for days in fr.INSIDER_WINDOWS:
            buy = max(net, 0) + days * 10
            sell = buy - net
            out.append(
                fr.InsiderWindow(
                    corp_code=corp,
                    as_of=as_of,
                    days=days,
                    net_shares=net,
                    buy_shares=buy,
                    sell_shares=sell,
                    n_reports=0 if net == 0 and ratio is None else 3,
                    n_reporters=0 if net == 0 and ratio is None else 2,
                    ratio_pct=ratio,
                    reason_known_pct=50.0 if ratio is not None else None,
                    quality=EST if corp == ELEC else OK,
                )
            )
        return out

    f.put_insider_windows(
        BIO,
        date(2026, 11, 13),
        windows(BIO, date(2026, 11, 13), -500_000, -0.78),
        loaded_by=JOB,
        now=rec,
    )
    f.put_insider_windows(
        ELEC,
        date(2026, 11, 12),
        windows(ELEC, date(2026, 11, 12), 1_000, 0.01),
        loaded_by=JOB,
        now=rec,
    )
    f.put_insider_windows(
        ELEC,
        date(2026, 11, 13),
        windows(ELEC, date(2026, 11, 13), 2_000, 0.02),
        loaded_by=JOB,
        now=rec,
    )
    f.put_insider_windows(
        HOLD, date(2026, 11, 13), windows(HOLD, date(2026, 11, 13), 0, None), loaded_by=JOB, now=rec
    )
    f.put_insider_windows(
        GLOBAL,
        date(2026, 10, 1),
        windows(GLOBAL, date(2026, 10, 1), 9_000, 5.0),
        loaded_by=JOB,
        now=rec,
    )

    # ── 재무: 합성전자(CFS 전 + OFS 일부, 상세 층), 합성바이오(3월 결산 OFS, 주요계정 층),
    #          합성글로벌(USD), 합성리츠(없음) ─────────────────────────────────────────────────
    qs: list[fr.QuarterRow] = []
    for y in range(2019, 2027):
        for q in (1, 2, 3, 4):
            if (y, q) > (2026, 2):
                continue
            rev = 1_000_000_000_000 + (y - 2019) * 40_000_000_000 + q * 5_000_000_000
            qs.append(
                quarter(
                    ELEC,
                    y,
                    q,
                    revenue=rev,
                    quality=EST if (y, q) == (2025, 3) else OK,
                    diff=2.5 if (y, q) == (2025, 3) else 0.1,
                    notes=("3개월값과 2.5% 차이",) if (y, q) == (2025, 3) else (),
                )
            )
            qs.append(quarter(ELEC, y, q, fs="OFS", revenue=rev // 2))
    # CFS 가 없는 분기 하나(OFS 로 채움) — 2024Q2 의 CFS 를 빼고 OFS 만
    qs = [x for x in qs if not (x.fiscal_year == 2024 and x.fiscal_q == 2 and x.fs_div == "CFS")]
    for y in range(2021, 2027):
        for q in (1, 2, 3, 4):
            if (y, q) > (2026, 1):
                continue
            qs.append(
                quarter(
                    BIO,
                    y,
                    q,
                    acc_mt=3,
                    fs="OFS",
                    tier="main",
                    revenue=-5_000_000 if (y, q) == (2024, 1) else 30_000_000_000,
                    derived=(y, q) == (2021, 1),
                )
            )
    for y in (2025, 2026):
        for q in (1, 2):
            qs.append(quarter(GLOBAL, y, q, currency="USD", revenue=50_000_000))
    fin.upsert_quarters(qs, loaded_by=JOB, now=rec)
    fin.upsert_ratios(
        [
            fr.RatioRow(ELEC, 2026, 1, "CFS", ttm_revenue=4_400_000_000_000, opm=10.5),
            fr.RatioRow(
                ELEC,
                2026,
                2,
                "CFS",
                ttm_revenue=4_420_000_000_000,
                ttm_op=450_000_000_000,
                opm=10.2,
                npm=4.0,
                roe=8.5,
                roa=4.1,
                debt_ratio=45.0,
                net_debt_ratio=-12.5,
                rev_yoy=3.3,
                op_yoy=None,
                eps_ttm=494.0,
                bps=40_000.0,
                shares_basis=5_919_637_922,
                na_reasons={"op_yoy": "missing_input"},
            ),
            fr.RatioRow(
                BIO,
                2026,
                1,
                "OFS",
                opm=None,
                na_reasons={"opm": "no_revenue"},
                quality=EST,
                notes=("비율 일부 없음",),
            ),
        ],
        loaded_by=JOB,
        now=rec,
    )
    fin.put_segments(
        [
            fr.SegmentRow(ELEC, 2024, "DX", {"2024": 100}, rc(date(2025, 3, 10), 1)),
            fr.SegmentRow(
                ELEC, 2025, "DS", {"2025": 2_000_000_000, "2024": None}, rc(date(2026, 3, 10), 1)
            ),
            fr.SegmentRow(
                ELEC,
                2025,
                "DX",
                {"2025": 3_000_000_000, "2024": 2_900_000_000},
                rc(date(2026, 3, 10), 1),
            ),
        ],
        loaded_by=JOB,
        now=rec,
    )
    fin.put_report(
        fr.ReportRow(
            stock_code=REPORT_CODE,
            built_on=date(2026, 11, 15),
            corp_code=ELEC,
            year_from=2023,
            year_to=2026,
            fs_div_used="CFS",
            payload={
                "unit": "억원",
                "quarterly": {"unit": "억원", "rows": []},
                "annual_costs": None,
                "bridge": {"steps": []},
                "orders": None,
                "quarterly_costs": None,
                "unmapped": [],
            },
            xlsx=XLSX,
            warnings=("주석 표 1개 못 찾음",),
            engine_version="dart_excel-1",
            input_digest="d1",
            generated_at=datetime(2026, 11, 15, 21, 5, tzinfo=KST),
        ),
        loaded_by=JOB,
    )
    fin.put_report(
        fr.ReportRow(
            stock_code=REPORT_CODE,
            built_on=date(2026, 11, 1),
            corp_code=ELEC,
            year_from=2023,
            year_to=2026,
            fs_div_used="CFS",
            payload={"unit": "억원"},
            xlsx=None,
            warnings=(),
            engine_version="dart_excel-1",
            input_digest="d0",
            generated_at=datetime(2026, 11, 1, 21, 5, tzinfo=KST),
        ),
        loaded_by=JOB,
    )
    return w


# ── 열 이름 dict 로(Postgres 가 돌려주는 모양) ─────────────────────────────────────────────


def _plain(v: object) -> object:
    if isinstance(v, Jsonb):
        return v.obj
    if isinstance(v, float):
        return Decimal(repr(v))  # numeric → Decimal(실수 칸) — 빌더의 num() 이 다시 float
    return v


def _rows(table: Table[Any], stored: Iterable[Any]) -> list[Row]:
    out: list[Row] = []
    for s in stored:
        vals = table.values(s.row, s.extra)
        out.append({c: _plain(v) for c, v in zip(table.columns, vals, strict=True)})
    return out


def table_rows(repos: MemoryPublicRepos, name: str) -> list[Row]:
    f, fin = repos.filings, repos.fin
    tables: dict[str, Any] = {
        "disclosure": (filings_repo.DISCLOSURE, f.disclosure_t),
        "corp_profile": (filings_repo.PROFILE, f.profile_t),
        "earnings_prelim": (filings_repo.PRELIM, f.prelim_t),
        "earnings_schedule": (filings_repo.SCHEDULE, f.schedule_t),
        "overhang_event": (filings_repo.OVERHANG_EVENT, f.event_t),
        "overhang_summary": (filings_repo.OVERHANG_SUMMARY, f.summary_t),
        "insider_window": (filings_repo.WINDOW, f.window_t),
        "quarterly": (fin_repo.QUARTER, fin.quarter_t),
        "ratio": (fin_repo.RATIO, fin.ratio_t),
        "segment": (fin_repo.SEGMENT, fin.segment_t),
        "report": (fin_repo.REPORT, fin.report_t),
    }
    spec, mem = tables[name]
    return _rows(spec, mem.rows.values())


def _desc(*keys: str) -> Callable[[Row], tuple[Any, ...]]:
    return lambda r: tuple(r[k] for k in keys)


@dataclass
class MemReader:
    """`Query.name` 마다 SQL 과 같은 뜻의 파이썬 구현. 부른 질의 이름·매개변수를 남긴다."""

    world: PubWorld
    calls: list[tuple[str, dict[str, object]]] = field(
        default_factory=list[tuple[str, dict[str, object]]]
    )

    def __call__(self, q: Query, params: Mapping[str, object]) -> list[Row]:
        p = dict(params)
        self.calls.append((q.name, p))
        fn = getattr(self, f"q_{q.name}")
        return fn(p)

    def t(self, name: str) -> list[Row]:
        return table_rows(self.world.repos, name)

    # ── P5 MAC(macro/·credit.json) — 같은 뜻의 메모리 읽기는 web_fixtures_p5.macro 한 곳 ──────
    def _mac(self, name: str, p: dict[str, Any]) -> list[Row]:
        from tests.unit.api.web_fixtures_p5.macro import memory_pub_reader

        return memory_pub_reader(self.world.repos)(Query(name, "SELECT 1"), p)

    def q_macro_meta(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("macro_meta", p)

    def q_macro_series(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("macro_series", p)

    def q_macro_spread(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("macro_spread", p)

    def q_macro_yoy(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("macro_yoy", p)

    def q_macro_calendar(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("macro_calendar", p)

    def q_credit_series(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("credit_series", p)

    def q_credit_balance(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("credit_balance", p)

    def q_credit_capital(self, p: dict[str, Any]) -> list[Row]:
        return self._mac("credit_capital", p)

    # ── corp_index ─────────────────────────────────────────────────────────────────────
    def q_corps(self, p: dict[str, Any]) -> list[Row]:
        prof = {r["corp_code"]: r for r in self.t("corp_profile")}
        disc = sorted(self.t("disclosure"), key=_desc("rcept_dt", "rcept_no"), reverse=True)
        out: list[Row] = []
        for c in self.world.corp_code_rows:
            if c["stock_code"] is None:
                continue
            pr = prof.get(c["corp_code"], {})
            dc = next(
                (
                    d["corp_cls"]
                    for d in disc
                    if d["corp_code"] == c["corp_code"] and d["corp_cls"] is not None
                ),
                None,
            )
            out.append(
                {
                    "stock_code": c["stock_code"],
                    "corp_code": c["corp_code"],
                    "corp_name": c["corp_name"],
                    "received_at": c["received_at"],
                    "quality": c["quality"],
                    "acc_mt": pr.get("acc_mt"),
                    "induty_code": pr.get("induty_code"),
                    "profile_cls": pr.get("corp_cls"),
                    "disclosure_cls": dc,
                }
            )
        return sorted(out, key=_desc("stock_code", "corp_code"))

    # ── filings_json ───────────────────────────────────────────────────────────────────
    def q_feed(self, p: dict[str, Any]) -> list[Row]:
        listed = [r for r in self.t("disclosure") if r["stock_code"] is not None]
        days = sorted(
            {r["rcept_dt"] for r in listed if p["floor"] < r["rcept_dt"] <= p["today"]},
            reverse=True,
        )[: p["n_days"]]
        rows = [r for r in listed if r["rcept_dt"] in days]
        return sorted(rows, key=_desc("rcept_dt", "first_seen_at", "rcept_no"), reverse=True)

    def q_prelims(self, p: dict[str, Any]) -> list[Row]:
        fix = {r["rcept_no"]: r["is_correction"] for r in self.t("disclosure")}
        rows = [
            {**r, "is_correction": fix.get(r["rcept_no"], False)}
            for r in self.t("earnings_prelim")
            if p["start"] <= r["rcept_dt"] <= p["end"]
        ]
        return sorted(rows, key=_desc("rcept_dt", "rcept_no"), reverse=True)

    def _latest_summary(self, today: date) -> dict[str, Row]:
        best: dict[str, Row] = {}
        for r in self.t("overhang_summary"):
            if r["as_of"] <= today and (
                r["corp_code"] not in best or r["as_of"] > best[r["corp_code"]]["as_of"]
            ):
                best[r["corp_code"]] = r
        return best

    def q_overhang(self, p: dict[str, Any]) -> list[Row]:
        summ = self._latest_summary(p["today"])
        rows = [
            {
                **r,
                "ratio_pct": summ[r["corp_code"]]["ratio_pct"] if r["corp_code"] in summ else None,
            }
            for r in self.t("overhang_event")
            if p["start"] <= r["rcept_dt"] <= p["end"]
        ]
        return sorted(rows, key=_desc("rcept_no"), reverse=True)

    def q_insider(self, p: dict[str, Any]) -> list[Row]:
        best: dict[str, Row] = {}
        for r in self.t("insider_window"):
            if r["days"] == p["days"] and p["since"] <= r["as_of"] <= p["today"]:
                if r["corp_code"] not in best or r["as_of"] > best[r["corp_code"]]["as_of"]:
                    best[r["corp_code"]] = r
        return [best[c] for c in sorted(best)]

    def q_schedule(self, p: dict[str, Any]) -> list[Row]:
        rows = [
            r for r in self.t("earnings_schedule") if p["start"] <= r["planned_date"] <= p["end"]
        ]
        return sorted(rows, key=_desc("planned_date", "corp_code", "period_end", "kind"))

    # ── fin_json ───────────────────────────────────────────────────────────────────────
    def q_quarters(self, p: dict[str, Any]) -> list[Row]:
        rows = [r for r in self.t("quarterly") if r["fiscal_year"] >= p["from_year"]]
        return sorted(rows, key=_desc("corp_code", "fiscal_year", "fiscal_q", "fs_div"))

    def q_ratios(self, p: dict[str, Any]) -> list[Row]:
        best: dict[str, Row] = {}
        for r in self.t("ratio"):
            k = r["corp_code"]
            if k not in best or (r["fiscal_year"], r["fiscal_q"]) > (
                best[k]["fiscal_year"],
                best[k]["fiscal_q"],
            ):
                best[k] = r
        return [best[c] for c in sorted(best)]

    def q_segments(self, p: dict[str, Any]) -> list[Row]:
        rows = self.t("segment")
        last: dict[str, int] = {}
        for r in rows:
            last[r["corp_code"]] = max(last.get(r["corp_code"], 0), r["bsns_year"])
        keep = [r for r in rows if r["bsns_year"] == last[r["corp_code"]]]
        return sorted(keep, key=_desc("corp_code", "segment"))

    def q_overhang_summary(self, p: dict[str, Any]) -> list[Row]:
        best = self._latest_summary(p["today"])
        return [best[c] for c in sorted(best)]

    def q_insider_windows(self, p: dict[str, Any]) -> list[Row]:
        rows = [r for r in self.t("insider_window") if r["as_of"] <= p["today"]]
        last: dict[str, date] = {}
        for r in rows:
            last[r["corp_code"]] = max(last.get(r["corp_code"], r["as_of"]), r["as_of"])
        keep = [r for r in rows if r["as_of"] == last[r["corp_code"]]]
        return sorted(keep, key=_desc("corp_code", "days"))

    def q_recent_filings(self, p: dict[str, Any]) -> list[Row]:
        by: dict[str, list[Row]] = defaultdict(list)
        for r in self.t("disclosure"):
            if r["stock_code"] is not None and p["since"] <= r["rcept_dt"] <= p["today"]:
                by[r["corp_code"]].append(r)
        out: list[Row] = []
        for c in sorted(by):
            out += sorted(by[c], key=_desc("rcept_dt", "rcept_no"), reverse=True)[: p["n"]]
        return out

    def q_reports(self, p: dict[str, Any]) -> list[Row]:
        best: dict[str, Row] = {}
        for r in self.t("report"):
            if r["stock_code"] in p["codes"]:
                k = r["stock_code"]
                if k not in best or r["built_on"] > best[k]["built_on"]:
                    best[k] = r
        return [best[c] for c in sorted(best)]


def reader(world: PubWorld | None = None) -> MemReader:
    return MemReader(world or build_world())


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ── 프런트 시험 픽스처(web/test/fixtures/public — P4 파일) ───────────────────────────────────

ROOT = Path(__file__).resolve().parents[3]
WEB_PUBLIC = ROOT / "web" / "test" / "fixtures" / "public"
P4_MANIFEST = (
    "manifest_p4.json"  # P3 의 manifest.json(손으로 쓴 예시)은 W 시험이 쓴다 — 건드리지 않는다
)
EVENTS = CalendarEvents(
    version=1,
    events=(
        CalendarEvent(
            kind="bok_mpc",
            date=date(2026, 11, 26),
            title="금통위 통화정책방향(합성)",
            source="한국은행 공표 일정(수기 설정)",
        ),
    ),
)


def export_world(out: Path) -> Manifest:
    """합성 세계 전체를 `out` 에 내보낸다(공개 데모 = REPORT_CODE, 시각·일정 고정)."""
    rcfg = load_reports().model_copy(update={"public_demo": (REPORT_CODE,)})
    return export_all(
        None,
        out,
        now=NOW,
        cal=TradingCalendar.default(),
        events=EVENTS,
        reader=reader(),
        reports_cfg=rcfg,
    )


def web_fixture_files(tmp: Path) -> dict[str, bytes]:
    """프런트 픽스처로 둘 파일(경로 → 바이트): P4 평평한 파일·manifest(이름 바꿈)·fin/·reports/."""
    m = export_world(tmp)
    out: dict[str, bytes] = {}
    for name in m.names:
        if name in DART_ONLY_NAMES:
            out[name] = (tmp / name).read_bytes()
    out[P4_MANIFEST] = (tmp / MANIFEST_NAME).read_bytes()
    for d in m.dirs:
        for p in sorted((tmp / d.name).iterdir()):
            out[f"{d.name}/{p.name}"] = p.read_bytes()
    return out


def write_web_fixtures() -> list[str]:
    with tempfile.TemporaryDirectory() as tmp:
        files = web_fixture_files(Path(tmp))
    for rel, body in files.items():
        dest = WEB_PUBLIC / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
    return sorted(files)


if __name__ == "__main__":  # uv run python -m tests.unit.public_export.pub_rows
    for n in write_web_fixtures():
        print(n)
