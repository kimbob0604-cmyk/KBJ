"""합성 20종목 세계 `p4_world` — P4 묶음(F·E·R·K·V·A·X·S)이 같이 쓰는 고정 시드 합성 데이터.

근거: docs/p4_design.md §1.1(M)·§8.4(완료 시험 표 20행)·§4(엔진 명세)·§8.5(하루 시뮬레이션).
로그인 등급(KIS·KRX) 실데이터 fixture 는 쓰지 않는다(CLAUDE.md 절대 규칙) — 이 생성기가
`random.Random(f"{seed}:{이름}")` 으로 결정적으로 만든다(같은 시드 → 같은 `digest()`). 공개
등급(DART)
응답도 모양만 흉내 낸 합성이다. **실데이터 아님** — 모든 기록의 머리 칸 `_source` 가 `SYNTHETIC`
으로
시작한다.

쓰는 법
    from tests.fixtures.synthetic.p4_world import build_world
    w = build_world()                      # 시드 20261007(캐시 — 같은 객체)
    w.stock("994010").corp_code            # '99400010'
    w.dart_json("list.json", {"bgn_de": "20261116", "page_no": "1"}, now=w.clock)
    w.dart_bytes("document.xml", {"rcept_no": ...}, now=w.clock)   # zip 또는 None
    w.bars("994010")                       # kbj.core.rows.Bar(krx) — 첫 상장일 또는 2021-06-01 부터

무엇이 있나(설계 §1.1 M 행)
- 종목 20개(§8.4 표 순서 — `WorldStock.no`)와 회사(DART 고유번호 `994000xx` — 우선주는 없다) +
  비상장
  잡음 회사 10개(`994009xx`, 공시 목록 쪽 경계용).
- DART 응답: 고유번호 zip(`corpCode.xml`), 공시 목록(`list.json` — 쪽·기간·회사·유형 거르기, 시각
  `now` 이전에 '접수'된 것만), 주요사항
  구조화(`cvbdIsDecsn`·`bdwtIsDecsn`·`piicDecsn`·`tsstkAqDecsn`
  …), 소유보고(`elestock`·`majorstock`), 주식총수(`stockTotqySttus`)·최대주주 현황(`hyslrSttus`),
  기업개황(`company`), 재무제표
  (`fnlttSinglAcntAll` — 전체, `fnlttMultiAcnt` — 주요계정), 원문 zip(`document.xml` — 잠정실적 5열·
  2열 폼, CB·BW·유상 결정, 전환청구·행사·전환가 조정, 소유보고, 공급계약, 정기보고서 주석(G2 회사 둘
  —
  비용 성격별·금융수익/원가·기타수익/비용·부문 매출, 연결·별도 파일 셋의 이중 합산 함정)).
- 정답(참값): 분기 단독 재무(`QuarterTruth` — 3개월 값·분기말 상태표·주석 항목), 잠정실적
  (`PrelimRow`), 오버행 결정·조정(`OverhangEvent`·`OverhangAdjust`), 소유보고(`InsiderReport`·
  `MajorHolderReport`), 자기주식(`BuybackEvent`), 주식총수(`ShareCount`), 기업개황(`CorpProfile`),
  결산예고(`ScheduleRow`), 종류주식(`ShareClass`), 관심종목(`WatchItem` 19개 — #16 은 관심 밖).
- KIS 컨센서스(의미값 — TR 출력 이름은 [확인 필요 #29], 묶음 K 의 가짜 서버가 바꿔 싣는다):
  `ConsensusRow`(2026-09-01 부터 매 거래일 스냅), `OpinionRow`(증권사별 의견·목표가).
- KRX 일별(2021-06-01 ~ 2026-11-13): `Bar`·`Snap`·`UniverseRow`·`SplitEvent`, 원 응답 행
  (`krx_daily_rows` — KRX OPEN API 필드 이름), KIS 투자자 일별(`InvestorDay` — 마지막 20거래일).
- 손계산 기대값: `expect_overhang()`(#14), `expect_insider_windows()`(#15) — 엔진과 따로 쓴 산수.
  같은 수가 `p4_expect.yaml` 에 있다(시험이 둘을 맞춘다).

가짜 시계: `CLOCK` = 2026-11-16(월) 21:30 KST — 3분기 보고 마감일(11-14 토 → 11-16 월) 밤. KRX
일별의 마지막 확정일은 `LAST_KRX_DAY` = 11-13(KRX 는 D+1 08:00 공표 — 11-16 행은 아직 없다).

[확인 필요] 실측 전의 모양: DART 구조화 응답 필드 이름(#31~#33 — 이 파일의 `_row_*` 함수가 쓰는
이름은 DART 개발가이드 기준 추정), 날짜·금액 표기('2026년 03월 15일'·'10,000,000,000'), 소유보고의
`rcept_dt`(YYYYMMDD 로 둠), 비12월 결산의 `bsns_year`(#34 — 여기서는 '회계연도 시작 연도'), 잠정실적
본문의 기간 표기(#30), 전환청구·전환가 조정 원문 표 모양. 실측으로 바뀌면 **이 파일 한 곳**을 고친다
(묶음 M 담당 — 다른 묶음은 요청).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import random
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cache
from typing import Any, Final, Literal

from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import (
    BuybackEvent,
    ConsensusRow,
    CorpProfile,
    FiscalPeriod,
    InsiderReport,
    Instrument,
    MajorHolderReport,
    OpinionRow,
    OverhangAdjust,
    OverhangEvent,
    PrelimRow,
    PrelimScope,
    ScheduleRow,
    ShareClass,
    ShareCount,
    WatchItem,
    reprt_of_quarter,
)
from kbj.core.quality import Quality
from kbj.core.rows import INST7, Bar, Investor, InvestorDay, Snap, SplitEvent, UniverseRow
from kbj.core.time import KST

__all__ = [
    "CLOCK",
    "LAST_KRX_DAY",
    "ROUTES",
    "SEED",
    "SYNTHETIC",
    "TODAY",
    "P4World",
    "QuarterTruth",
    "WorldCorp",
    "WorldDisclosure",
    "WorldStock",
    "build_world",
    "fiscal_quarter",
]

SEED: Final = 20261007
SYNTHETIC: Final = (
    "SYNTHETIC — tests/fixtures/synthetic/p4_world.py(고정 시드 생성기)가 만든 합성 데이터. "
    "실측 아님"
)
TODAY: Final = date(2026, 11, 16)
CLOCK: Final = datetime(2026, 11, 16, 21, 30, tzinfo=KST)
LAST_KRX_DAY: Final = date(2026, 11, 13)
PRICE_FROM: Final = date(2021, 6, 1)
CONSENSUS_FROM: Final = date(2026, 9, 1)
INVESTOR_DAYS: Final = 20
USD_KRW: Final = 1350.0  # 외국기업(#19) 시세 기준값 환산에만 — 재무는 USD 그대로(환산 안 함)
# 페이지 8 라우트 14개(§8.4 — `/api/stock/{code}/*` 13개 + P3 `/api/flows/stock/{code}`)
ROUTES: Final[tuple[str, ...]] = (
    "header",
    "chart",
    "financials",
    "report",
    "report_xlsx",
    "segments",
    "consensus",
    "valuation",
    "scenario",
    "overhang",
    "insiders",
    "disclosures",
    "earnings",
    "flows",
)
# 피드 날(11-13 다시 훑기·11-16 당일)의 비상장 잡음 공시 수 — 100건 쪽 경계를 넘게
FEED_NOISE: Final[Mapping[date, int]] = {date(2026, 11, 13): 140, TODAY: 230}
STATUS_TEXT: Final[Mapping[str, str]] = {
    "000": "정상",
    "013": "조회된 데이타가 없습니다.",
}
_KRW_UNIT: Final = 1_000_000  # 원화 회사 재무 참값은 백만원 단위로 맞춘 원(주석 표가 정확히 맞게)
_USD_UNIT: Final = 1_000
_REPORT_NAME: Final[Mapping[str, str]] = {
    "11013": "분기보고서",
    "11012": "반기보고서",
    "11014": "분기보고서",
    "11011": "사업보고서",
}


# ── 달력·기간 ──────────────────────────────────────────────────────────────────────────


@cache
def _cal() -> TradingCalendar:
    return TradingCalendar.default()


def _tdays(start: date, end: date) -> tuple[date, ...]:
    cal = _cal()
    out: list[date] = []
    d = start
    while d <= end:
        if cal.is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return tuple(out)


def _on_or_after(d: date) -> date:
    while not _cal().is_trading_day(d):
        d += timedelta(days=1)
    return d


def _on_or_before(d: date) -> date:
    while not _cal().is_trading_day(d):
        d -= timedelta(days=1)
    return d


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    return date(d.year + y, m + 1, 1)


def fiscal_quarter(year: int, q: int, acc_mt: int) -> FiscalPeriod:
    """회계연도 `year`(시작 연도 — [확인 필요 #34])의 q 분기. 12월 결산이면 달력 분기."""
    fy_start = date(year, acc_mt % 12 + 1, 1)
    start = _add_months(fy_start, 3 * (q - 1))
    end = _add_months(start, 3) - timedelta(days=1)
    return FiscalPeriod(year, q, start, end)


def _kst(d: date, hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime.combine(d, time(int(h), int(m)), tzinfo=KST)


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _kor_date(d: date) -> str:
    return f"{d.year}년 {d.month:02d}월 {d.day:02d}일"


def _comma(v: int) -> str:
    return f"{v:,}"


def _r(x: float, unit: int) -> int:
    return round(x / unit) * unit


def _rng(seed: int, *parts: object) -> random.Random:
    """부분마다 따로 씨를 준 난수(한 부분을 바꿔도 다른 부분이 밀리지 않게)."""
    return random.Random(":".join(str(x) for x in (seed, *parts)))  # noqa: S311 — 합성(암호 아님)


def _tick(p: float) -> float:
    """KRX 호가 단위(2023 개편)로 맞춘 가격."""
    for bound, step in ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100),
                        (500_000, 500)):  # fmt: skip
        if p < bound:
            return float(max(step, round(p / step) * step))
    return float(round(p / 1_000) * 1_000)


# ── 종목·회사 명세(§8.4 표 순서) ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Fin:
    """재무 참값 생성 매개변수(분기 번호 i = 0 이 첫 사업연도 1분기)."""

    rev0: float  # 첫 분기 매출(원 — USD 회사는 달러)
    g: float = 0.012  # 분기 성장률
    season: tuple[float, float, float, float] = (-0.04, 0.0, 0.01, 0.05)
    gm: float | None = (
        0.3  # 매출총이익률 — None 이면 매출원가 없음(영업수익·영업비용 — 금융·리츠·스팩)
    )
    opm: tuple[tuple[int, float], ...] = ((0, 0.08),)  # (분기 번호부터, 영업이익률)
    dep: float = 0.03
    amort: float = 0.005
    fi: float = 0.008
    fi_abs: float = 0.0  # 분기 고정 금융수익(스팩 신탁 이자)
    oi: float = 0.003
    oe: float = 0.004
    rate: float = 0.011  # 차입금 분기 이자율
    tax: float = 0.22
    eq0: float = 0.0  # 첫 지배 자본(0 이면 rev0 × 3.6)
    lev: float = 0.8  # 부채 ÷ 자본(처음)
    gl: float = 0.01  # 부채 분기 증가율
    cash: float = 0.12
    sinv: float = 0.05
    borrow: float = 0.45  # 부채 중 차입금 비중
    minority: float = 0.03
    payout: float = 0.25
    ofs: float = 0.62  # 별도 ÷ 연결
    pbr: float = 1.3  # 시세 기준값(시총 ≈ PBR × 지배 자본)
    vol: float = 0.018  # 하루 변동성
    noise: float = 0.03
    sga_fixed: float = 0.0  # 스팩 판관비(분기)


@dataclass(frozen=True)
class _Spec:
    no: int
    code: str
    name: str
    market: Literal["KOSPI", "KOSDAQ", "KONEX"]
    kind: Literal["common", "pref", "spac", "reit"]
    corp: str | None
    cls: str
    acc_mt: int
    induty: str
    est: date
    listed_on: date | None  # None = 마지막 KRX 일에서 20거래일 전(신규상장)
    par: int
    shares: int
    eng: str
    trap: str
    fin: _Fin | None
    fs: tuple[str, ...] = ("CFS", "OFS")
    cadence: Literal["quarterly", "semiannual", "new", "none"] = "quarterly"
    fin_from: int = 2020
    fin_last: tuple[int, int] = (2026, 3)
    currency: str = "KRW"
    watch: bool = True
    consensus: Literal["full", "thin", "empty", "none"] = "full"
    flags: tuple[str, ...] = ()
    treasury: int = 0
    pref_shares: int = 0
    common_no: int = 0
    last_day: date = date(2026, 11, 13)  # 최신 정기보고서(2026 3Q — 3월 결산은 반기) 접수일
    brokers: int = 4
    revision: float = 0.01  # 30일 리비전 속도(FY1·FY2)


_SPECS: Final[tuple[_Spec, ...]] = (
    _Spec(1, "994010", "모의전자", "KOSPI", "common", "99400010", "Y", 12, "26429",
          date(1985, 3, 2), date(1995, 3, 2), 100, 400_000_000, "MOCK ELECTRONICS CO",
          "기준(12월 결산·연결) — dart-report G2 회사 A(원문 셋 — 연결·별도 이중 합산 함정)",
          _Fin(4.2e12, g=0.012, gm=0.36, opm=((0, 0.11), (16, 0.13)), lev=0.5, pbr=1.3),
          treasury=5_000_000, pref_shares=60_000_000, brokers=6, revision=0.07),
    _Spec(2, "994015", "모의전자우", "KOSPI", "pref", None, "Y", 12, "26429",
          date(1985, 3, 2), date(1995, 3, 2), 100, 60_000_000, "MOCK ELECTRONICS CO(1P)",
          "1번의 우선주(끝자리 5·이름 '…우') — DART corp_code 없음, 회사 시총 합",
          None, fs=(), cadence="none", common_no=1),
    _Spec(3, "994020", "모의바이오", "KOSDAQ", "common", "99400020", "K", 12, "21102",
          date(2015, 5, 11), None, 500, 18_000_000, "MOCK BIO",
          "신규상장(20거래일) — 밴드·TTM 없음, 정기보고서는 2026 3Q 하나",
          _Fin(1.5e10, g=0.03, gm=0.55, opm=((0, 0.06),), lev=0.4, pbr=4.0, vol=0.035),
          fs=("OFS",), cadence="new", fin_from=2025, consensus="thin", brokers=2),
    _Spec(4, "994030", "모의정밀", "KOSDAQ", "common", "99400030", "K", 12, "29271",
          date(2001, 7, 1), date(2005, 8, 10), 500, 30_000_000, "MOCK PRECISION",
          "거래정지(마지막 5거래일) — 시세 stale, 2025 3Q 보고서의 3개월값 ≠ 누적 차분(1.5%)",
          _Fin(6e10, opm=((0, 0.09),)), flags=("halted",), revision=-0.03),
    _Spec(5, "994040", "모의스팩1호", "KOSDAQ", "spac", "99400040", "K", 12, "64999",
          date(2025, 1, 6), date(2025, 3, 10), 100, 5_000_000, "MOCK SPAC NO.1",
          "스팩 — 매출 0(PER·PSR spac), KSIC 64(금융)이지만 spac 이 먼저",
          _Fin(0.0, gm=None, opm=((0, 0.0),), dep=0.0, amort=0.0, fi=0.0, fi_abs=8.5e7,
               oi=0.0, oe=0.0, eq0=1.0e10, lev=0.02, minority=0.0, payout=0.0, pbr=1.02,
               vol=0.004, sga_fixed=6e7),
          fs=("OFS",), fin_from=2025, consensus="none"),
    _Spec(6, "994050", "모의리츠", "KOSPI", "reit", "99400050", "Y", 12, "68112",
          date(2018, 2, 1), date(2019, 6, 20), 500, 80_000_000, "MOCK REIT",
          "리츠(반기 보고만) — 재무는 반기·연간 보고서뿐(분기 차분 derived)",
          _Fin(1.1e10, g=0.004, gm=None, opm=((0, 0.55),), dep=0.12, amort=0.0, eq0=4e11,
               lev=1.2, minority=0.0, payout=0.9, pbr=0.95, vol=0.008),
          fs=("OFS",), cadence="semiannual", consensus="none"),
    _Spec(7, "994060", "모의유통", "KOSPI", "common", "99400060", "Y", 3, "47111",
          date(1990, 4, 1), date(1998, 1, 5), 5000, 12_000_000, "MOCK RETAIL",
          "3월 결산 — 회계연도 라벨 FY26 2Q (26.07~26.09)·TTM, 최신 보고서 = 2026 회계연도 반기",
          _Fin(8e11, g=0.006, gm=0.28, opm=((0, 0.04),), pbr=0.6),
          fin_last=(2026, 2), revision=0.17),
    _Spec(8, "994070", "모의소재", "KOSDAQ", "common", "99400070", "K", 12, "20119",
          date(2003, 3, 3), date(2010, 12, 1), 500, 25_000_000, "MOCK MATERIALS",
          "적자(2025 3Q 부터 영업손실) — PER loss, 시나리오 PBR, 컨센 FY1 순이익 부호 전환(TURN)",
          _Fin(5e10, opm=((0, 0.07), (22, -0.06)), pbr=1.3), brokers=3, last_day=TODAY),
    _Spec(9, "994080", "모의테크", "KOSDAQ", "common", "99400080", "K", 12, "58221",
          date(2008, 9, 9), date(2014, 11, 20), 500, 15_000_000, "MOCK TECH",
          "재무 미제출(정기보고서 없음)·관리종목 — 재무 no_filings, 공시는 있다",
          None, fs=(), cadence="none", consensus="empty", flags=("managed",)),
    _Spec(10, "994090", "모의코넥스", "KONEX", "common", "99400090", "N", 12, "26519",
          date(2012, 2, 2), date(2016, 7, 25), 500, 6_000_000, "MOCK KONEX",
          "코넥스(반기 보고만) — 컨센 konex_no_consensus",
          _Fin(4e9, opm=((0, 0.05),), pbr=1.0, vol=0.012, minority=0.0),
          fs=("OFS",), cadence="semiannual", consensus="none"),
    _Spec(11, "994100", "모의금융지주", "KOSPI", "common", "99400100", "Y", 12, "64992",
          date(2001, 9, 1), date(2001, 9, 10), 5000, 300_000_000, "MOCK FINANCIAL GROUP",
          "은행지주(금융업) — 매출 = 영업수익, EV/EBITDA financial_sector",
          _Fin(3.0e12, g=0.008, gm=None, opm=((0, 0.33),), dep=0.01, amort=0.004, rate=0.0005,
               eq0=2.5e13, lev=11.0, gl=0.008, cash=0.05, borrow=0.6, minority=0.05,
               payout=0.3, pbr=0.45, vol=0.013),
          brokers=5),
    _Spec(12, "994110", "모의기계", "KOSDAQ", "common", "99400110", "K", 12, "29199",
          date(1999, 10, 10), date(2007, 4, 16), 500, 20_000_000, "MOCK MACHINERY",
          "연결 미작성(OFS 만) — fs_div=OFS 폴백, dart-report G2 회사 B(천원 단위 주석)",
          _Fin(3.5e10, opm=((0, 0.08),), pbr=1.0, minority=0.0), fs=("OFS",), brokers=3),
    _Spec(13, "994120", "모의광학", "KOSDAQ", "common", "99400120", "K", 12, "27301",
          date(2006, 6, 6), date(2012, 3, 19), 500, 10_000_000, "MOCK OPTICS",
          "자본잠식(2026 1Q 부터 자본총계 < 0 — 11-13 PIT 상태표 = 2026 3Q(11-13 접수)) — PBR "
          "capital_impairment",
          _Fin(1.2e10, g=0.0, opm=((0, 0.03), (16, -0.25)), eq0=2.4e10, lev=1.5, gl=0.02,
               minority=0.0, payout=0.0, pbr=1.5),
          fs=("OFS",), consensus="empty"),
    _Spec(14, "994130", "모의모빌리티", "KOSDAQ", "common", "99400130", "K", 12, "30332",
          date(2004, 1, 15), date(2009, 10, 1), 500, 40_000_000, "MOCK MOBILITY",
          "CB 3·BW 1(리픽싱 2회·일부 청구·일부 행사)·제3자배정 유증 — 오버행 비율 손계산",
          _Fin(7e10, opm=((0, 0.05),), pbr=1.4), brokers=3, last_day=TODAY),
    _Spec(15, "994140", "모의에너지", "KOSPI", "common", "99400140", "Y", 12, "35112",
          date(1975, 6, 1), date(1988, 8, 8), 5000, 50_000_000, "MOCK ENERGY",
          "내부자 대량 매도 + 5% 보고(정정 포함) — 창 합계 손계산, 관심종목 잠정실적 알림 1통",
          _Fin(1.6e12, g=0.012, gm=0.18, opm=((0, 0.06), (26, 0.085)), pbr=0.8),
          brokers=5, revision=-0.18, last_day=TODAY),
    _Spec(16, "994150", "모의소프트", "KOSDAQ", "common", "99400150", "K", 12, "58221",
          date(2010, 10, 10), date(2018, 5, 15), 100, 16_000_000, "MOCK SOFT",
          "관심 밖·컨센서스 없는 소형 코스닥(상세 층 아님) — EV/EBITDA not_detail_tier",
          _Fin(1.5e10, opm=((0, 0.10), (26, 0.16)), pbr=1.6), watch=False, consensus="none",
          last_day=TODAY),
    _Spec(17, "994160", "모의화학", "KOSPI", "common", "99400160", "Y", 12, "20111",
          date(1980, 1, 1), date(1990, 2, 2), 5000, 20_000_000, "MOCK CHEMICAL",
          "액면분할 직후(5:1, 2026-10-26) — 주당 값·밴드 연속(배수는 총액 ÷ 총액)",
          _Fin(9e11, gm=0.2, opm=((0, 0.07),), pbr=0.7)),
    _Spec(18, "99K170", "모의로보틱스", "KOSDAQ", "common", "99400170", "K", 12, "29280",
          date(2016, 3, 3), date(2024, 5, 20), 100, 22_000_000, "MOCK ROBOTICS",
          "영숫자 단축코드(99K170) — 코드 정규식·종류 판정, 잠정실적 2열 폼(전년동기 없음)",
          _Fin(2e10, g=0.02, opm=((0, 0.07),), pbr=2.5, vol=0.025), brokers=3, last_day=TODAY),
    _Spec(19, "994180", "모의홀딩스", "KOSDAQ", "common", "99400180", "K", 12, "26429",
          date(2009, 4, 4), date(2011, 6, 30), 500, 60_000_000, "MOCK HOLDINGS LTD",
          "외국기업(재무 통화 USD) — 환산하지 않음, 배수 currency_mismatch",
          _Fin(2.5e7, opm=((0, 0.08),), pbr=0.8), currency="USD", consensus="empty",
          last_day=TODAY),
    _Spec(20, "994190", "모의건설", "KOSDAQ", "common", "99400190", "K", 12, "41221",
          date(1994, 5, 5), date(2000, 12, 12), 500, 30_000_000, "MOCK CONSTRUCTION",
          "정리매매(상장폐지 결정 — 마지막 7거래일) — 상태 배지, 나머지 정상(재무는 2026 반기까지)",
          _Fin(4e10, opm=((0, 0.03),), pbr=0.5), fin_last=(2026, 2), consensus="empty",
          flags=("liquidation",), last_day=date(2026, 8, 13)),
)  # fmt: skip

_NOISE_CORPS: Final = tuple(f"994009{i:02d}" for i in range(10))
_NOISE_TITLES: Final = (
    "기타경영사항(자율공시)",
    "타법인주식및출자증권취득결정",
    "감사보고서제출",
    "증권신고서(지분증권)",
    "투자설명서",
    "특수관계인에대한자금대여",
    "소송등의제기ㆍ신청(경영권분쟁소송)",
    "최대주주등소유주식변동신고서",
    "기업설명회(IR)개최(안내공시)",
    "주주총회소집결의",
)


@dataclass(frozen=True)
class WorldStock:
    """종목 하나(§8.4 표의 한 행). `record()` 의 `_source` 가 SYNTHETIC."""

    no: int
    code: str
    name: str
    market: Literal["KOSPI", "KOSDAQ", "KONEX"]
    kind: Literal["common", "pref", "spac", "reit"]
    common_code: str
    corp_code: str | None  # 우선주는 None(DART 고유번호 없음 — 회사는 company_corp)
    company_corp: str
    listed_on: date
    par: int
    watch: bool
    consensus: Literal["full", "thin", "empty", "none"]
    flags: tuple[str, ...]  # 마지막 KRX 일의 상태(halted·managed·liquidation)
    trap: str
    source: str = SYNTHETIC

    @property
    def detail_tier(self) -> bool:
        """상세 층 = 관심종목 ∪ 컨센서스 top_n ∪ 공개 데모(완료 시험 설정 top_n=0·데모 없음)."""
        return self.watch

    def record(self) -> dict[str, Any]:
        return {
            "_source": self.source,
            "no": self.no,
            "code": self.code,
            "name": self.name,
            "market": self.market,
            "kind": self.kind,
            "common_code": self.common_code,
            "corp_code": self.corp_code,
            "company_corp": self.company_corp,
            "listed_on": self.listed_on.isoformat(),
            "watch": self.watch,
            "detail_tier": self.detail_tier,
            "consensus": self.consensus,
            "flags": list(self.flags),
            "trap": self.trap,
        }


@dataclass(frozen=True)
class WorldCorp:
    """DART 회사 하나(상장 19 + 비상장 잡음 10)."""

    corp_code: str
    corp_name: str
    eng_name: str
    stock_code: str | None
    corp_cls: str
    acc_mt: int
    induty_code: str
    est_dt: date
    currency: str
    fs_divs: tuple[str, ...]
    cadence: Literal["quarterly", "semiannual", "new", "none"]
    is_financial: bool
    source: str = SYNTHETIC


@dataclass(frozen=True)
class QuarterTruth:
    """분기 단독 참값(3개월) + 분기말 상태표 — R·V 시험의 독립 기대값(원 단위, USD 회사는 달러).

    `notes`: 주석 항목의 3개월 값 — 'cost'(비용의 성격별), 'fin_income'·'fin_cost'·'other_income'·
    'other_expense'(영업외손익). 각 표의 합 = 손익계산서의 그 줄.
    """

    corp_code: str
    fs_div: Literal["CFS", "OFS"]
    period: FiscalPeriod
    currency: str
    revenue: int
    cogs: int | None
    gross_profit: int | None
    sgna: int
    op: int
    fin_income: int
    fin_cost: int
    other_income: int
    other_expense: int
    pretax: int
    tax: int
    ni: int
    ni_owner: int
    dep: int
    amort: int
    total_assets: int
    total_liabilities: int
    total_equity: int
    equity_owner: int
    minority: int
    cash: int
    short_inv: int
    short_borrow: int
    long_borrow: int
    bonds: int
    current_ltd: int
    capital: int
    common_issued: int
    pref_issued: int
    treasury: int
    eps: float
    notes: Mapping[str, tuple[tuple[str, int], ...]] = field(
        default_factory=dict[str, tuple[tuple[str, int], ...]]
    )

    @property
    def key(self) -> tuple[int, int]:
        return (self.period.year, self.period.q)

    @property
    def total_debt(self) -> int:
        return self.short_borrow + self.long_borrow + self.bonds + self.current_ltd

    @property
    def ebitda(self) -> int:
        return self.op + self.dep + self.amort


@dataclass(frozen=True)
class WorldDisclosure:
    """공시 목록 한 행(+ 세계 안 이름 `ident`·'접수' 시각 `posted_at` — DART 응답에는 시각이
    없다)."""

    ident: str
    rcept_no: str
    corp_code: str
    corp_name: str
    stock_code: str | None
    corp_cls: str
    title: str
    filer: str
    rcept_dt: date
    posted_at: datetime
    pblntf_ty: str
    rm: str
    kind: str  # 세계의 분류(periodic·prelim·overhang …) — 수집기 kind 와 같을 필요는 없다

    def item(self) -> dict[str, str]:
        """`list.json` 의 `list` 한 행(DART 개발가이드 필드)."""
        return {
            "corp_code": self.corp_code,
            "corp_name": self.corp_name,
            "stock_code": self.stock_code or "",
            "corp_cls": self.corp_cls,
            "report_nm": self.title,
            "rcept_no": self.rcept_no,
            "flr_nm": self.filer,
            "rcept_dt": _ymd(self.rcept_dt),
            "rm": self.rm,
        }


@dataclass(frozen=True)
class _Report:
    """정기보고서 한 건(분기 누적 = 1분기 … q 분기)."""

    corp_code: str
    year: int
    q: int
    reprt_code: str
    ident: str  # 공시 ident(정정본이 있으면 `fix` 가 그것)
    fix: str | None = None


# ── 세계 ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class P4World:
    """합성 20종목 세계. 만들기는 `build_world(seed)` — 직접 생성하지 않는다."""

    seed: int
    clock: datetime
    today: date
    last_krx_day: date
    trading_days: tuple[date, ...]
    stocks: tuple[WorldStock, ...]
    corps: tuple[WorldCorp, ...]
    disclosures: tuple[WorldDisclosure, ...]
    quarters: Mapping[tuple[str, str], tuple[QuarterTruth, ...]]  # (corp, fs_div) → 분기 참값
    profiles: tuple[CorpProfile, ...]
    prelims: tuple[PrelimRow, ...]
    schedule: tuple[ScheduleRow, ...]
    overhang_events: tuple[OverhangEvent, ...]
    overhang_adjusts: tuple[OverhangAdjust, ...]
    insider_reports: tuple[InsiderReport, ...]
    major_reports: tuple[MajorHolderReport, ...]
    buybacks: tuple[BuybackEvent, ...]
    share_counts: tuple[ShareCount, ...]
    share_classes: tuple[ShareClass, ...]
    splits: tuple[SplitEvent, ...]
    watchlist: tuple[WatchItem, ...]
    consensus: tuple[ConsensusRow, ...]
    opinions: tuple[OpinionRow, ...]
    insider_base: Mapping[str, str]  # 소유보고 정정 접수번호 → 원 보고 접수번호(정정 짝)
    _reports: tuple[_Report, ...]
    _structured: Mapping[str, tuple[tuple[str, date, Mapping[str, str]], ...]]
    _docs: Mapping[str, tuple[tuple[str, str], ...]]
    _prices: Mapping[
        str, tuple[tuple[date, float, float, float, float, int, int, int, tuple[str, ...]], ...]
    ]
    _investors: tuple[InvestorDay, ...]

    # ── 찾기 ──

    def stock(self, code: str) -> WorldStock:
        for s in self.stocks:
            if s.code == code:
                return s
        raise KeyError(code)

    def by_no(self, no: int) -> WorldStock:
        return self.stocks[no - 1]

    def corp(self, corp_code: str) -> WorldCorp:
        for c in self.corps:
            if c.corp_code == corp_code:
                return c
        raise KeyError(corp_code)

    def disclosure(self, ident: str) -> WorldDisclosure:
        for d in self.disclosures:
            if d.ident == ident:
                return d
        raise KeyError(ident)

    def rcept(self, ident: str) -> str:
        return self.disclosure(ident).rcept_no

    def disclosure_by_rcept(self, rcept_no: str) -> WorldDisclosure:
        for d in self.disclosures:
            if d.rcept_no == rcept_no:
                return d
        raise KeyError(rcept_no)

    def disclosures_on(self, day: date, *, now: datetime | None = None) -> list[WorldDisclosure]:
        """그날 접수(시각 ≤ now) — 접수 순서."""
        return [
            d for d in self.disclosures if d.rcept_dt == day and (now is None or d.posted_at <= now)
        ]

    def quarter_truth(self, corp_code: str, fs_div: str = "CFS") -> tuple[QuarterTruth, ...]:
        """회사의 분기 참값(그 기준이 없으면 빈 튜플). CFS 를 묻고 회사가 OFS 만이면 OFS."""
        got = self.quarters.get((corp_code, fs_div))
        if got is None and fs_div == "CFS":
            got = self.quarters.get((corp_code, "OFS"))
        return got or ()

    def primary_fs(self, corp_code: str) -> str | None:
        c = self.corp(corp_code)
        return c.fs_divs[0] if c.fs_divs else None

    # ── 시세(KRX) ──

    def bars(self, code: str | None = None) -> list[Bar]:
        """일봉(krx, 비수정). 거래정지일은 행이 없다(차트 마지막 = 정지 전)."""
        out: list[Bar] = []
        for c in [code] if code else [s.code for s in self.stocks]:
            for d, o, h, lo, cl, vol, tv, _sh, flags in self._prices[c]:
                if "halted" in flags:
                    continue
                out.append(Bar(c, d, o, h, lo, cl, vol, tv, "krx", "KRX", Quality.OK))
        return out

    def snaps(self, day: date | None = None, *, code: str | None = None) -> list[Snap]:
        """종목 일자 스냅(krx). 상태(status_flags)는 참값 — 실제 KRX 일별에는 없고 KIS 가 준다."""
        out: list[Snap] = []
        for s in self.stocks:
            if code is not None and s.code != code:
                continue
            prev: float | None = None
            for d, _o, _h, _lo, cl, vol, tv, sh, flags in self._prices[s.code]:
                chg = 0.0 if prev is None else round((cl / prev - 1) * 100, 2)
                prev = cl
                if day is not None and d != day:
                    continue
                out.append(
                    Snap(s.code, d, s.name, s.market, s.kind, cl, chg, vol, tv, False,
                         int(cl * sh), sh, flags, "krx", "KRX", Quality.OK)
                )  # fmt: skip
        return out

    def universe(self) -> list[UniverseRow]:
        out: list[UniverseRow] = []
        for s in self.stocks:
            out.append(UniverseRow(
                s.code, self.last_krx_day, s.name, s.market, s.kind, s.listed_on, "krx",
                Quality.OK, {"par": s.par, "_source": SYNTHETIC},
            ))  # fmt: skip
        return out

    def investors(self, code: str | None = None) -> list[InvestorDay]:
        """KIS 투자자 일별(마지막 20거래일, 정지일 없음) — 검산 ①② 가 성립하게 만든다."""
        return [r for r in self._investors if code is None or r.code == code]

    def close(self, code: str, day: date) -> float | None:
        """그날(또는 그 전 마지막) 종가. 상장 전이면 None."""
        last: float | None = None
        for row in self._prices[code]:
            if row[0] > day:
                break
            last = row[4]
        return last

    def shares_on(self, code: str, day: date) -> int | None:
        last: int | None = None
        for row in self._prices[code]:
            if row[0] > day:
                break
            last = row[7]
        return last

    def krx_daily_rows(self, day: date) -> dict[str, list[dict[str, str]]]:
        """KRX OPEN API 주식 일별 응답 행(엔드포인트 → 행). 정지일은 시·고·저 0, 거래량 0(KRX
        관행)."""
        out: dict[str, list[dict[str, str]]] = {
            "/sto/stk_bydd_trd": [],
            "/sto/ksq_bydd_trd": [],
            "/sto/knx_bydd_trd": [],
        }
        ep = {"KOSPI": "/sto/stk_bydd_trd", "KOSDAQ": "/sto/ksq_bydd_trd",
              "KONEX": "/sto/knx_bydd_trd"}  # fmt: skip
        for s in self.stocks:
            prev: float | None = None
            for d, o, h, lo, cl, vol, tv, sh, flags in self._prices[s.code]:
                if d == day:
                    halted = "halted" in flags
                    diff = 0.0 if prev is None else cl - prev
                    out[ep[s.market]].append({
                        "BAS_DD": _ymd(d), "ISU_CD": s.code, "ISU_NM": s.name,
                        "MKT_NM": s.market, "SECT_TP_NM": _sect(s),
                        "TDD_CLSPRC": f"{cl:.0f}", "CMPPREVDD_PRC": f"{diff:.0f}",
                        "FLUC_RT": f"{0.0 if not prev else diff / prev * 100:.2f}",
                        "TDD_OPNPRC": "0" if halted else f"{o:.0f}",
                        "TDD_HGPRC": "0" if halted else f"{h:.0f}",
                        "TDD_LWPRC": "0" if halted else f"{lo:.0f}",
                        "ACC_TRDVOL": str(vol), "ACC_TRDVAL": str(tv),
                        "MKTCAP": str(int(cl * sh)), "LIST_SHRS": str(sh),
                    })  # fmt: skip
                    break
                prev = cl
        return out

    def krx_base_rows(self, day: date) -> dict[str, list[dict[str, str]]]:
        """KRX 종목 기본정보 행(코스피·코스닥 — 코넥스는 가짜 KRX 에 엔드포인트가 없다)."""
        out: dict[str, list[dict[str, str]]] = {
            "/sto/stk_isu_base_info": [],
            "/sto/ksq_isu_base_info": [],
        }
        for s in self.stocks:
            if s.market == "KONEX" or s.listed_on > day:
                continue
            ep = "/sto/stk_isu_base_info" if s.market == "KOSPI" else "/sto/ksq_isu_base_info"
            out[ep].append({
                "ISU_CD": f"KR7{s.code}00{s.no % 10}", "ISU_SRT_CD": s.code,
                "ISU_NM": s.name + ("" if s.kind == "pref" else "보통주"), "ISU_ABBRV": s.name,
                "ISU_ENG_NM": self.corp(s.company_corp).eng_name, "LIST_DD": _ymd(s.listed_on),
                "MKT_TP_NM": s.market, "SECUGRP_NM": _secugrp(s), "SECT_TP_NM": _sect(s),
                "KIND_STKCERT_TP_NM": "우선주" if s.kind == "pref" else "보통주",
                "PARVAL": str(s.par), "LIST_SHRS": str(self.shares_on(s.code, day) or 0),
            })  # fmt: skip
        return out

    # ── DART 응답(가짜 서버 — 묶음 F 가 tests/fakes/dart_server.py 에서 서빙) ──

    def dart_json(
        self, endpoint: str, params: Mapping[str, str], *, now: datetime
    ) -> dict[str, Any]:
        """DART JSON 엔드포인트(`list.json` 등) 응답 본문. 모르는 엔드포인트는 KeyError."""
        name = endpoint.removesuffix(".json")
        if name == "list":
            return self._list(params, now)
        if name == "company":
            return self._company(params.get("corp_code", ""))
        if name in _STRUCTURED:
            return self._structured_reply(name, params, now)
        if name in ("elestock", "majorstock"):
            return self._ownership(name, params.get("corp_code", ""), now)
        if name == "stockTotqySttus":
            return self._share_total(params, now)
        if name == "hyslrSttus":
            return self._largest_holder(params, now)
        if name == "fnlttSinglAcntAll":
            return self._single_all(params, now)
        if name == "fnlttMultiAcnt":
            return self._multi(params, now)
        raise KeyError(endpoint)

    def dart_bytes(
        self, endpoint: str, params: Mapping[str, str], *, now: datetime
    ) -> bytes | None:
        """`corpCode.xml`(zip) · `document.xml`(원문 zip — 접수 전이면 None)."""
        if endpoint == "corpCode.xml":
            return self.corp_zip()
        if endpoint == "document.xml":
            files = self.documents(params.get("rcept_no", ""), now=now)
            if not files:
                return None
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for fname, html in files:
                    zf.writestr(fname, html.encode("utf-8"))
            return buf.getvalue()
        raise KeyError(endpoint)

    def corp_zip(self) -> bytes:
        rows = []
        for c in self.corps:
            rows.append(
                f"<list><corp_code>{c.corp_code}</corp_code><corp_name>{c.corp_name}</corp_name>"
                f"<corp_eng_name>{c.eng_name}</corp_eng_name>"
                f"<stock_code>{c.stock_code or ' '}</stock_code>"
                f"<modify_date>20261001</modify_date></list>"
            )
        xml = (
            f'<?xml version="1.0" encoding="UTF-8"?><!-- {SYNTHETIC} --><result>'
            f"{''.join(rows)}</result>"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("CORPCODE.xml", xml.encode("utf-8"))
        return buf.getvalue()

    def documents(
        self, rcept_no: str, *, now: datetime | None = None
    ) -> tuple[tuple[str, str], ...]:
        """원문 파일 [(파일명, HTML)] — 접수 전(now 기준)이면 빈 튜플."""
        d = next((x for x in self.disclosures if x.rcept_no == rcept_no), None)
        if d is None or (now is not None and d.posted_at > now):
            return ()
        got = self._docs.get(rcept_no)
        if got:
            return got
        return ((f"{rcept_no}.xml", _generic_doc(d)),)

    def _visible(self, now: datetime) -> list[WorldDisclosure]:
        return [d for d in self.disclosures if d.posted_at <= now]

    def _list(self, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        bgn = p.get("bgn_de") or _ymd(now.date())
        end = p.get("end_de") or bgn
        rows = [
            d
            for d in self._visible(now)
            if bgn <= _ymd(d.rcept_dt) <= end
            and (not p.get("corp_code") or d.corp_code == p["corp_code"])
            and (not p.get("corp_cls") or d.corp_cls == p["corp_cls"])
            and (not p.get("pblntf_ty") or d.pblntf_ty == p["pblntf_ty"])
        ]
        rows.sort(key=lambda d: (d.posted_at, d.rcept_no), reverse=True)  # 접수 내림차순(#28)
        if not rows:
            return _no_data()
        count = max(1, min(100, int(p.get("page_count") or 10)))
        page = max(1, int(p.get("page_no") or 1))
        total_page = math.ceil(len(rows) / count)
        chunk = rows[(page - 1) * count : page * count]
        return {
            "_source": SYNTHETIC,
            "status": "000",
            "message": STATUS_TEXT["000"],
            "page_no": page,
            "page_count": count,
            "total_count": len(rows),
            "total_page": total_page,
            "list": [d.item() for d in chunk],
        }

    def _company(self, corp_code: str) -> dict[str, Any]:
        try:
            c = self.corp(corp_code)
        except KeyError:
            return _no_data()
        return {
            "_source": SYNTHETIC,
            "status": "000",
            "message": STATUS_TEXT["000"],
            "corp_code": c.corp_code,
            "corp_name": c.corp_name,
            "corp_name_eng": c.eng_name,
            "stock_name": c.corp_name,
            "stock_code": c.stock_code or "",
            "ceo_nm": "가상대표",
            "corp_cls": c.corp_cls,
            "jurir_no": "0000000000000",
            "bizr_no": "0000000000",
            "adres": "합성시 가상구 모의로 1",
            "hm_url": "",
            "ir_url": "",
            "phn_no": "",
            "fax_no": "",
            "induty_code": c.induty_code,
            "est_dt": _ymd(c.est_dt),
            "acc_mt": f"{c.acc_mt:02d}",
        }

    def _structured_reply(self, name: str, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        corp = p.get("corp_code", "")
        bgn = p.get("bgn_de") or "19000101"
        end = p.get("end_de") or _ymd(now.date())
        posted = {d.rcept_no: d.posted_at for d in self.disclosures}
        rows = [
            dict(row)
            for rcept, day, row in self._structured.get(name, ())
            if row["corp_code"] == corp and bgn <= _ymd(day) <= end and posted[rcept] <= now
        ]
        return _ok(rows) if rows else _no_data()

    def _ownership(self, name: str, corp: str, now: datetime) -> dict[str, Any]:
        posted = {d.rcept_no: d.posted_at for d in self.disclosures}
        rows = [
            dict(row)
            for rcept, _day, row in self._structured.get(name, ())
            if row["corp_code"] == corp and posted[rcept] <= now
        ]
        return _ok(rows) if rows else _no_data()

    def _report_visible(self, corp: str, year: int, reprt: str, now: datetime) -> _Report | None:
        for r in self._reports:
            if (r.corp_code, r.year, r.reprt_code) == (corp, year, reprt):
                return r if self.disclosure(r.ident).posted_at <= now else None
        return None

    def _report_rcept(self, r: _Report, now: datetime) -> str:
        """정정본이 접수됐으면 그 접수번호(DART 는 최신 판을 준다)."""
        if r.fix is not None and self.disclosure(r.fix).posted_at <= now:
            return self.rcept(r.fix)
        return self.rcept(r.ident)

    def _share_total(self, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        corp = p.get("corp_code", "")
        r = self._report_visible(corp, int(p.get("bsns_year") or 0), p.get("reprt_code", ""), now)
        if r is None:
            return _no_data()
        c = self.corp(corp)
        sc = next(x for x in self.share_counts if x.rcept_no == self.rcept(r.ident))
        rcept = self._report_rcept(r, now)
        rows = []
        for se, issued, treas in (
            ("보통주", sc.common_issued, sc.treasury_common),
            ("우선주", sc.pref_issued, sc.treasury_pref),
        ):
            rows.append(_share_row(c, rcept, se, issued, treas, sc.period_end))
        total_issued = (sc.common_issued or 0) + (sc.pref_issued or 0)
        total_treas = (sc.treasury_common or 0) + (sc.treasury_pref or 0)
        rows.append(_share_row(c, rcept, "합계", total_issued, total_treas, sc.period_end))
        return _ok(rows)

    def _largest_holder(self, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        """최대주주 현황 — 최대주주 하나가 보통주 30%(합성), 종류별 '계' 행(실측 모양 10-21)."""
        corp = p.get("corp_code", "")
        r = self._report_visible(corp, int(p.get("bsns_year") or 0), p.get("reprt_code", ""), now)
        if r is None:
            return _no_data()
        c = self.corp(corp)
        sc = next(x for x in self.share_counts if x.rcept_no == self.rcept(r.ident))
        held = _comma((sc.common_issued or 0) * 3 // 10) if sc.common_issued else "-"
        base = {"rcept_no": self._report_rcept(r, now), "corp_cls": c.corp_cls,
                "corp_code": c.corp_code, "corp_name": c.corp_name,
                "stlm_dt": sc.period_end.isoformat()}  # fmt: skip
        return _ok([
            {**base, "nm": "합성최대주주", "relate": "본인", "stock_knd": "보통주",
             "trmend_posesn_stock_co": held},
            {**base, "nm": "계", "relate": "-", "stock_knd": "보통주",
             "trmend_posesn_stock_co": held},
        ])  # fmt: skip

    def _single_all(self, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        corp = p.get("corp_code", "")
        fs = p.get("fs_div") or "CFS"
        year = int(p.get("bsns_year") or 0)
        r = self._report_visible(corp, year, p.get("reprt_code", ""), now)
        if r is None or (corp, fs) not in self.quarters:
            return _no_data()
        return _ok(self._statement_rows(r, fs, self._report_rcept(r, now)))

    def _multi(self, p: Mapping[str, str], now: datetime) -> dict[str, Any]:
        year = int(p.get("bsns_year") or 0)
        reprt = p.get("reprt_code", "")
        rows: list[dict[str, str]] = []
        for corp in [x.strip() for x in (p.get("corp_code") or "").split(",") if x.strip()][:100]:
            r = self._report_visible(corp, year, reprt, now)
            if r is None:
                continue
            for fs in self.corp(corp).fs_divs:
                rows.extend(self._multi_rows(r, fs, self._report_rcept(r, now)))
        return _ok(rows) if rows else _no_data()

    # ── 재무제표 행 ──

    def _span(self, r: _Report, fs: str) -> list[QuarterTruth]:
        qs = {q.key: q for q in self.quarters[(r.corp_code, fs)]}
        return [qs[(r.year, k)] for k in range(1, r.q + 1)]

    def _statement_rows(self, r: _Report, fs: str, rcept: str) -> list[dict[str, str]]:
        c = self.corp(r.corp_code)
        span = self._span(r, fs)
        last = span[-1]
        annual = r.reprt_code == "11011"
        nth = r.year - c.est_dt.year + 1
        label = {"11013": "1분기", "11012": "반기", "11014": "3분기", "11011": ""}[r.reprt_code]
        th_nm = f"제 {nth} 기" + (f" {label}" if label else "")
        is_div = ("CIS", "포괄손익계산서") if c.corp_code == "99400010" else ("IS", "손익계산서")
        rows: list[dict[str, str]] = []
        ordn = 0

        def put(
            sj: tuple[str, str], acc_id: str, acc_nm: str, amount: str, add: str | None
        ) -> None:
            nonlocal ordn
            ordn += 1
            row = {
                "rcept_no": rcept, "reprt_code": r.reprt_code, "bsns_year": str(r.year),
                "corp_code": c.corp_code, "sj_div": sj[0], "sj_nm": sj[1],
                "account_id": acc_id, "account_nm": acc_nm, "account_detail": "-",
                "thstrm_nm": th_nm, "thstrm_amount": amount, "ord": str(ordn),
                "currency": c.currency,
            }  # fmt: skip
            if add is not None:
                row["thstrm_add_amount"] = add
            rows.append(row)

        bs = ("BS", "재무상태표")
        for key, acc_id, acc_nm in _BS_ACCOUNTS:
            if fs == "OFS" and key in ("equity_owner", "minority"):
                continue
            put(bs, acc_id, acc_nm, str(getattr(last, key)), None)
        financial = last.cogs is None
        for key, acc_id, acc_nm in _IS_ACCOUNTS:
            if financial and key in ("cogs", "gross_profit"):
                continue
            if fs == "OFS" and key in ("ni_owner", "ni_minority"):
                continue
            if financial and key == "revenue":
                acc_id, acc_nm = "dart_OperatingRevenue", "영업수익"
            if financial and key == "sgna":
                acc_id, acc_nm = "dart_OperatingExpenses", "영업비용"
            if key == "eps":
                three = last.eps
                cum = round(sum(q.eps for q in span), 2)
                fmt = _eps_fmt(c.currency)
                put(is_div, acc_id, acc_nm, fmt(cum if annual else three),
                    None if annual else fmt(cum))  # fmt: skip
                continue
            vals = [_is_value(q, key) for q in span]
            cum_v = sum(vals)
            three_v = vals[-1]
            if (c.corp_code, r.year, r.reprt_code, key) == ("99400030", 2025, "11014", "revenue"):
                three_v = round(three_v * 1.015)  # 3개월값 ≠ 누적 차분(정정 반영 차이 함정 — §4.5)
            put(is_div, acc_id, acc_nm, str(cum_v if annual else three_v),
                None if annual else str(cum_v))  # fmt: skip
        cf = ("CF", "현금흐름표")
        put(cf, "ifrs-full_AdjustmentsForDepreciationExpense", "감가상각비",
            str(sum(q.dep for q in span)), None)  # fmt: skip
        put(cf, "ifrs-full_AdjustmentsForAmortisationExpense", "무형자산상각비",
            str(sum(q.amort for q in span)), None)  # fmt: skip
        return rows

    def _multi_rows(self, r: _Report, fs: str, rcept: str) -> list[dict[str, str]]:
        c = self.corp(r.corp_code)
        span = self._span(r, fs)
        last = span[-1]
        annual = r.reprt_code == "11011"
        p = last.period
        th_dt = f"{p.end:%Y.%m.%d} 현재"
        fs_nm = "연결재무제표" if fs == "CFS" else "재무제표"
        rows: list[dict[str, str]] = []
        cur_assets = round(last.total_assets * 0.45)
        cur_liab = round(last.total_liabilities * 0.55)
        bs_vals = (
            ("유동자산", cur_assets),
            ("비유동자산", last.total_assets - cur_assets),
            ("자산총계", last.total_assets),
            ("유동부채", cur_liab),
            ("비유동부채", last.total_liabilities - cur_liab),
            ("부채총계", last.total_liabilities),
            ("자본금", last.capital),
            ("이익잉여금", last.equity_owner - last.capital),
            ("자본총계", last.total_equity),
        )
        rev_nm = "영업수익" if last.cogs is None else "매출액"
        is_vals = (
            (rev_nm, [q.revenue for q in span]),
            ("영업이익", [q.op for q in span]),
            ("법인세차감전 순이익", [q.pretax for q in span]),
            ("당기순이익", [q.ni for q in span]),
        )
        ordn = 0
        for nm, v in bs_vals:
            ordn += 1
            rows.append(_multi_row(c, r, rcept, fs, fs_nm, ("BS", "재무상태표"), nm, th_dt,
                                   _comma(v), None, ordn))  # fmt: skip
        start = fiscal_quarter(r.year, 1, c.acc_mt).start
        for nm, vals in is_vals:
            ordn += 1
            amount = sum(vals) if annual else vals[-1]
            rows.append(_multi_row(
                c, r, rcept, fs, fs_nm, ("IS", "손익계산서"), nm,
                f"{(start if annual else p.start):%Y.%m.%d} ~ {p.end:%Y.%m.%d}",
                _comma(amount), None if annual else _comma(sum(vals)), ordn,
            ))  # fmt: skip
        return rows

    # ── 컨센서스(KIS 의미값) ──

    def consensus_rows(self, code: str, snap_date: date | None = None) -> list[ConsensusRow]:
        return [
            r
            for r in self.consensus
            if r.code == code and (snap_date is None or r.snap_date == snap_date)
        ]

    def opinion_rows(self, code: str, *, upto: date | None = None) -> list[OpinionRow]:
        return [
            r for r in self.opinions if r.code == code and (upto is None or r.opinion_date <= upto)
        ]

    # ── 관심종목 ──

    def sd_watchlist_doc(self) -> list[dict[str, str]]:
        """SD `cache/server_watchlist.json` 모양(브라우저 localStorage 목록) — 미국 티커 1건
        섞음."""
        out = [
            {
                "code": w.code,
                "name": w.name_at_add or "",
                "market": "kr",
                "addedAt": w.added_at.isoformat() if w.added_at else "",
            }
            for w in self.watchlist
        ]
        out.insert(3, {"code": "SYNQ", "name": "Synthetic US", "market": "us",
                       "addedAt": "2026-02-02T10:00:00+09:00"})  # fmt: skip
        return out

    # ── 손계산 기대값(엔진과 따로 쓴 산수) ──

    def expect_overhang(self, as_of: date | None = None) -> dict[str, Any]:
        """#14 의 t 시점 오버행(§4.3 정의를 그대로 손으로 — 잔여 ÷ 현재 가격, 버림)."""
        t = as_of or self.today
        corp = self.by_no(14).company_corp
        per: dict[str, int] = {}
        exercisable = 0
        potential = 0
        for ev in self.overhang_events:
            if ev.corp_code != corp or ev.rcept_dt > t:
                continue
            adj = [
                a
                for a in self.overhang_adjusts
                if a.target_rcept_no == ev.rcept_no and (a.effective_date or a.rcept_dt) <= t
            ]
            if ev.instrument is Instrument.PAID_IN:
                active = ev.listing_date is not None and t < ev.listing_date
                shares = (ev.shares or 0) if active else 0
            else:
                # docs/metrics.md §12 — 권면총액 − Σ(청구·상환 금액, effective_date ≤ t)
                face = (ev.face_value or 0) - sum(
                    a.amount or 0 for a in adj if a.kind in ("exercise", "redemption")
                )
                prices = [a for a in adj if a.kind == "price_adjust" and a.new_price]
                prices.sort(key=lambda a: (a.effective_date or a.rcept_dt, a.rcept_no))
                price = prices[-1].new_price if prices else ev.price
                ends = [x for x in (ev.exercise_end, ev.maturity) if x is not None]
                active = face > 0 and (not ends or t <= min(ends))
                shares = math.floor(face / price) if active and price else 0
                if active and ev.exercise_start is not None and ev.exercise_start <= t:
                    exercisable += shares
            per[ev.rcept_no] = shares
            potential += shares
        den = next(
            sc.common_issued
            for sc in sorted(self.share_counts, key=lambda s: s.period_end, reverse=True)
            if sc.corp_code == corp and (sc.rcept_dt or sc.period_end) <= t
        )
        assert den is not None
        return {
            "corp_code": corp,
            "as_of": t.isoformat(),
            "per_event": per,
            "potential_new_shares": potential,
            "exercisable_shares": exercisable,
            "denominator": den,
            "ratio_pct": round(potential / den * 100, 4),
            "exercisable_ratio_pct": round(exercisable / den * 100, 4),
        }

    def expect_insider_windows(self, as_of: date | None = None) -> dict[int, dict[str, Any]]:
        """#15 의 30·90·180일 창(§4.4 — 정정은 늦은 접수번호만, 창 = 공시일 ∈ [t − days, t] —
        docs/metrics.md §13·설계 부록 A 와 같은 닫힌 구간)."""
        t = as_of or self.today
        corp = self.by_no(15).company_corp
        latest: dict[tuple[str, str], InsiderReport] = {}
        for rep in sorted(self.insider_reports, key=lambda x: x.rcept_no):
            if rep.corp_code != corp or rep.rcept_dt > t:
                continue
            base = self.insider_base.get(rep.rcept_no, rep.rcept_no)
            latest[(rep.reporter, base)] = rep
        den = next(
            sc.common_issued
            for sc in sorted(self.share_counts, key=lambda s: s.period_end, reverse=True)
            if sc.corp_code == corp and (sc.rcept_dt or sc.period_end) <= t
        )
        assert den is not None
        out: dict[int, dict[str, Any]] = {}
        for days in (30, 90, 180):
            inside = [r for r in latest.values() if t - timedelta(days=days) <= r.rcept_dt <= t]
            deltas = [r.shares_delta or 0 for r in inside]
            net = sum(deltas)
            out[days] = {
                "net_shares": net,
                "buy_shares": sum(x for x in deltas if x > 0),
                "sell_shares": -sum(x for x in deltas if x < 0),
                "n_reports": len(inside),
                "n_reporters": len({r.reporter for r in inside}),
                "ratio_pct": round(net / den * 100, 4),
            }
        return out

    # ── 결정성 ──

    def digest(self) -> str:
        """세계 전체의 sha256(같은 시드 → 같은 값). 시세·재무·공시·응답 행·컨센서스를 다 넣는다."""
        h = hashlib.sha256()

        def put(obj: object) -> None:
            h.update(json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode())

        put([s.record() for s in self.stocks])
        put([d.item() | {"posted_at": d.posted_at.isoformat()} for d in self.disclosures])
        for key in sorted(self.quarters):
            put([repr(q) for q in self.quarters[key]])
        for r in self._reports:
            for fs in self.corp(r.corp_code).fs_divs:
                put(self._statement_rows(r, fs, self.rcept(r.ident)))
        put({k: [list(v) for v in rows] for k, rows in sorted(self._structured.items())})
        put({k: list(v) for k, v in sorted(self._docs.items())})
        put({k: [list(r) for r in v] for k, v in sorted(self._prices.items())})
        put([repr(x) for x in self._investors])
        for seq in (self.consensus, self.opinions, self.prelims, self.overhang_events,
                    self.overhang_adjusts, self.insider_reports, self.major_reports,
                    self.share_counts, self.watchlist, self.buybacks):  # fmt: skip
            put([repr(x) for x in seq])
        return h.hexdigest()


_STRUCTURED: Final = (
    "cvbdIsDecsn",
    "bdwtIsDecsn",
    "exbdIsDecsn",
    "piicDecsn",
    "fricDecsn",
    "tsstkAqDecsn",
    "tsstkDpDecsn",
)

_BS_ACCOUNTS: Final[tuple[tuple[str, str, str], ...]] = (
    ("cash", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산"),
    ("short_inv", "dart_ShortTermDepositsNotClassifiedAsCashEquivalents", "단기금융상품"),
    ("total_assets", "ifrs-full_Assets", "자산총계"),
    ("short_borrow", "ifrs-full_ShorttermBorrowings", "단기차입금"),
    ("current_ltd", "ifrs-full_CurrentPortionOfLongtermBorrowings", "유동성장기부채"),
    ("bonds", "dart_BondsIssued", "사채"),
    ("long_borrow", "ifrs-full_LongtermBorrowings", "장기차입금"),
    ("total_liabilities", "ifrs-full_Liabilities", "부채총계"),
    ("capital", "ifrs-full_IssuedCapital", "자본금"),
    (
        "equity_owner",
        "ifrs-full_EquityAttributableToOwnersOfParent",
        "지배기업의 소유주에게 귀속되는 자본",
    ),
    ("minority", "ifrs-full_NoncontrollingInterests", "비지배지분"),
    ("total_equity", "ifrs-full_Equity", "자본총계"),
)
_IS_ACCOUNTS: Final[tuple[tuple[str, str, str], ...]] = (
    ("revenue", "ifrs-full_Revenue", "매출액"),
    ("cogs", "ifrs-full_CostOfSales", "매출원가"),
    ("gross_profit", "ifrs-full_GrossProfit", "매출총이익"),
    ("sgna", "dart_TotalSellingGeneralAdministrativeExpenses", "판매비와관리비"),
    ("op", "dart_OperatingIncomeLoss", "영업이익"),
    ("fin_income", "ifrs-full_FinanceIncome", "금융수익"),
    ("fin_cost", "ifrs-full_FinanceCosts", "금융원가"),
    ("other_income", "dart_OtherGains", "기타수익"),
    ("other_expense", "dart_OtherLosses", "기타비용"),
    ("pretax", "ifrs-full_ProfitLossBeforeTax", "법인세비용차감전순이익"),
    ("tax", "ifrs-full_IncomeTaxExpenseContinuingOperations", "법인세비용"),
    ("ni", "ifrs-full_ProfitLoss", "당기순이익"),
    (
        "ni_owner",
        "ifrs-full_ProfitLossAttributableToOwnersOfParent",
        "지배기업의 소유주에게 귀속되는 당기순이익",
    ),
    (
        "ni_minority",
        "ifrs-full_ProfitLossAttributableToNoncontrollingInterests",
        "비지배지분에 귀속되는 당기순이익",
    ),
    ("eps", "ifrs-full_BasicEarningsLossPerShare", "기본주당이익"),
)


def _is_value(q: QuarterTruth, key: str) -> int:
    if key == "ni_minority":
        return q.ni - q.ni_owner
    v = getattr(q, key)
    return 0 if v is None else int(v)


def _eps_fmt(currency: str) -> Any:
    if currency == "KRW":
        return lambda x: str(round(x))
    return lambda x: f"{x:.2f}"


def _multi_row(
    c: WorldCorp,
    r: _Report,
    rcept: str,
    fs: str,
    fs_nm: str,
    sj: tuple[str, str],
    nm: str,
    th_dt: str,
    amount: str,
    add: str | None,
    ordn: int,
) -> dict[str, str]:
    row = {
        "rcept_no": rcept, "bsns_year": str(r.year), "corp_code": c.corp_code,
        "stock_code": c.stock_code or "", "reprt_code": r.reprt_code, "account_nm": nm,
        "fs_div": fs, "fs_nm": fs_nm, "sj_div": sj[0], "sj_nm": sj[1],
        "thstrm_nm": f"제 {r.year - c.est_dt.year + 1} 기", "thstrm_dt": th_dt,
        "thstrm_amount": amount, "ord": str(ordn), "currency": c.currency,
    }  # fmt: skip
    if add is not None:
        row["thstrm_add_amount"] = add
    return row


def _share_row(
    c: WorldCorp, rcept: str, se: str, issued: int | None, treas: int | None, end: date
) -> dict[str, str]:
    def f(v: int | None) -> str:
        return "-" if not v else _comma(v)

    return {
        "rcept_no": rcept, "corp_cls": c.corp_cls, "corp_code": c.corp_code,
        "corp_name": c.corp_name, "se": se, "isu_stock_totqy": f((issued or 0) * 4 or None),
        "now_to_isu_stock_totqy": f(issued), "now_to_dcrs_stock_totqy": "-", "redc": "-",
        "profit_incnr": "-", "rdmstk_repy": "-", "etc": "-", "istc_totqy": f(issued),
        "tesstk_co": f(treas), "distb_stock_co": f((issued or 0) - (treas or 0) or None),
        "stlm_dt": end.isoformat(),
    }  # fmt: skip


def _ok(rows: list[dict[str, str]]) -> dict[str, Any]:
    return {"_source": SYNTHETIC, "status": "000", "message": STATUS_TEXT["000"], "list": rows}


def _no_data() -> dict[str, Any]:
    return {"_source": SYNTHETIC, "status": "013", "message": STATUS_TEXT["013"]}


def _sect(s: WorldStock) -> str:
    if s.market != "KOSDAQ":
        return ""
    return "SPAC(소속부없음)" if s.kind == "spac" else "중견기업부"


def _secugrp(s: WorldStock) -> str:
    if s.kind == "reit":
        return "부동산투자회사"
    if s.no == 19:
        return "외국주권"
    return "주권"


# ── 재무 참값 ───────────────────────────────────────────────────────────────────────────


def _alloc(
    total: int, parts: Sequence[tuple[str, float]], unit: int
) -> tuple[tuple[str, int], ...]:
    """합계를 비율로 나눈다(마지막 = 나머지 — 합이 정확히 total)."""
    out: list[tuple[str, int]] = []
    used = 0
    for i, (name, share) in enumerate(parts):
        v = total - used if i == len(parts) - 1 else _r(total * share, unit)
        used += v
        out.append((name, v))
    return tuple(out)


def _notes(q: dict[str, Any], unit: int) -> dict[str, tuple[tuple[str, int], ...]]:
    total = q["revenue"] - q["op"]
    cost: list[tuple[str, int]] = []
    if q["cogs"] is not None:
        cost += [("재고자산의 변동", _r(q["cogs"] * 0.08, unit)),
                 ("원재료 사용액", _r(q["cogs"] * 0.6, unit))]  # fmt: skip
    cost += [
        ("종업원급여", _r(total * 0.14, unit)),
        ("감가상각비", q["dep"]),
        ("무형자산상각비", q["amort"]),
        ("지급수수료", _r(total * 0.05, unit)),
        ("외주용역비", _r(total * 0.04, unit)),
        ("수선비", _r(total * 0.02, unit)),
        ("광고선전비", _r(total * 0.015, unit)),
        ("운반비", _r(total * 0.01, unit)),
    ]
    cost.append(("기타", total - sum(v for _, v in cost)))
    return {
        "cost": tuple(cost),
        "fin_income": _alloc(q["fin_income"], (
            ("이자수익", 0.4), ("외환차익", 0.2), ("외화환산이익", 0.1),
            ("당기손익-공정가치측정금융자산평가이익", 0.2), ("배당금수익", 0.0)), unit),
        "fin_cost": _alloc(q["fin_cost"], (
            ("이자비용", 0.6), ("외환차손", 0.25), ("외화환산손실", 0.0)), unit),
        "other_income": _alloc(q["other_income"], (("유형자산처분이익", 0.3), ("잡이익", 0.0)),
                               unit),
        "other_expense": _alloc(q["other_expense"], (
            ("기부금", 0.2), ("유형자산처분손실", 0.3), ("잡손실", 0.0)), unit),
    }  # fmt: skip


def _opm(fin: _Fin, i: int) -> float:
    m = fin.opm[0][1]
    for start, v in fin.opm:
        if i >= start:
            m = v
    return m


def _dart_shares(spec: _Spec, end: date) -> tuple[int, int, int]:
    """기간 말 (보통주 발행, 우선주 발행, 보통주 자기주식) — DART 주식총수(자기주식 포함)."""
    common = spec.shares
    if spec.no == 14:
        common += 250_000 if end >= date(2025, 6, 10) else 0
        common += 1_000_000 if end >= date(2026, 7, 15) else 0
    treasury = spec.treasury
    if spec.no == 1 and end >= date(2026, 9, 30):
        treasury += 600_000  # 자기주식 취득(2026-08-21 ~) 중 3분기 말까지 산 것
    return common, spec.pref_shares, treasury


def _krx_shares(spec: _Spec, day: date) -> int:
    """KRX 상장주식수(그날)."""
    if spec.kind == "pref":
        return spec.shares
    n = spec.shares
    if spec.no == 14:
        n += 250_000 if day >= date(2025, 6, 25) else 0
        n += 1_000_000 if day >= date(2026, 7, 15) else 0
    if spec.no == 17 and day >= SPLIT_DAY:
        n *= 5
    return n


SPLIT_DAY: Final = date(2026, 10, 26)


def _gen_quarters(spec: _Spec, seed: int) -> list[dict[str, Any]]:
    """회사의 분기 참값(연결 또는 별도 하나 — 회사 기본 기준) 사전 목록."""
    fin = spec.fin
    assert fin is not None and spec.corp is not None
    rng = _rng(seed, "fin", spec.corp)
    unit = _USD_UNIT if spec.currency == "USD" else _KRW_UNIT
    eq0 = fin.eq0 or fin.rev0 * 3.6
    owner = float(_r(eq0, unit))
    liab0 = eq0 * fin.lev
    out: list[dict[str, Any]] = []
    last_y, last_q = spec.fin_last
    prev_fy_ni = 0
    fy_ni = 0
    i = 0
    year = spec.fin_from
    while (year, 1) <= (last_y, last_q):
        for q in range(1, 5):
            if (year, q) > (last_y, last_q):
                break
            if q == 1:
                prev_fy_ni, fy_ni = fy_ni, 0
            per = fiscal_quarter(year, q, spec.acc_mt)
            liab = _r(liab0 * (1 + fin.gl) ** i, unit)
            if fin.rev0 == 0:  # 스팩
                rev = 0
                cogs: int | None = None
                sgna = _r(fin.sga_fixed * (1 + rng.uniform(-0.1, 0.1)), unit)
                op = -sgna
            else:
                base = fin.rev0 * (1 + fin.g) ** i * (1 + fin.season[q - 1])
                rev = _r(base * (1 + rng.uniform(-fin.noise, fin.noise)), unit)
                op = _r(rev * _opm(fin, i) * (1 + rng.uniform(-0.08, 0.08)), unit)
                if fin.gm is None:
                    cogs = None
                    sgna = rev - op
                else:
                    cogs = _r(rev * (1 - fin.gm * (1 + rng.uniform(-0.02, 0.02))), unit)
                    sgna = rev - cogs - op
            fi = _r(rev * fin.fi + fin.fi_abs * (1 + rng.uniform(-0.05, 0.05)), unit)
            fc = _r(liab * fin.borrow * fin.rate, unit)
            oi = _r(rev * fin.oi, unit)
            oe = _r(rev * fin.oe, unit)
            pretax = op + fi - fc + oi - oe
            tax = _r(max(pretax, 0) * fin.tax, unit)
            ni = pretax - tax
            ni_owner = ni - _r(ni * fin.minority, unit)
            fy_ni += ni_owner
            dividend = _r(max(prev_fy_ni, 0) * fin.payout, unit) if q == 1 and i > 0 else 0
            owner += ni_owner - dividend
            eo = int(owner)
            minority_eq = _r(max(eo, 0) * fin.minority / (1 - fin.minority), unit)
            equity = eo + minority_eq
            assets = liab + equity
            borrow = _r(liab * fin.borrow, unit)
            sb, lb, bd = _r(borrow * 0.4, unit), _r(borrow * 0.3, unit), _r(borrow * 0.2, unit)
            common, pref, treasury = _dart_shares(spec, per.end)
            eps_den = common - treasury + pref
            eps = ni_owner / eps_den
            row: dict[str, Any] = {
                "period": per, "revenue": rev, "cogs": cogs,
                "gross_profit": None if cogs is None else rev - cogs, "sgna": sgna, "op": op,
                "fin_income": fi, "fin_cost": fc, "other_income": oi, "other_expense": oe,
                "pretax": pretax, "tax": tax, "ni": ni, "ni_owner": ni_owner,
                "dep": _r(rev * fin.dep, unit), "amort": _r(rev * fin.amort, unit),
                "total_assets": assets, "total_liabilities": liab, "total_equity": equity,
                "equity_owner": eo, "minority": minority_eq,
                "cash": _r(max(assets, 0) * fin.cash, unit),
                "short_inv": _r(max(assets, 0) * fin.sinv, unit),
                "short_borrow": sb, "long_borrow": lb, "bonds": bd, "current_ltd": borrow - sb - lb - bd,  # noqa: E501
                "capital": (common + pref) * spec.par, "common_issued": common,
                "pref_issued": pref, "treasury": treasury,
                "eps": round(eps) if spec.currency == "KRW" else round(eps, 2),
            }  # fmt: skip
            row["notes"] = _notes(row, unit)
            out.append(row)
            i += 1
        year += 1
    return out


def _ofs(rows: list[dict[str, Any]], fin: _Fin, unit: int) -> list[dict[str, Any]]:
    """연결 참값 → 별도(비율 축소, 비지배 없음). 손익 항등식은 다시 맞춘다."""
    s = fin.ofs
    out: list[dict[str, Any]] = []
    for q in rows:
        rev = _r(q["revenue"] * s, unit)
        cogs = None if q["cogs"] is None else _r(q["cogs"] * s, unit)
        op = _r(q["op"] * s, unit)
        fi, fc = _r(q["fin_income"] * s, unit), _r(q["fin_cost"] * s, unit)
        oi, oe = _r(q["other_income"] * s, unit), _r(q["other_expense"] * s, unit)
        pretax = op + fi - fc + oi - oe
        tax = _r(max(pretax, 0) * fin.tax, unit)
        ni = pretax - tax
        liab = _r(q["total_liabilities"] * s, unit)
        eo = _r(q["equity_owner"] * s, unit)
        assets = liab + eo
        borrow = _r(liab * fin.borrow, unit)
        sb, lb, bd = _r(borrow * 0.4, unit), _r(borrow * 0.3, unit), _r(borrow * 0.2, unit)
        den = q["common_issued"] - q["treasury"] + q["pref_issued"]
        row = dict(q)
        row.update({
            "revenue": rev, "cogs": cogs, "gross_profit": None if cogs is None else rev - cogs,
            "sgna": rev - op - (cogs or 0), "op": op, "fin_income": fi, "fin_cost": fc,
            "other_income": oi, "other_expense": oe, "pretax": pretax, "tax": tax, "ni": ni,
            "ni_owner": ni, "dep": _r(q["dep"] * s, unit), "amort": _r(q["amort"] * s, unit),
            "total_assets": assets, "total_liabilities": liab, "total_equity": eo,
            "equity_owner": eo, "minority": 0, "cash": _r(max(assets, 0) * fin.cash, unit),
            "short_inv": _r(max(assets, 0) * fin.sinv, unit), "short_borrow": sb,
            "long_borrow": lb, "bonds": bd, "current_ltd": borrow - sb - lb - bd,
            "eps": round(ni / den) if unit == _KRW_UNIT else round(ni / den, 2),
        })  # fmt: skip
        row["notes"] = _notes(row, unit)
        out.append(row)
    return out


def _truth(
    corp: str, fs: Literal["CFS", "OFS"], currency: str, rows: list[dict[str, Any]]
) -> tuple[QuarterTruth, ...]:
    return tuple(QuarterTruth(corp_code=corp, fs_div=fs, currency=currency, **q) for q in rows)


# ── 공시·원문 ──────────────────────────────────────────────────────────────────────────

# (ident, 종목 번호, 날짜, 시각, 제목, 공시유형, 세계 분류)
_EVENTS: Final[tuple[tuple[str, int, date, str, str, str, str], ...]] = (
    ("e1.contract", 1, date(2025, 11, 20), "16:20", "단일판매ㆍ공급계약체결", "I", "contract"),
    (
        "e1.contract_fix",
        1,
        date(2025, 11, 27),
        "17:05",
        "[기재정정]단일판매ㆍ공급계약체결",
        "I",
        "contract",
    ),
    (
        "e1.prelim_25q4",
        1,
        date(2026, 1, 28),
        "16:10",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    ("e1.dividend", 1, date(2026, 3, 5), "15:30", "현금ㆍ현물배당결정", "I", "other"),
    (
        "e1.prelim_26q1",
        1,
        date(2026, 4, 7),
        "16:00",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    (
        "e1.prelim_26q2",
        1,
        date(2026, 7, 7),
        "16:00",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    (
        "e1.buyback",
        1,
        date(2026, 8, 20),
        "16:30",
        "주요사항보고서(자기주식취득결정)",
        "B",
        "buyback",
    ),
    (
        "e1.prelim_26q3",
        1,
        date(2026, 10, 8),
        "16:00",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    ("e1.preann", 1, TODAY, "09:12", "결산실적공시예고", "I", "pre_announce"),
    ("e1.outlook", 1, TODAY, "13:30", "실적등에대한전망(공정공시)", "I", "outlook"),
    ("e3.ipo", 3, date(2026, 10, 14), "17:00", "증권발행실적보고서", "C", "other"),
    ("e4.halt", 4, date(2026, 11, 6), "17:30", "주권매매거래정지", "I", "other"),
    ("e5.prospectus", 5, date(2025, 3, 5), "09:00", "투자설명서", "C", "other"),
    ("e6.dividend", 6, date(2026, 8, 25), "15:00", "현금ㆍ현물배당결정", "I", "other"),
    (
        "e8.prelim_26q3",
        8,
        date(2026, 10, 28),
        "16:30",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    ("e9.managed", 9, date(2026, 4, 1), "07:30", "관리종목지정(사업보고서 미제출)", "I", "other"),
    ("e9.notice", 9, TODAY, "15:00", "기타시장안내", "I", "other"),
    (
        "e11.prelim_26q3",
        11,
        date(2026, 10, 23),
        "15:40",
        "연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    (
        "e11.sub_prelim",
        11,
        TODAY,
        "15:20",
        "연결재무제표기준영업(잠정)실적(공정공시)(자회사의 주요경영사항)",
        "I",
        "prelim",
    ),
    ("e12.contract", 12, date(2026, 6, 15), "16:00", "단일판매ㆍ공급계약체결", "I", "contract"),
    ("e13.impair", 13, date(2026, 8, 14), "18:10", "자본잠식률50%이상사실발생", "I", "other"),
    (
        "e14.cb1",
        14,
        date(2024, 3, 15),
        "16:00",
        "주요사항보고서(전환사채권발행결정)",
        "B",
        "overhang",
    ),
    (
        "e14.cb2",
        14,
        date(2025, 1, 20),
        "16:00",
        "주요사항보고서(전환사채권발행결정)",
        "B",
        "overhang",
    ),
    ("e14.ex1", 14, date(2025, 6, 10), "17:00", "전환청구권행사", "I", "overhang_exercise"),
    (
        "e14.bw1",
        14,
        date(2025, 7, 1),
        "16:00",
        "주요사항보고서(신주인수권부사채권발행결정)",
        "B",
        "overhang",
    ),
    ("e14.adj1", 14, date(2026, 4, 20), "17:20", "전환가액의조정", "I", "price_adjust"),
    ("e14.paid", 14, date(2026, 6, 10), "16:00", "주요사항보고서(유상증자결정)", "B", "overhang"),
    (
        "e14.cb3",
        14,
        date(2026, 9, 1),
        "16:00",
        "주요사항보고서(전환사채권발행결정)",
        "B",
        "overhang",
    ),
    ("e14.adj2", 14, TODAY, "10:40", "전환가액의조정", "I", "price_adjust"),
    ("e14.bwex", 14, TODAY, "14:10", "신주인수권행사", "I", "overhang_exercise"),
    (
        "e15.ins_new",
        15,
        date(2026, 6, 2),
        "17:10",
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "D",
        "insider",
    ),
    (
        "e15.ins_ceo1",
        15,
        date(2026, 9, 1),
        "17:20",
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "D",
        "insider",
    ),
    (
        "e15.ins_cfo",
        15,
        date(2026, 10, 15),
        "16:50",
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "D",
        "insider",
    ),
    (
        "e15.maj1",
        15,
        date(2026, 10, 20),
        "15:30",
        "주식등의대량보유상황보고서(일반)",
        "D",
        "major_holder",
    ),
    (
        "e15.maj1_fix",
        15,
        date(2026, 10, 22),
        "16:00",
        "[기재정정]주식등의대량보유상황보고서(일반)",
        "D",
        "major_holder",
    ),
    (
        "e15.ins_major",
        15,
        date(2026, 11, 2),
        "17:40",
        "임원ㆍ주요주주특정증권등소유상황보고서",
        "D",
        "insider",
    ),
    (
        "e15.ins_major_fix",
        15,
        date(2026, 11, 5),
        "16:10",
        "[기재정정]임원ㆍ주요주주특정증권등소유상황보고서",
        "D",
        "insider",
    ),
    (
        "e15.maj2",
        15,
        date(2026, 11, 10),
        "15:50",
        "주식등의대량보유상황보고서(일반)",
        "D",
        "major_holder",
    ),
    ("e15.ins_ceo2", 15, TODAY, "11:20", "임원ㆍ주요주주특정증권등소유상황보고서", "D", "insider"),
    ("e15.prelim", 15, TODAY, "15:35", "연결재무제표기준영업(잠정)실적(공정공시)", "I", "prelim"),
    (
        "e15.prelim_fix",
        15,
        TODAY,
        "16:20",
        "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)",
        "I",
        "prelim",
    ),
    ("e16.prelim", 16, TODAY, "16:05", "영업(잠정)실적(공정공시)", "I", "prelim"),
    ("e17.split", 17, date(2026, 9, 15), "16:40", "주식분할결정", "I", "other"),
    ("e18.prelim", 18, TODAY, "14:45", "영업(잠정)실적(공정공시)", "I", "prelim"),
    ("e20.delist", 20, date(2026, 10, 30), "18:00", "상장폐지결정", "I", "other"),
    ("e20.liq", 20, date(2026, 11, 4), "17:00", "상장폐지에따른정리매매개시", "I", "other"),
    ("e20.notice", 20, TODAY, "08:30", "기타시장안내", "I", "other"),
)


def _statutory_deadline(end: date, q: int) -> date:
    """법정 제출기한(분기·반기 45일, 사업 90일 — 휴일이면 다음 영업일 [확인 필요])."""
    return _on_or_after(end + timedelta(days=90 if q == 4 else 45))


def _report_specs(spec: _Spec) -> list[tuple[int, int]]:
    """정기보고서가 있는 (연도, 분기)."""
    if spec.fin is None or spec.cadence == "none":
        return []
    out: list[tuple[int, int]] = []
    y = spec.fin_from
    while (y, 1) <= spec.fin_last:
        for q in range(1, 5):
            if (y, q) > spec.fin_last:
                break
            if spec.cadence == "semiannual" and q not in (2, 4):
                continue
            if spec.cadence == "new" and (y, q) != spec.fin_last:
                continue
            out.append((y, q))
        y += 1
    return out


def _fmt_num(v: int) -> str:
    return f"{v:,}" if v >= 0 else f"-{-v:,}"


def _pct(cur: int, base: int) -> tuple[str, str]:
    """(증감율 표기, 흑자적자전환 표기) — DART 폼 관행."""
    if base < 0 < cur:
        return "-", "흑자전환"
    if base > 0 > cur:
        return "-", "적자전환"
    if base == 0:
        return "-", "-"
    return f"{(cur - base) / abs(base) * 100:.1f}", "-"


def _html(title: str, body: str) -> str:
    return (
        f'<html><head><meta charset="utf-8"><title>{title}</title></head><body>'
        f"<!-- {SYNTHETIC} -->{body}</body></html>"
    )


def _kv_table(rows: Iterable[Sequence[str]]) -> str:
    trs = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table border=1>{trs}</table>"


def _generic_doc(d: WorldDisclosure) -> str:
    return _html(
        d.title,
        f"<p>{d.corp_name}</p><p>{d.title}</p><p>합성 공시 원문 — 본문 표 없음(세계 밖 내용)</p>",
    )


_PRELIM_LABELS: Final = (
    ("revenue", "매출액"),
    ("op", "영업이익"),
    ("pretax", "법인세비용차감전계속사업이익"),
    ("ni", "당기순이익"),
    ("ni_owner", "지배기업 소유주지분 순이익"),
)


def _prelim_doc(
    title: str,
    scope_text: str,
    unit_label: str,
    vals: Mapping[str, tuple[int, int, int | None, int, int | None]],
    *,
    period_text: str | None,
    two_col: bool,
) -> str:
    """잠정실적 폼(SD earnings_parser 정규식이 읽는 '라벨 당해실적 …' 순서).

    vals[지표] = (당기, 전기(직전 분기), 전년동기 | None, 누계 당기, 누계 전년동기 | None) — 표시
    단위.
    """
    head = ["구분", "", "당기", "전기"] + ([] if two_col else [
        "전기대비증감율(%)", "흑자적자전환여부", "전년동기", "전년동기대비증감율(%)",
        "흑자적자전환여부"])  # fmt: skip
    rows: list[list[str]] = [["1. 실적내용", f"(단위 : {unit_label}, %)"], head]
    for key, label in _PRELIM_LABELS:
        if key not in vals:
            continue
        cur, prev_q, prev_y, cum, cum_y = vals[key]
        if two_col:
            rows.append([label, "당해실적", _fmt_num(cur), _fmt_num(prev_q)])
            rows.append(["누계실적", _fmt_num(cum), "-"])
            continue
        assert prev_y is not None and cum_y is not None
        qoq, t1 = _pct(cur, prev_q)
        yoy, t2 = _pct(cur, prev_y)
        cyoy, t3 = _pct(cum, cum_y)
        rows.append([label, "당해실적", _fmt_num(cur), _fmt_num(prev_q), qoq, t1,
                     _fmt_num(prev_y), yoy, t2])  # fmt: skip
        rows.append(["누계실적", _fmt_num(cum), "-", "-", "-", _fmt_num(cum_y), cyoy, t3])
    extra = [["2. 실적기간", period_text]] if period_text else []
    extra.append(["3. 정보제공내역", "정보제공자: IR팀, 정보제공대상자: 국내외 투자자"])
    body = (
        f"<p>{title}</p><p>※ 동 정보는 {scope_text} 기준의 잠정 영업실적입니다.</p>"
        + _kv_table(rows)
        + _kv_table(extra)
    )
    return _html(title, body)


@dataclass
class _Build:
    """build_world 안에서만 쓰는 작업 상자."""

    seed: int
    specs: tuple[_Spec, ...]
    trading_days: tuple[date, ...]
    listed: dict[int, date] = field(default_factory=dict[int, date])
    pending: list[tuple[str, str, date, str, str, str, str]] = field(
        default_factory=list[tuple[str, str, date, str, str, str, str]]
    )  # (ident, corp, day, hh:mm, title, pblntf, kind)
    rcept: dict[str, str] = field(default_factory=dict[str, str])
    docs: dict[str, tuple[tuple[str, str], ...]] = field(
        default_factory=dict[str, tuple[tuple[str, str], ...]]
    )
    structured: dict[str, list[tuple[str, date, Mapping[str, str]]]] = field(
        default_factory=dict[str, list[tuple[str, date, Mapping[str, str]]]]
    )

    def spec(self, no: int) -> _Spec:
        return self.specs[no - 1]

    def add_structured(self, name: str, ident: str, day: date, row: Mapping[str, str]) -> None:
        self.structured.setdefault(name, []).append((self.rcept[ident], day, row))


def _assign_rcept(pending: list[tuple[str, str, date, str, str, str, str]]) -> dict[str, str]:
    """접수번호 = YYYYMMDD + 계열(DART 접수 '00' · 거래소 공시 '80') + 그날 순번 4자리(접수 순)."""
    out: dict[str, str] = {}
    seq: dict[tuple[date, str], int] = {}
    for ident, _corp, day, hhmm, _title, pblntf, _kind in sorted(
        pending, key=lambda p: (p[2], p[3], p[0])
    ):
        series = "80" if pblntf == "I" else "00"
        n = seq.get((day, series), 0) + 1
        seq[(day, series)] = n
        out[ident] = f"{_ymd(day)}{series}{n:04d}"
        del hhmm
    return out


# ── 세계 만들기 ────────────────────────────────────────────────────────────────────────


@cache
def build_world(seed: int = SEED) -> P4World:
    """합성 세계(캐시 — 같은 시드는 같은 객체). 시드가 다르면 시세·재무 잡음·잡음 공시가
    달라진다."""
    tdays = _tdays(PRICE_FROM, LAST_KRX_DAY)
    b = _Build(seed=seed, specs=_SPECS, trading_days=tdays)
    for sp in _SPECS:
        b.listed[sp.no] = sp.listed_on or tdays[-20]

    # 회사
    corps: list[WorldCorp] = []
    for sp in _SPECS:
        if sp.corp is None:
            continue
        corps.append(WorldCorp(
            sp.corp, sp.name, sp.eng, sp.code, sp.cls, sp.acc_mt, sp.induty, sp.est,
            sp.currency, sp.fs, sp.cadence, sp.induty[:2] in ("64", "65", "66"),
        ))  # fmt: skip
    for i, cc in enumerate(_NOISE_CORPS):
        corps.append(WorldCorp(cc, f"모의비상장{i}", f"MOCK PRIVATE {i}", None, "E", 12,
                               "70113", date(2010, 1, 1), "KRW", (), "none", False))  # fmt: skip
    corp_by = {c.corp_code: c for c in corps}

    # 재무 참값
    quarters: dict[tuple[str, str], tuple[QuarterTruth, ...]] = {}
    for sp in _SPECS:
        if sp.fin is None or sp.corp is None:
            continue
        rows = _gen_quarters(sp, seed)
        unit = _USD_UNIT if sp.currency == "USD" else _KRW_UNIT
        if sp.fs == ("OFS",):
            quarters[(sp.corp, "OFS")] = _truth(sp.corp, "OFS", sp.currency, rows)
        else:
            quarters[(sp.corp, "CFS")] = _truth(sp.corp, "CFS", sp.currency, rows)
            quarters[(sp.corp, "OFS")] = _truth(sp.corp, "OFS", sp.currency,
                                                _ofs(rows, sp.fin, unit))  # fmt: skip

    # 정기보고서 공시
    reports: list[_Report] = []
    for sp in _SPECS:
        if sp.corp is None:
            continue
        rng = _rng(seed, "filing", sp.corp)
        for y, q in _report_specs(sp):
            per = fiscal_quarter(y, q, sp.acc_mt)
            reprt = reprt_of_quarter(q)
            ident = f"r{sp.no}.{y}.{reprt}"
            deadline = _statutory_deadline(per.end, q)
            if (y, q) == sp.fin_last:
                day = sp.last_day
                hhmm = (
                    "17:10" if sp.no == 15 else f"{16 + rng.randrange(2)}:{rng.randrange(60):02d}"
                )
            else:
                day = _on_or_before(deadline - timedelta(days=rng.randrange(0, 6)))
                hhmm = f"{15 + rng.randrange(3)}:{rng.randrange(60):02d}"
            title = f"{_REPORT_NAME[reprt]} ({per.end:%Y.%m})"
            b.pending.append((ident, sp.corp, day, hhmm, title, "A", "periodic"))
            fix = None
            if sp.no == 1 and (y, reprt) == (2025, "11011"):
                fix = ident + ".fix"
                b.pending.append((fix, sp.corp, date(2026, 4, 10), "16:00", f"[기재정정]{title}",
                                  "A", "periodic"))  # fmt: skip
            reports.append(_Report(sp.corp, y, q, reprt, ident, fix))

    # 그 밖의 공시(세계 사건)
    for ident, no, day, hhmm, title, pblntf, kind in _EVENTS:
        sp = b.spec(no)
        assert sp.corp is not None
        b.pending.append((ident, sp.corp, day, hhmm, title, pblntf, kind))

    # 잡음(비상장 회사 — 쪽 경계)
    for day, n in FEED_NOISE.items():
        rng = _rng(seed, "noise", day)
        for k in range(n):
            corp = _NOISE_CORPS[rng.randrange(len(_NOISE_CORPS))]
            minute = 7 * 60 + rng.randrange(13 * 60)
            title = _NOISE_TITLES[rng.randrange(len(_NOISE_TITLES))]
            b.pending.append((f"n.{_ymd(day)}.{k:03d}", corp, day,
                              f"{minute // 60:02d}:{minute % 60:02d}", title,
                              "B" if "결정" in title else "I", "noise"))  # fmt: skip

    b.rcept = _assign_rcept(b.pending)
    disclosures: list[WorldDisclosure] = []
    for ident, corp, day, hhmm, title, pblntf, kind in b.pending:
        c = corp_by[corp]
        rm = "정" if title.startswith("[") else ("공" if "공정공시" in title else "")
        disclosures.append(WorldDisclosure(
            ident, b.rcept[ident], corp, c.corp_name, c.stock_code, c.corp_cls, title,
            "가상주주법인" if kind in ("insider", "major_holder") else c.corp_name, day,
            _kst(day, hhmm), pblntf, rm, kind,
        ))  # fmt: skip
    disclosures.sort(key=lambda d: (d.posted_at, d.rcept_no))

    # 주식총수(정기보고서마다)
    share_counts: list[ShareCount] = []
    for r in reports:
        sp = next(s for s in _SPECS if s.corp == r.corp_code)
        per = fiscal_quarter(r.year, r.q, sp.acc_mt)
        common, pref, treasury = _dart_shares(sp, per.end)
        d = next(x for x in disclosures if x.ident == r.ident)
        share_counts.append(ShareCount(
            r.corp_code, per.end, d.rcept_no, common, pref or None, treasury, None,
            rcept_dt=d.rcept_dt,
        ))  # fmt: skip

    # 원문·구조화·참값 사건
    prelims = _build_prelims(b, quarters, disclosures)
    events, adjusts = _build_overhang(b)
    insiders, majors, insider_base = _build_ownership(b)
    buybacks = _build_buyback(b)
    _build_contracts(b)
    _build_periodic_docs(b, reports, quarters)
    schedule = (
        ScheduleRow(
            "99400010", date(2026, 12, 31), "pre_announce", date(2027, 1, 28), 2026, 4,
            rcept_no=b.rcept["e1.preann"], note="2026년 연간 및 4분기 잠정실적",
        ),
    )  # fmt: skip
    preann = b.rcept["e1.preann"]
    preann_rows = [
        ["1. 공시예정일", "2027-01-28"],
        ["2. 공시예정시간", "16:00"],
        ["3. 공시대상", "2026년 연간 및 4분기 잠정실적"],
        ["4. 공시방법", "거래소 공정공시"],
    ]
    preann_html = _html("결산실적공시예고", "<p>결산실적공시 예고</p>" + _kv_table(preann_rows))
    b.docs[preann] = ((f"{preann}.xml", preann_html),)

    profiles = tuple(
        CorpProfile(
            c.corp_code,
            c.stock_code,
            c.acc_mt,
            c.induty_code,
            c.corp_cls,
            c.est_dt,
            corp_name=c.corp_name,
        )
        for c in corps
        if c.stock_code is not None
    )

    # 종목
    stocks: list[WorldStock] = []
    for sp in _SPECS:
        company = sp.corp or b.spec(sp.common_no).corp
        assert company is not None
        common_code = b.spec(sp.common_no).code if sp.kind == "pref" else sp.code
        stocks.append(WorldStock(
            sp.no, sp.code, sp.name, sp.market, sp.kind, common_code, sp.corp, company,
            b.listed[sp.no], sp.par, sp.watch, sp.consensus, sp.flags, sp.trap,
        ))  # fmt: skip

    prices = _build_prices(b, quarters)
    investors = _build_investors(b, prices)
    consensus = _build_consensus(b, quarters)
    opinions = _build_opinions(b, prices)
    share_classes = tuple(
        ShareClass(s.code, s.company_corp, "pref" if s.kind == "pref" else "common", s.common_code)
        for s in stocks
    )
    splits = (
        SplitEvent("994160", SPLIT_DAY, 5.0, "manual", "액면분할 5:1(5,000원 → 1,000원) — 합성",
                   "krx", Quality.OK),
    )  # fmt: skip
    watch_rng = _rng(seed, "watch")
    watchlist: list[WatchItem] = []
    for i, s in enumerate(x for x in stocks if x.watch):
        added = datetime(2025, 3, 3, 9, 0, tzinfo=KST) + timedelta(
            days=17 * i + watch_rng.randrange(5)
        )
        added = max(added, _kst(s.listed_on, "18:00"))
        watchlist.append(WatchItem("main", s.code, s.name, added, i, None, "import:sd"))

    return P4World(
        seed=seed,
        clock=CLOCK,
        today=TODAY,
        last_krx_day=LAST_KRX_DAY,
        trading_days=tdays,
        stocks=tuple(stocks),
        corps=tuple(corps),
        disclosures=tuple(disclosures),
        quarters=quarters,
        profiles=profiles,
        prelims=tuple(prelims),
        schedule=schedule,
        overhang_events=tuple(events),
        overhang_adjusts=tuple(adjusts),
        insider_reports=tuple(insiders),
        major_reports=tuple(majors),
        buybacks=tuple(buybacks),
        share_counts=tuple(share_counts),
        share_classes=share_classes,
        splits=splits,
        watchlist=tuple(watchlist),
        consensus=tuple(consensus),
        opinions=tuple(opinions),
        insider_base=insider_base,
        _reports=tuple(reports),
        _structured={k: tuple(v) for k, v in b.structured.items()},
        _docs=dict(b.docs),
        _prices=prices,
        _investors=tuple(investors),
    )


# ── 잠정실적 ───────────────────────────────────────────────────────────────────────────

# (ident, 기간(연·분기), 범위, 단위 이름, 배수, 원천 기준, 기간 표기, 2열 폼, 정정 보정(매출 +표시
# 단위))
_PRELIM_SPECS: Final = (
    (
        "e1.prelim_25q4",
        (2025, 4),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        0,
    ),
    (
        "e1.prelim_26q1",
        (2026, 1),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        0,
    ),
    (
        "e1.prelim_26q2",
        (2026, 2),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        0,
    ),
    (
        "e1.prelim_26q3",
        (2026, 3),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        0,
    ),
    (
        "e8.prelim_26q3",
        (2026, 3),
        PrelimScope.CONSOLIDATED,
        "백만원",
        1_000_000,
        "CFS",
        False,
        False,
        0,
    ),
    (
        "e11.prelim_26q3",
        (2026, 3),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        0,
    ),
    (
        "e11.sub_prelim",
        (2026, 3),
        PrelimScope.SUBSIDIARY,
        "억원",
        100_000_000,
        "CFS",
        False,
        False,
        0,
    ),
    ("e15.prelim", (2026, 3), PrelimScope.CONSOLIDATED, "억원", 100_000_000, "CFS", True, False, 0),
    (
        "e15.prelim_fix",
        (2026, 3),
        PrelimScope.CONSOLIDATED,
        "억원",
        100_000_000,
        "CFS",
        True,
        False,
        1,
    ),
    ("e16.prelim", (2026, 3), PrelimScope.STANDALONE, "천원", 1_000, "OFS", False, False, 0),
    ("e18.prelim", (2026, 3), PrelimScope.CONSOLIDATED, "백만원", 1_000_000, "CFS", False, True, 0),
)


def _build_prelims(
    b: _Build,
    quarters: Mapping[tuple[str, str], tuple[QuarterTruth, ...]],
    disclosures: Sequence[WorldDisclosure],
) -> list[PrelimRow]:
    out: list[PrelimRow] = []
    by_ident = {d.ident: d for d in disclosures}
    for ident, (y, q), scope, unit_label, mult, fs, has_period, two_col, bump in _PRELIM_SPECS:
        d = by_ident[ident]
        qs = {x.key: x for x in quarters[(d.corp_code, fs)]}
        cur = qs[(y, q)]
        prev_q = qs[(y, q - 1)] if q > 1 else qs[(y - 1, 4)]
        prev_y = qs[(y - 1, q)]
        factor = 0.3 if scope is PrelimScope.SUBSIDIARY else 1.0

        def disp(v: int, factor: float = factor, mult: int = mult) -> int:
            return round(v * factor / mult)

        vals: dict[str, tuple[int, int, int | None, int, int | None]] = {}
        for key, _label in _PRELIM_LABELS:
            if key == "ni_owner" and fs == "OFS":
                continue
            cum = sum(getattr(qs[(y, k)], key) for k in range(1, q + 1))
            cum_y = sum(getattr(qs[(y - 1, k)], key) for k in range(1, q + 1))
            c_v = disp(getattr(cur, key)) + (bump if key == "revenue" else 0)
            vals[key] = (
                c_v, disp(getattr(prev_q, key)),
                None if two_col else disp(getattr(prev_y, key)),
                disp(cum) + (bump if key == "revenue" else 0),
                None if two_col else disp(cum_y),
            )  # fmt: skip
        per = cur.period
        period_text = f"{per.start:%Y.%m.%d} ~ {per.end:%Y.%m.%d}" if has_period else None
        scope_text = "별도재무제표" if scope is PrelimScope.STANDALONE else "연결재무제표"
        b.docs[d.rcept_no] = ((f"{d.rcept_no}.xml", _prelim_doc(
            d.title, scope_text, unit_label, vals, period_text=period_text, two_col=two_col,
        )),)  # fmt: skip
        yoy: dict[str, float | None] = {}
        if not two_col:
            for key, v in vals.items():
                pct, _turn = _pct(v[0], v[2] or 0)
                yoy[key] = None if pct == "-" else float(pct)
        out.append(PrelimRow(
            rcept_no=d.rcept_no, corp_code=d.corp_code, rcept_dt=d.rcept_dt, scope=scope,
            fiscal_year=y, fiscal_q=q, is_annual=False, period_end=per.end,
            period_inferred=not has_period, unit_mult=mult,
            revenue_cur=vals["revenue"][0] * mult, revenue_prev_q=vals["revenue"][1] * mult,
            revenue_prev_y=_mul(vals["revenue"][2], mult),
            op_cur=vals["op"][0] * mult, op_prev_q=vals["op"][1] * mult,
            op_prev_y=_mul(vals["op"][2], mult),
            pretax_cur=vals["pretax"][0] * mult, pretax_prev_q=vals["pretax"][1] * mult,
            pretax_prev_y=_mul(vals["pretax"][2], mult),
            ni_cur=vals["ni"][0] * mult, ni_prev_q=vals["ni"][1] * mult,
            ni_prev_y=_mul(vals["ni"][2], mult),
            ni_owner_cur=_mul(vals["ni_owner"][0], mult) if "ni_owner" in vals else None,
            ni_owner_prev_q=_mul(vals["ni_owner"][1], mult) if "ni_owner" in vals else None,
            ni_owner_prev_y=_mul(vals["ni_owner"][2], mult) if "ni_owner" in vals else None,
            yoy_reported=yoy,
            quality=Quality.ESTIMATED if not has_period else Quality.OK,
        ))  # fmt: skip
    return out


def _mul(v: int | None, mult: int) -> int | None:
    return None if v is None else v * mult


# ── 오버행(#14) ────────────────────────────────────────────────────────────────────────

# (ident, 상품, 회차, 권면(원), 가격(원), 리픽싱 하한, 청구 시작, 청구 끝, 만기)
_BONDS: Final = (
    ("e14.cb1", Instrument.CB, "1", 10_000_000_000, 12_000.0, 8_400.0, date(2025, 3, 15),
     date(2027, 2, 15), date(2027, 3, 15)),
    ("e14.cb2", Instrument.CB, "2", 15_000_000_000, 10_000.0, 7_000.0, date(2026, 1, 20),
     date(2027, 12, 20), date(2028, 1, 20)),
    ("e14.bw1", Instrument.BW, "1", 8_000_000_000, 11_000.0, 7_700.0, date(2026, 7, 1),
     date(2028, 6, 1), date(2028, 7, 1)),
    ("e14.cb3", Instrument.CB, "3", 5_000_000_000, 9_000.0, 6_300.0, date(2027, 9, 1),
     date(2029, 8, 1), date(2029, 9, 1)),
)  # fmt: skip


def _build_overhang(b: _Build) -> tuple[list[OverhangEvent], list[OverhangAdjust]]:
    sp = b.spec(14)
    corp = sp.corp
    assert corp is not None
    events: list[OverhangEvent] = []
    total_shares = 40_250_000
    for ident, inst, seq, face, price, floor, ex_s, ex_e, mat in _BONDS:
        rcept = b.rcept[ident]
        day = next(e[2] for e in _EVENTS if e[0] == ident)
        shares = math.floor(face / price)
        events.append(OverhangEvent(
            rcept, corp, day, inst, face, price, shares, floor, ex_s, ex_e, mat, None, "사모",
            "api", seq=seq,
        ))  # fmt: skip
        ratio = f"{shares / total_shares * 100:.2f}"
        common = {
            "rcept_no": rcept, "corp_cls": "K", "corp_code": corp, "corp_name": sp.name,
            "bd_tm": seq, "bd_fta": _comma(face), "bd_intr_ex": "0.0", "bd_intr_sf": "2.0",
            "bd_mtd": _kor_date(mat), "bdis_mthn": "사모",
            "sbd": _kor_date(day + timedelta(days=5)),
            "pymd": _kor_date(day + timedelta(days=7)), "bddd": _kor_date(day),
            "act_mktprcfl_cvprc_lwtrsprc": _comma(int(floor)),
        }  # fmt: skip
        if inst is Instrument.CB:
            row = common | {
                "bd_knd": "무기명식 이권부 무보증 사모 전환사채", "cv_rt": "100",
                "cv_prc": _comma(int(price)), "cvisstk_knd": "기명식 보통주",
                "cvisstk_cnt": _comma(shares), "cvisstk_tisstk_vs": ratio,
                "cvrqpd_bgd": _kor_date(ex_s), "cvrqpd_edd": _kor_date(ex_e),
            }  # fmt: skip
            b.add_structured("cvbdIsDecsn", ident, day, row)
            words = ("전환가액", "전환에 따라 발행할 주식수", "전환청구기간")
            kind = "전환사채권"
        else:
            row = common | {
                "bd_knd": "무기명식 이권부 무보증 분리형 신주인수권부사채", "ex_rt": "100",
                "ex_prc": _comma(int(price)), "ex_prc_dmth": "시가 기준", "bdwt_div_atn": "분리",
                "nstk_isstk_knd": "기명식 보통주", "nstk_isstk_cnt": _comma(shares),
                "nstk_isstk_tisstk_vs": ratio, "expd_bgd": _kor_date(ex_s),
                "expd_edd": _kor_date(ex_e),
            }  # fmt: skip
            b.add_structured("bdwtIsDecsn", ident, day, row)
            words = ("행사가액", "행사에 따라 발행할 주식수", "행사청구기간")
            kind = "신주인수권부사채권"
        b.docs[rcept] = ((f"{rcept}.xml", _html(f"주요사항보고서({kind}발행결정)", "".join(
            f"<p>{t}</p>" for t in (
                f"1. 사채의 종류 : 회차 {seq}, {row['bd_knd']}",
                f"2. 사채의 액면총액 : {_comma(face)}원",
                f"{words[0]} : {_comma(int(price))}원",
                f"{words[1]} : {_comma(shares)}주",
                f"{words[2]} : {ex_s.isoformat()} ~ {ex_e.isoformat()}",
                f"시가하락에 따른 조정 최저 조정가액 : {_comma(int(floor))}원",
                f"사채만기일 : {mat.isoformat()}",
                "사채발행방법 : 사모",
            )))),)  # fmt: skip

    # 제3자배정 유상증자 — 결정일 ≤ t < 상장일 동안만 잠재(§4.3), 보호예수 추정(D-P4-18)
    paid = b.rcept["e14.paid"]
    paid_day = date(2026, 6, 10)
    listing = date(2026, 7, 15)
    # face_value 는 권면총액(CB·BW·EB)만 — 유상증자는 None(fin_rows.OverhangEvent·0010 계약). 조달
    # 금액(fdpp_op 8e9)은 신주 수 × 발행가로 따로 있다
    events.append(OverhangEvent(
        paid, corp, paid_day, Instrument.PAID_IN, None, 8_000.0, 1_000_000, None,
        None, None, None, listing, "제3자배정증자", "api",
    ))  # fmt: skip
    b.add_structured("piicDecsn", "e14.paid", paid_day, {
        "rcept_no": paid, "corp_cls": "K", "corp_code": corp, "corp_name": sp.name,
        "nstk_ostk_cnt": "1,000,000", "nstk_estk_cnt": "-", "fv_ps": "500",
        "bfic_tisstk_ostk": "40,250,000", "bfic_tisstk_estk": "-", "fdpp_fclt": "-",
        "fdpp_bsninh": "-", "fdpp_op": "8,000,000,000", "fdpp_dtrp": "-", "fdpp_ocsa": "-",
        "fdpp_etc": "-", "ic_mthn": "제3자배정증자", "ssl_at": "미해당", "ssl_bgd": "-",
        "ssl_edd": "-",
    })  # fmt: skip
    b.docs[paid] = ((f"{paid}.xml", _html("주요사항보고서(유상증자결정)", "".join(
        f"<p>{t}</p>" for t in (
            "1. 신주의 종류와 수 : 보통주식", "신주의 수 : 1,000,000주",
            "1주당 발행가액 : 8,000원", "증자방식 : 제3자배정증자",
            "배정방법 : 제3자배정(가상투자조합)", f"신주 상장 예정일 : {listing.isoformat()}",
        )))),)  # fmt: skip

    # 조정·청구(원문만 — 구조화 API 없음 [확인 필요 #31])
    cb1, cb2, bw1 = b.rcept["e14.cb1"], b.rcept["e14.cb2"], b.rcept["e14.bw1"]
    adjusts = [
        OverhangAdjust(b.rcept["e14.ex1"], corp, date(2025, 6, 10), cb1, "exercise", None,
                       3_000_000_000, 250_000, date(2025, 6, 10)),
        OverhangAdjust(b.rcept["e14.adj1"], corp, date(2026, 4, 20), cb2, "price_adjust",
                       8_500.0, None, None, date(2026, 4, 20)),
        OverhangAdjust(b.rcept["e14.adj2"], corp, TODAY, cb2, "price_adjust", 7_200.0, None,
                       None, TODAY),
        OverhangAdjust(b.rcept["e14.bwex"], corp, TODAY, bw1, "exercise", None,
                       2_200_000_000, 200_000, TODAY),
    ]  # fmt: skip
    b.docs[b.rcept["e14.ex1"]] = _exercise_doc(b.rcept["e14.ex1"], "전환청구권행사", "1",
                                               "2025-06-10", 3_000_000_000, 12_000, 250_000,
                                               "2025-06-25")  # fmt: skip
    b.docs[b.rcept["e14.bwex"]] = _exercise_doc(b.rcept["e14.bwex"], "신주인수권행사", "1",
                                                TODAY.isoformat(), 2_200_000_000, 11_000,
                                                200_000, "2026-11-27")  # fmt: skip
    for ident, before, after, day in (("e14.adj1", 10_000, 8_500, date(2026, 4, 20)),
                                      ("e14.adj2", 8_500, 7_200, TODAY)):  # fmt: skip
        rc = b.rcept[ident]
        b.docs[rc] = ((f"{rc}.xml", _html("전환가액의조정", _kv_table([
            ["1. 조정에 관한 사항", "", ""],
            ["회차", "조정전 전환가액(원)", "조정후 전환가액(원)"],
            ["2", _comma(before), _comma(after)],
            ["2. 전환가능주식수", "조정전 " + _comma(15_000_000_000 // before),
             "조정후 " + _comma(15_000_000_000 // after)],
            ["3. 조정가액 적용일", day.isoformat(), ""],
            ["4. 조정사유", "시가하락에 따른 전환가액 조정(리픽싱)", ""],
        ]))),)  # fmt: skip
    return events, adjusts


def _exercise_doc(
    rcept: str, title: str, seq: str, day: str, amount: int, price: int, shares: int, listing: str
) -> tuple[tuple[str, str], ...]:
    verb = "청구" if "전환" in title else "행사"
    return ((f"{rcept}.xml", _html(title, _kv_table([
        [f"1. {title} 주식수 누계(주)", _comma(shares)],
        [f"2. {title} 내역", ""],
        [f"{verb}일자", "회차", f"{verb}금액(원)", "가액(원)", "발행주식수(주)", "상장(예정)일"],
        [day, seq, _comma(amount), _comma(price), _comma(shares), listing],
    ]))),)  # fmt: skip


# ── 소유보고(#15) ──────────────────────────────────────────────────────────────────────

# (ident, 보고자, 직위, 등기, 주요주주, 변동 후, 증감, 사유, 정정 대상 ident)
_INSIDERS: Final = (
    ("e15.ins_new", "가상신임이사", "사내이사", True, False, 5_000, 5_000, "신규선임", None),
    ("e15.ins_ceo1", "가상대표", "대표이사", True, False, 1_700_000, -300_000, "장내매도(-)", None),
    ("e15.ins_cfo", "가상재무이사", "전무", False, False, 50_000, 20_000, "장내매수(+)", None),
    ("e15.ins_major", "가상주주법인", "-", None, True, 4_800_000, -1_200_000, "장내매도(-)", None),
    ("e15.ins_major_fix", "가상주주법인", "-", None, True, 4_850_000, -1_150_000, "장내매도(-)",
     "e15.ins_major"),
    ("e15.ins_ceo2", "가상대표", "대표이사", True, False, 1_200_000, -500_000, "장내매도(-)", None),
)  # fmt: skip
# (ident, 보고구분, 보유 주식, 증감, 비율, 비율 증감, 사유)
_MAJORS: Final = (
    ("e15.maj1", "일반", 2_560_000, 2_560_000, 5.12, 5.12, "신규 5% 보고(경영참가 목적 아님)"),
    ("e15.maj1_fix", "일반", 2_605_000, 2_605_000, 5.21, 5.21, "신규 5% 보고(정정 — 보유 수량)"),
    ("e15.maj2", "일반", 3_150_000, 545_000, 6.30, 1.09, "보유비율 1% 이상 변동"),
)
_INSIDER_DEN: Final = 50_000_000


def _build_ownership(
    b: _Build,
) -> tuple[list[InsiderReport], list[MajorHolderReport], dict[str, str]]:
    sp = b.spec(15)
    corp = sp.corp
    assert corp is not None
    base_of: dict[str, str] = {}
    insiders: list[InsiderReport] = []
    for ident, who, pos, reg, major, after, delta, reason, base in _INSIDERS:
        rcept = b.rcept[ident]
        day = next(e[2] for e in _EVENTS if e[0] == ident)
        if base is not None:
            base_of[rcept] = b.rcept[base]
        ratio_after = round(after / _INSIDER_DEN * 100, 2)
        ratio_delta = round(delta / _INSIDER_DEN * 100, 2)
        insiders.append(InsiderReport(
            rcept, corp, day, who, pos if pos != "-" else None, reg, major, after, delta,
            ratio_after, ratio_delta, reason,
        ))  # fmt: skip
        b.add_structured("elestock", ident, day, {
            "rcept_no": rcept, "rcept_dt": _ymd(day), "corp_code": corp, "corp_name": sp.name,
            "repror": who,
            "isu_exctv_rgist_at": "-" if reg is None else ("등기임원" if reg else "비등기임원"),
            "isu_exctv_ofcps": pos, "isu_main_shrholdr": "주요주주" if major else "-",
            "sp_stock_lmp_cnt": _comma(after), "sp_stock_lmp_irds_cnt": _fmt_num(delta),
            "sp_stock_lmp_rate": f"{ratio_after:.2f}", "sp_stock_lmp_irds_rate": f"{ratio_delta:.2f}",  # noqa: E501
        })  # fmt: skip
        title = "임원ㆍ주요주주특정증권등소유상황보고서"
        b.docs[rcept] = ((f"{rcept}.xml", _html(title, _kv_table([
            ["보고자", who], ["직위", pos], ["변동일", (day - timedelta(days=3)).isoformat()],
            ["변동사유", reason], ["변동 전 주식수", _comma(after - delta)],
            ["증감", _fmt_num(delta)], ["변동 후 주식수", _comma(after)],
        ]))),)  # fmt: skip
    majors: list[MajorHolderReport] = []
    for ident, tp, shares, delta, ratio, rdelta, reason in _MAJORS:
        rcept = b.rcept[ident]
        day = next(e[2] for e in _EVENTS if e[0] == ident)
        majors.append(MajorHolderReport(rcept, corp, day, "가상자산운용", tp, shares, delta,
                                        ratio, rdelta, reason))  # fmt: skip
        b.add_structured("majorstock", ident, day, {
            "rcept_no": rcept, "rcept_dt": _ymd(day), "corp_code": corp, "corp_name": sp.name,
            "report_tp": tp, "repror": "가상자산운용", "stkqy": _comma(shares),
            "stkqy_irds": _fmt_num(delta), "stkrt": f"{ratio:.2f}", "stkrt_irds": f"{rdelta:.2f}",
            "ctr_stkqy": "-", "ctr_stkrt": "-", "report_resn": reason,
        })  # fmt: skip
        b.docs[rcept] = ((f"{rcept}.xml", _html("주식등의대량보유상황보고서", _kv_table([
            ["보고구분", tp], ["대량보유자", "가상자산운용"], ["보유주식등의 수", _comma(shares)],
            ["보유비율(%)", f"{ratio:.2f}"], ["보고사유", reason],
        ]))),)  # fmt: skip
    return insiders, majors, base_of


# ── 자기주식(#1) ───────────────────────────────────────────────────────────────────────


def _build_buyback(b: _Build) -> list[BuybackEvent]:
    sp = b.spec(1)
    assert sp.corp is not None
    rcept = b.rcept["e1.buyback"]
    day = date(2026, 8, 20)
    start, end = date(2026, 8, 21), date(2026, 11, 20)
    b.add_structured("tsstkAqDecsn", "e1.buyback", day, {
        "rcept_no": rcept, "corp_cls": "Y", "corp_code": sp.corp, "corp_name": sp.name,
        "aqpln_stk_ostk": "1,000,000", "aqpln_stk_estk": "-",
        "aqpln_prc_ostk": "52,000,000,000", "aqpln_prc_estk": "-",
        "aqexpd_bgd": _kor_date(start), "aqexpd_edd": _kor_date(end), "hdexpd_bgd": "-",
        "hdexpd_edd": "-", "aq_pp": "주주가치 제고", "aq_mth": "유가증권시장을 통한 장내 직접 취득",
        "cs_iv_bk": "가상증권", "aq_wtn_div_ostk": "5,000,000", "aq_wtn_div_ostk_rt": "1.25",
        "aq_dd": _kor_date(day),
    })  # fmt: skip
    return [BuybackEvent(rcept, sp.corp, day, "acquire", 1_000_000, 52_000_000_000, start, end,
                         "장내 직접 취득")]  # fmt: skip


# ── 공급계약(G2 수주 — #1·#12) ─────────────────────────────────────────────────────────


def _build_contracts(b: _Build) -> None:
    for ident, amount, who, what, start, end, sales in (
        ("e1.contract", 12_345_000_000, "모의고객사A", "모의 반도체 장비 공급", "2025-11-20",
         "2026-06-30", 16_800_000_000_000),
        ("e1.contract_fix", 13_000_000_000, "모의고객사A", "모의 반도체 장비 공급(정정)",
         "2025-11-20", "2026-07-31", 16_800_000_000_000),
        ("e12.contract", 4_200_000_000, "모의발주처(NIPA 과제)", "모의 산업용 로봇 셀 공급",
         "2026-06-15", "2027-03-31", 150_000_000_000),
    ):  # fmt: skip
        rcept = b.rcept[ident]
        rows = [
            ["1. 판매ㆍ공급계약 내용", what],
            ["2. 계약내역", ""],
            ["계약금액(원)", _comma(amount)],
            ["최근매출액(원)", _comma(sales)],
            ["매출액대비(%)", f"{amount / sales * 100:.2f}"],
            ["3. 계약상대", who],
            ["4. 판매ㆍ공급지역", "대한민국"],
            ["5. 계약기간", f"{start} ~ {end}"],
            ["시작일", start],
            ["종료일", end],
        ]
        if ident.endswith("_fix"):
            rows.append(["정정사항", "계약금액·종료일 정정"])
        b.docs[rcept] = ((f"{rcept}.xml", _html("단일판매ㆍ공급계약체결", _kv_table(rows))),)


# ── 정기보고서 원문(G2 — #1·#12) ───────────────────────────────────────────────────────


def _note_table(
    title: str,
    unit_label: str,
    scale: int,
    items_by_q: Sequence[tuple[tuple[str, int], ...]],
    *,
    annual: bool,
) -> str:
    """주석 표 — 분기·반기 보고서는 '3개월 | 누적' 두 열(DR 은 누적 열을 읽는다)."""
    names = [n for n, _ in items_by_q[-1]]
    three = dict(items_by_q[-1])
    cum: dict[str, int] = {}
    for items in items_by_q:
        for n, v in items:
            cum[n] = cum.get(n, 0) + v
    if annual:
        head = ["구분", "당기", "전기"]
        rows = [[n, _fmt_num(round(cum[n] / scale)), "-"] for n in names]
        rows.append(["합계", _fmt_num(round(sum(cum.values()) / scale)), "-"])
    else:
        head = ["구분", "당분기 3개월", "당분기 누적", "전분기 3개월", "전분기 누적"]
        rows = [
            [n, _fmt_num(round(three[n] / scale)), _fmt_num(round(cum[n] / scale)), "-", "-"]
            for n in names
        ]
        rows.append(["합계", _fmt_num(round(sum(three.values()) / scale)),
                     _fmt_num(round(sum(cum.values()) / scale)), "-", "-"])  # fmt: skip
    trs = "<tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr>"
    trs += "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<p>{title}</p><p>(단위: {unit_label})</p><table border=1>{trs}</table>"


# 표와 표 사이 — DR `_table_context`(앞 글 조각 30개·1500자)가 앞 표의 제목을 못 보게
_SPACER: Final = "<p>·</p>" * 32


def _periodic_html(
    corp_name: str,
    title: str,
    span: Sequence[QuarterTruth],
    *,
    annual: bool,
    unit_label: str,
    scale: int,
    segments: bool,
    part: str,
) -> str:
    body = f"<p>{corp_name}</p><p>{title}</p><p>{part}</p>"
    if segments:
        cum_rev = sum(q.revenue for q in span)
        segs = (("모의부문A(전자부품)", 0.6), ("모의부문B(장비)", 0.3), ("기타", 0.1))
        rows = [["부문", "당기", "전기"]] + [
            [n, _comma(round(cum_rev * s / scale)), _comma(round(cum_rev * s * 0.93 / scale))]
            for n, s in segs
        ]
        body += f"<p>사업부문별 매출실적 (단위: {unit_label})</p>" + _kv_table(rows) + _SPACER
    for key, ttl in (("cost", "비용의 성격별 분류"), ("fin_income", "금융수익"),
                     ("fin_cost", "금융원가"), ("other_income", "기타수익"),
                     ("other_expense", "기타비용")):  # fmt: skip
        body += _note_table(ttl, unit_label, scale, [q.notes[key] for q in span], annual=annual)
        body += _SPACER
    return _html(title, body)


def _build_periodic_docs(
    b: _Build,
    reports: Sequence[_Report],
    quarters: Mapping[tuple[str, str], tuple[QuarterTruth, ...]],
) -> None:
    for r in reports:
        if r.corp_code not in ("99400010", "99400110"):
            continue
        sp = next(s for s in _SPECS if s.corp == r.corp_code)
        annual = r.reprt_code == "11011"
        per = fiscal_quarter(r.year, r.q, sp.acc_mt)
        title = f"{_REPORT_NAME[r.reprt_code]} ({per.end:%Y.%m})"
        files: list[tuple[str, str]] = []
        for ident in [r.ident] + ([r.fix] if r.fix else []):
            rcept = b.rcept[ident]
            if r.corp_code == "99400010":
                qs = {q.key: q for q in quarters[(r.corp_code, "CFS")]}
                qo = {q.key: q for q in quarters[(r.corp_code, "OFS")]}
                span = [qs[(r.year, k)] for k in range(1, r.q + 1)]
                span_o = [qo[(r.year, k)] for k in range(1, r.q + 1)]
                files = [
                    (f"{rcept}.xml", _periodic_html(sp.name, title, span, annual=annual,
                                                    unit_label="백만원", scale=1_000_000,
                                                    segments=True, part="본문")),
                    (f"{rcept}_00760.xml", _periodic_html(sp.name, title, span, annual=annual,
                                                          unit_label="백만원", scale=1_000_000,
                                                          segments=False, part="연결재무제표 주석")),  # noqa: E501
                    (f"{rcept}_00761.xml", _periodic_html(sp.name, title, span_o, annual=annual,
                                                          unit_label="백만원", scale=1_000_000,
                                                          segments=False, part="재무제표 주석(별도)")),  # noqa: E501
                ]  # fmt: skip
            else:
                qo = {q.key: q for q in quarters[(r.corp_code, "OFS")]}
                span_o = [qo[(r.year, k)] for k in range(1, r.q + 1)]
                files = [
                    (f"{rcept}.xml", _periodic_html(sp.name, title, span_o, annual=annual,
                                                    unit_label="천원", scale=1_000,
                                                    segments=True, part="본문")),
                    (f"{rcept}_00761.xml", _periodic_html(sp.name, title, span_o, annual=annual,
                                                          unit_label="천원", scale=1_000,
                                                          segments=False, part="재무제표 주석")),
                ]  # fmt: skip
            b.docs[rcept] = tuple(files)


# ── 시세 ───────────────────────────────────────────────────────────────────────────────

_Price = tuple[date, float, float, float, float, int, int, int, tuple[str, ...]]


def _equity_at(qs: Sequence[QuarterTruth], day: date, eq0: float) -> float:
    last = eq0
    for q in qs:
        if q.period.end <= day:
            last = float(q.equity_owner)
        else:
            break
    return last


def _flags_on(sp: _Spec, day: date, tdays: Sequence[date]) -> tuple[str, ...]:
    if sp.no == 4 and day in tdays[-5:]:
        return ("halted",)
    if sp.no == 20 and day in tdays[-7:]:
        return ("liquidation",)
    if sp.no == 9 and day >= date(2026, 4, 1):
        return ("managed",)
    return ()


def _build_prices(
    b: _Build, quarters: Mapping[tuple[str, str], tuple[QuarterTruth, ...]]
) -> dict[str, tuple[_Price, ...]]:
    out: dict[str, tuple[_Price, ...]] = {}
    tdays = b.trading_days
    for sp in _SPECS:
        if sp.kind == "pref":
            continue
        rng = _rng(b.seed, "price", sp.code)
        days = [d for d in tdays if d >= b.listed[sp.no]]
        fin = sp.fin
        qs: Sequence[QuarterTruth] = ()
        if fin is not None and sp.corp is not None:
            qs = quarters.get((sp.corp, "CFS")) or quarters[(sp.corp, "OFS")]
        fx = USD_KRW if sp.currency == "USD" else 1.0
        eq0 = (fin.eq0 or fin.rev0 * 3.6) if fin else 3e10
        pbr = fin.pbr if fin else 1.0
        vol = fin.vol if fin else 0.02
        share_of_company = 0.9 if sp.pref_shares else 1.0

        def fair(
            day: date,
            qs: Sequence[QuarterTruth] = qs,
            eq0: float = eq0,
            pbr: float = pbr,
            fx: float = fx,
            share: float = share_of_company,
        ) -> float:
            eq = _equity_at(qs, day, eq0) if qs else eq0
            return math.log(pbr * max(eq, 0.15 * eq0) * fx * share)  # fmt: skip

        x = fair(days[0]) + rng.gauss(0, 0.08) + (0.5 if sp.no == 3 else 0.0)
        rows: list[_Price] = []
        prev_close: float | None = None
        for d in days:
            flags = _flags_on(sp, d, tdays)
            sh = _krx_shares(sp, d)
            if "halted" not in flags:
                x += 0.03 * (fair(d) - x) + rng.gauss(0, vol)
                if "liquidation" in flags:
                    x += math.log(0.78)
            close = _tick(math.exp(x) / sh)
            if "halted" in flags and prev_close is not None:
                close = prev_close
            base = prev_close if prev_close is not None else close
            if sp.no == 17 and d == SPLIT_DAY:
                base = base / 5
            o = _tick(base * math.exp(rng.gauss(0, vol / 3)))
            hi = _tick(max(o, close) * (1 + abs(rng.gauss(0, vol / 2))))
            lo = _tick(min(o, close) * (1 - abs(rng.gauss(0, vol / 2))))
            cap = close * sh
            ratio = 0.0005 if sp.market == "KONEX" else (0.0012 if sp.kind == "spac" else 0.004)
            turnover = 0 if "halted" in flags else int(cap * ratio * math.exp(rng.gauss(0, 0.4)))
            volume = 0 if "halted" in flags else max(1, int(turnover // close))
            rows.append((d, o, hi, lo, close, volume, turnover, sh, flags))
            prev_close = close
        out[sp.code] = tuple(rows)
    # 우선주(#2) — 보통주 종가 × 0.83 언저리
    sp = b.spec(2)
    rng = _rng(b.seed, "price", sp.code)
    rows = []
    for d, *_o, close, _v, _t, _sh, _f in out[b.spec(1).code]:
        c = _tick(close * 0.83 * math.exp(rng.gauss(0, 0.012)))
        o = _tick(c * math.exp(rng.gauss(0, 0.004)))
        tv = int(c * sp.shares * 0.002 * math.exp(rng.gauss(0, 0.4)))
        rows.append((d, o, max(o, c), min(o, c), c, max(1, int(tv // c)), tv, sp.shares, ()))
    out[sp.code] = tuple(rows)
    return out


def _build_investors(b: _Build, prices: Mapping[str, Sequence[_Price]]) -> list[InvestorDay]:
    out: list[InvestorDay] = []
    last = set(b.trading_days[-INVESTOR_DAYS:])
    unit = 1_000_000
    for sp in _SPECS:
        rng = _rng(b.seed, "investor", sp.code)
        for d, *_x, tv, _sh, flags in prices[sp.code]:
            if d not in last or "halted" in flags:
                continue
            scale = max(1, tv // (40 * unit))
            seven = {inv: rng.randint(-5, 5) * scale * unit for inv in INST7}
            foreign = (rng.randint(-30, 30) * scale // 10) * unit
            other_corp = (rng.randint(-3, 3) * scale // 10) * unit
            inst = sum(seven.values())
            indiv = -(foreign + inst + other_corp)
            vals = {
                Investor.FOREIGN: foreign,
                Investor.INSTITUTION: inst,
                Investor.OTHER_CORP: other_corp,
                Investor.INDIVIDUAL: indiv,
                **seven,
            }
            for inv, v in vals.items():
                out.append(InvestorDay(sp.code, d, inv, v, None, "kis", "KRX", Quality.OK))
    return out


# ── 컨센서스·투자의견(KIS 의미값) ──────────────────────────────────────────────────────


def _fiscal_ym(year: int, acc_mt: int) -> str:
    end = fiscal_quarter(year, 4, acc_mt).end
    return f"{end.year}{end.month:02d}"


def _build_consensus(
    b: _Build, quarters: Mapping[tuple[str, str], tuple[QuarterTruth, ...]]
) -> list[ConsensusRow]:
    out: list[ConsensusRow] = []
    snap_days = _tdays(CONSENSUS_FROM, TODAY)
    for sp in _SPECS:
        if sp.consensus not in ("full", "thin") or sp.corp is None:
            continue
        rng = _rng(b.seed, "consensus", sp.code)
        qs = quarters.get((sp.corp, "CFS")) or quarters[(sp.corp, "OFS")]
        by_year: dict[int, list[QuarterTruth]] = {}
        for q in qs:
            by_year.setdefault(q.period.year, []).append(q)
        last_y = sp.fin_last[0]
        ttm = qs[-4:]
        common, pref, treasury = _dart_shares(sp, ttm[-1].period.end)
        den = common - treasury + pref
        financial = sp.induty[:2] in ("64", "65", "66")

        actual_years = [y for y in (last_y - 2, last_y - 1) if len(by_year.get(y, [])) == 4]
        base = {
            "revenue": sum(q.revenue for q in ttm) * 1.03,
            "op": sum(q.op for q in ttm) * 1.03,
            "ni": sum(q.ni for q in ttm) * 1.03,
            "ni_owner": sum(q.ni_owner for q in ttm) * 1.03,
            "da": sum(q.dep + q.amort for q in ttm) * 1.03,
        }
        years = [last_y, last_y + 1] if sp.consensus == "thin" else [last_y, last_y + 1, last_y + 2]
        start = max(CONSENSUS_FROM, b.listed[sp.no] + timedelta(days=1))
        for d in snap_days:
            if d < start:
                continue
            frac = (d - CONSENSUS_FROM).days / 30
            for y in actual_years:
                ys = by_year[y]
                out.append(_snap_row(sp, d, y, False, den, financial, {
                    "revenue": sum(q.revenue for q in ys), "op": sum(q.op for q in ys),
                    "ni": sum(q.ni for q in ys), "ni_owner": sum(q.ni_owner for q in ys),
                    "da": sum(q.dep + q.amort for q in ys),
                }))  # fmt: skip
            for k, y in enumerate(years):
                grow = (1.10**k) * (1 + rng.uniform(-0.002, 0.002))
                rev_mult = (1 + sp.revision * 0.3) ** frac  # 30일마다 같은 비율(리비전 창 30일)
                mult = (1 + sp.revision) ** frac
                vals = {
                    "revenue": base["revenue"] * grow * rev_mult,
                    "op": base["op"] * grow * mult,
                    "ni": base["ni"] * grow * mult,
                    "ni_owner": base["ni_owner"] * grow * mult,
                    "da": base["da"] * grow,
                }
                if sp.no == 8 and k == 0:  # FY1 순이익 −40억 → +15억(부호 전환 — TURN)
                    w = min(1.0, (d - CONSENSUS_FROM).days / 70)
                    for key, lo, hi in (("ni", -4e9, 1.5e9), ("ni_owner", -4e9, 1.5e9),
                                        ("op", -3e9, 2.5e9)):  # fmt: skip
                        vals[key] = lo + (hi - lo) * w
                out.append(_snap_row(sp, d, y, True, den, financial, vals))
    return out


def _eok(v: float) -> int:
    """KIS 추정 금액 단위(억원 [확인 필요 #29])로 맞춘 원."""
    return round(v / 100_000_000) * 100_000_000


def _snap_row(
    sp: _Spec,
    d: date,
    fy: int,
    est: bool,
    den: int,
    financial: bool,
    vals: Mapping[str, float],
) -> ConsensusRow:
    return ConsensusRow(
        sp.code, d, _fiscal_ym(fy, sp.acc_mt), est, _eok(vals["revenue"]), _eok(vals["op"]),
        _eok(vals["ni"]), _eok(vals["ni_owner"]), float(round(vals["ni_owner"] / den)),
        None if financial else _eok(vals["op"] + vals["da"]), {"_source": SYNTHETIC},
    )  # fmt: skip


_BROKERS: Final = ("가상증권", "모의증권", "시뮬투자증권", "샘플증권", "테스트금융투자", "예시증권")


def _build_opinions(b: _Build, prices: Mapping[str, Sequence[_Price]]) -> list[OpinionRow]:
    out: list[OpinionRow] = []
    for sp in _SPECS:
        if sp.consensus not in ("full", "thin"):
            continue
        rng = _rng(b.seed, "opinion", sp.code)
        closes = {row[0]: row[4] for row in prices[sp.code]}
        for k in range(sp.brokers):
            broker = _BROKERS[k]
            prev: str | None = None
            stale = sp.no == 1 and k == sp.brokers - 1  # 마지막 의견이 90일보다 오래됨(창 밖)
            for j in reversed(range(4)):
                back = 10 + 40 * j + rng.randrange(0, 12) + (100 if stale else 0)
                d = _on_or_before(TODAY - timedelta(days=back))
                if d < b.listed[sp.no] + timedelta(days=5):
                    continue
                ref = closes.get(d) or next(
                    (c for day, c in sorted(closes.items(), reverse=True) if day <= d), None
                )
                if ref is None:
                    continue
                opinion = "중립" if (sp.no == 8 and k == 2) else "매수"
                target: float = _tick(ref * (1.18 + 0.04 * k) * (1 + rng.uniform(-0.03, 0.03)))
                if sp.no == 4 and k == 3 and j == 0:
                    opinion, target = "Not Rated", 0.0  # 미제시 — 목표가 컨센서스에서 뺀다
                out.append(OpinionRow(sp.code, broker, d, opinion, prev, target))
                prev = opinion
    out.sort(key=lambda r: (r.code, r.broker, r.opinion_date))
    return out
