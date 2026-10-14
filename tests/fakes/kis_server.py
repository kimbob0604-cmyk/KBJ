"""가짜 KIS — REST(접근토큰·접속키 발급, 주식·GEX TR)와 마스터 배포 서버. 합성 데이터만(U3).

GEXLAB `tests/fakes/kis_server.py`(`FakeKisServer` — `token_posts`·`bearers`·`max_in_window`·
`inject`·`make_client`)의 KBJ 판이다. GX 판은 GX 모듈(poller·옵션 체인 fixture·`data.store`)에 묶여
있어 그대로 옮기지 않고, 하루 운영 시뮬레이션(tests/sim)이 쓰는 것만 같은 이름으로 다시 만들었다.
GEX 체인·분봉 세부(전광판 행사가·야간 시각 표기)는 P7 승격 때 GX 판을 옮긴다.

- **발급**: POST 토큰 경로(접근토큰 — 24시간)·접속키 경로(웹소켓 접속키). 앱키·시크릿이 맞아야 하고,
  누가 보냈는지(`transport(consumer)` 로 만든 이름)를 `posts` 에 남긴다 → '발급은 auth 에서만' 단언.
- **조회**: GET. Bearer 토큰이 이 서버가 발급했고 만료 전이어야 한다(아니면 `EGW00123`). 앱키·
  시크릿 헤더가 맞아야 한다. `tr_id` 와 경로가 TR 표(`TRS`)와 맞아야 한다(아니면 `OPSQ0002`).
- 응답 본문은 (tr_id, 파라미터, 날짜, 10분 슬롯)으로 정해지는 **합성 값**(고정 시드 — sha256).
  종목은 합성 20개(`SYMBOLS` — 앞 10개 코스피·뒤 10개 코스닥, `market_symbols`). P3 TR(묶음 C):
  FHKST01010100 상태 칸·OHLC, FHKST01010900 3일치 3구분(금액 = 수량 × 종가 — 자릿수 대조가 맞게),
  FHPTJ04040000 4구분 합 0·기관 7구분 합 = 기관([추정 칸]), FHPTJ04400000·FHPST01710000 시장마다
  `RANK_ROWS` 행, FHPST02400000 iNAV·괴리율, FHPUP02100000 지수, FHPUP02140000 업종
  `SECTORS_PER_MARKET` 개. 모양은 `kbj.data.private.kis.{investors,quotes,ranks,etf,index}` 파서가
  읽는 칸이다(tests/unit/fakes/test_kis_server_p3.py).
- **P4 컨센서스 TR(묶음 K)**: TR id·경로는 `kbj.data.private.kis.datasets.CONSENSUS_TRS` 에서 채운다
  (문자열을 다시 쓰지 않는다 — tests/unit/kis/test_consensus_tr_single.py). 출력 모양은
  `kbj.data.private.kis.consensus` 파서의 상수 표(행 순서·열·단위 — [확인 필요 #29])를 따른다.
  `FakeKisServer(consensus=world)` 로 합성 20종목 세계(tests/fixtures/synthetic/p4_world.py — 의미값
  `consensus_rows`·`opinion_rows`)를 주면 세계 종목은 그 값을 TR 출력으로 바꿔 싣고(커버 없는 종목은
  빈 출력), 세계 밖 종목(시뮬레이션의 `SYMBOLS`)은 고정 시드 합성 값을 준다(`estimate_body`·
  `opinion_body` — tests/unit/fakes/test_kis_server_p4.py).
- **P5 F2(장중 투자자 1분·프로그램매매)**: FHPTJ04030000·FHPPG04600001·FHPPG04600101·HHPPG046600C1·
  FHPPG04650101·FHPPG04650201(경로·칸 이름은 파서 표
  `kbj.data.private.kis.{investors_minute,program}`
  — 전부 [확인 필요 #39~#41]). 값은 고정 시드 + **그날 09:00 부터 지난 분 수**(`f2_output`) — 1분
  수집이 부를 때마다 누적이 늘어난다. 검산 ①②④ 가 정확히 맞고, 09:00 전은 값이 모두 0(장전),
  15:30 뒤는 마지막 누적 그대로(tests/unit/fakes/test_kis_server_p5.py).
- **P8 장중 슬롯 시세(묶음 EVL)**: `FakeKisServer(quotes=world)` 로 합성 P8 세계
  (tests/fixtures/synthetic/p8_world.py — `slots_for(code, 날짜)`)를 주면 세계 종목의
  FHKST01010100 은
  지금 시각(가짜 시계) 이하 마지막 슬롯의 시가·고가·저가·현재가·누적 거래량·등락률을 싣는다
  (`slot_quote_body` — 장중 재생 백테스트가 같은 값을 읽는다). 세계 밖 종목은 고정 시드 합성
  (tests/unit/fakes/test_kis_server_p8.py).
- **오류 주입** `inject(msg_cd, count=1, *, start=None, until=None, tr_id=None, consumers=None)`:
  `EGW00201`(초당 한도)·`EGW00123`(토큰 거절)·`EGW00133`(발급 1분 1회 — POST)·`HTTP500`. 시각은 가짜
  시계 기준, consumers 를 주면 그 프로세스의 요청만.
- **기록**: `calls`(시각·consumer·방법·경로·tr_id·파라미터·Bearer·결과), `token_posts`·
  `approval_posts`, `issued`(발급한 값·시각·만료), `max_in_window(초)`(어떤 반열린 창의 최대 건수).
- **마스터**: `master_transport(consumer)` — 배포 경로의 `fo_idx_code_mts.mst.zip` 을 합성 마스터
  줄(tests/fixtures/synthetic/kis/master_lines.json)로 만든 zip 으로 준다(cp949, 한 파일).

키·토큰 값은 모두 가짜다. 토큰은 JWT 모양이 아니다(공개 안전 검사에 걸리지 않게).
"""

from __future__ import annotations

import functools
import hashlib
import io
import json
import threading
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Protocol
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr
from redis import Redis

from kbj.core.fin_rows import ConsensusRow, OpinionRow
from kbj.core.p5_rows import MINUTE_INVESTORS
from kbj.core.rules_rows import QuoteSlot
from kbj.data.private.kis.consensus import (
    AMOUNT_UNIT_KRW,
    COLUMNS,
    IND_ROWS,
    OPINION_FIELDS,
    OUT_HEAD,
    OUT_IND,
    OUT_OPINION,
    OUT_PERIODS,
    OUT_PL,
    PERIOD_FIELD,
    PL_ROWS,
    TR_ESTIMATE,
    TR_OPINION,
)
from kbj.data.private.kis.credentials import KisCredentials
from kbj.data.private.kis.datasets import CONSENSUS_TRS
from kbj.data.private.kis.investors_minute import TR_MARKET_MINUTE
from kbj.data.private.kis.investors_minute import field_names as minute_field_names
from kbj.data.private.kis.master import FO_MASTER_FILE
from kbj.data.private.kis.program import INVESTOR_CODES as PROGRAM_INVESTOR_CODES
from kbj.data.private.kis.program import PROGRAM_TRS
from kbj.data.private.kis.program import investor_field as program_investor_field
from kbj.data.private.kis.program import market_field as program_market_field
from kbj.data.private.kis.program import stock_field as program_stock_field
from kbj.data.private.kis.rest import MINUTE_PATH, MINUTE_TR, KisRestClient
from kbj.data.private.kis.token import reader
from kbj.data.ratelimit import Clock, RedisRateLimiter
from kbj.services.auth.issuer import APPROVAL_PATH, TOKEN_PATH

KST: Final = ZoneInfo("Asia/Seoul")
FIX: Final = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic" / "kis"
MASTER_PATH: Final = "/common/master/" + FO_MASTER_FILE
TOKEN_LIFE: Final = timedelta(hours=24)

# 가짜 자격 — 실제 키 모양(PS+34자·base64 180자)이 아니다
APP_KEY: Final = "PSsimAPPKEY0123456789abcd"
APP_SECRET: Final = "SIMsecretVALUE9876543210zyx"

# 합성 종목 20개(코드 99xxxx — 실제 상장 코드와 겹치지 않는 대역). 이름도 합성
SYMBOLS: Final[tuple[str, ...]] = tuple(f"99{i:04d}" for i in range(10, 210, 10))

# TR → 경로. 주식 TR 은 kbj/data/private/kis/datasets.py 의 notes, GEX TR 은 GX 실측 경로
# (scripts/probe_common.py). 컨센서스 TR 은 datasets.CONSENSUS_TRS 상수에서(묶음 K — #29 전)
TRS: Final[dict[str, str]] = {
    "FHKST01010100": "/uapi/domestic-stock/v1/quotations/inquire-price",
    "FHKST01010900": "/uapi/domestic-stock/v1/quotations/inquire-investor",
    "FHPTJ04040000": "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market",
    "FHPTJ04400000": "/uapi/domestic-stock/v1/quotations/foreign-institution-total",
    "FHPST01710000": "/uapi/domestic-stock/v1/quotations/volume-rank",
    "FHPST02400000": "/uapi/etfetn/v1/quotations/inquire-price",
    **dict(CONSENSUS_TRS.values()),
    MINUTE_TR: MINUTE_PATH,
    "FHPIF05030100": "/uapi/domestic-futureoption/v1/quotations/display-board-callput",
    "FHPTJ04030000": "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market",
    # P3 지수·업종(market.intraday) — [추정 TR·경로] conflict_map §1.13, 체크리스트 #24.
    # 경로는 kbj.data.private.kis.parse.PATHS 와 같아야 한다(tests/unit/fakes 가 본다)
    "FHPUP02100000": "/uapi/domestic-stock/v1/quotations/inquire-index-price",
    "FHPUP02140000": "/uapi/domestic-stock/v1/quotations/inquire-index-category-price",
    # P5 F2 프로그램매매(묶음 F2 — 경로는 파서 표 kbj.data.private.kis.program.PROGRAM_TRS)
    **{spec.tr_id: spec.path for spec in PROGRAM_TRS.values()},
}

# 본문 없는 오류 주입(ERRORS 밖) — HTTP500·HTTP429(P5 F2)·TIMEOUT(P5 F2 — httpx.ReadTimeout)
INJECT_PLAIN: Final = frozenset({"HTTP500", "HTTP429", "TIMEOUT"})
OK_MSG: Final = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다."}
ERRORS: Final[dict[str, tuple[int, dict[str, str]]]] = {
    "EGW00201": (
        500,
        {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."},
    ),
    "EGW00123": (500, {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}),
    "EGW00133": (
        403,
        {
            "error_code": "EGW00133",
            "error_description": "접근토큰 발급 잠시 후 다시 시도하세요(1분당 1회)",
        },
    ),
}


@dataclass(frozen=True)
class KisCall:
    """받은 요청 하나(값은 가짜)."""

    at: datetime
    consumer: str
    method: str
    path: str
    tr_id: str
    params: dict[str, str]
    bearer: str | None
    status: int
    msg_cd: str


@dataclass(frozen=True)
class Issued:
    kind: str  # "token" | "ws_key"
    value: str
    at: datetime
    expires_at: datetime
    consumer: str


@dataclass
class _Rule:
    code: str
    count: int
    start: datetime | None
    until: datetime | None
    tr_id: str | None
    consumers: frozenset[str] | None = None
    hits: int = 0

    def active(self, now: datetime, tr_id: str, consumer: str) -> bool:
        if self.count <= 0:
            return False
        if self.consumers is not None and consumer not in self.consumers:
            return False
        if self.start is not None and now < self.start:
            return False
        if self.until is not None and now >= self.until:
            return False
        return self.tr_id is None or self.tr_id == tr_id


def _seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _num(seed: int, lo: int, hi: int) -> int:
    return lo + seed % (hi - lo + 1)


def _days_back(day: str, n: int) -> list[str]:
    """day(YYYYMMDD) 부터 달력일로 n 개(최근 먼저) — 합성 일별 행의 날짜."""
    d = datetime.strptime(day, "%Y%m%d").replace(tzinfo=KST)
    return [(d - timedelta(days=i)).strftime("%Y%m%d") for i in range(n)]


def market_symbols(market: str) -> tuple[str, ...]:
    """합성 시장 구성: 앞 10종목 코스피(0001)·뒤 10종목 코스닥(1001). 그 밖(0000 등)은 전부."""
    if market == "0001":
        return SYMBOLS[:10]
    if market == "1001":
        return SYMBOLS[10:]
    return SYMBOLS


def _quote_out(s: int, code: str) -> dict[str, Any]:
    price = _num(s, 5_000, 300_000)
    vol = _num(s >> 7, 1_000, 5_000_000)
    return {
        "stck_shrn_iscd": code,
        "hts_kor_isnm": f"합성{code[-3:]}",
        "rprs_mrkt_kor_name": "KOSPI" if code in SYMBOLS[:10] else "KOSDAQ",
        "stck_prpr": str(price),
        "stck_oprc": str(price - _num(s >> 13, 0, price // 50)),
        "stck_hgpr": str(price + _num(s >> 15, 0, price // 40)),
        "stck_lwpr": str(price - _num(s >> 17, 0, price // 30)),
        "prdy_vrss": str(_num(s >> 3, 0, 3_000) - 1_500),
        "prdy_ctrt": f"{(_num(s >> 5, 0, 600) - 300) / 100:.2f}",
        "acml_vol": str(vol),
        "acml_tr_pbmn": str(price * vol),
        "hts_avls": str(_num(s >> 11, 100, 900_000)),
        "lstn_stcn": str(_num(s >> 19, 1_000_000, 900_000_000)),
        "iscd_stat_cls_code": "55",
        "mang_issu_cls_code": "N",
        "temp_stop_yn": "N",
        "sltr_yn": "N",
    }


def _investor_rows(s: int, day: str, close: int) -> list[dict[str, Any]]:
    """종목 투자자 일별 3구분(최근 3일). 금액(백만원) = 수량 × 종가 ÷ 1e6 — 자릿수 대조가 맞게."""
    rows: list[dict[str, Any]] = []
    for i, d in enumerate(_days_back(day, 3)):
        row: dict[str, Any] = {"stck_bsop_date": d, "stck_clpr": str(close)}
        for j, who in enumerate(("prsn", "frgn", "orgn")):
            qty = _num(s >> (i * 3 + j), 0, 200_000) - 100_000
            row[f"{who}_ntby_qty"] = str(qty)
            row[f"{who}_ntby_tr_pbmn"] = str(round(qty * close / 1_000_000))
        rows.append(row)
    return rows


INST7_PREFIX: Final = ("scrt", "ivtr", "pe_fund", "insu", "bank", "fund", "mrbn")


def _market_investor_rows(s: int, day: str) -> list[dict[str, Any]]:
    """시장 투자자 일별(백만원) — 4구분 합 0·기관 7구분 합 = 기관이 되게 만든다(합성 원장 성질)."""
    rows: list[dict[str, Any]] = []
    for i, d in enumerate(_days_back(day, 3)):
        k = s >> i
        inst7 = [_num(k >> (2 * j), 0, 400_000) - 200_000 for j in range(7)]
        inst = sum(inst7)
        frgn = _num(k >> 17, 0, 900_000) - 450_000
        corp = _num(k >> 19, 0, 200_000) - 100_000
        prsn = -(inst + frgn + corp)
        row: dict[str, Any] = {
            "stck_bsop_date": d,
            "bstp_nmix_prpr": f"{2_000 + _num(k >> 21, 0, 90_000) / 100:.2f}",
            "prsn_ntby_tr_pbmn": str(prsn),
            "frgn_ntby_tr_pbmn": str(frgn),
            "orgn_ntby_tr_pbmn": str(inst),
            "etc_corp_ntby_tr_pbmn": str(corp),
        }
        for name, v in zip(INST7_PREFIX, inst7, strict=True):
            row[f"{name}_ntby_tr_pbmn"] = str(v)
        rows.append(row)
    return rows


RANK_ROWS: Final = 10  # 합성 거래대금 순위 행 수(시장마다)
SECTORS_PER_MARKET: Final = 5  # 합성 업종 수(시장마다)


# ── P4 컨센서스(묶음 K) — 의미값 ↔ TR 출력 ─────────────────────────────────────────────────
CONSENSUS_TR_IDS: Final = frozenset({TR_ESTIMATE.tr_id, TR_OPINION.tr_id})
SYN_BROKERS: Final = ("합성증권", "가상투자증권", "모의금융투자")  # 세계 밖 종목의 합성 증권사


class ConsensusWorld(Protocol):
    """가짜 서버가 싣는 컨센서스 의미값 — tests/fixtures/synthetic/p4_world.P4World 가 이 모양."""

    @property
    def stocks(self) -> Sequence[Any]: ...  # .code 가 있는 항목

    def consensus_rows(self, code: str, snap_date: date | None = None) -> list[ConsensusRow]: ...

    def opinion_rows(self, code: str, *, upto: date | None = None) -> list[OpinionRow]: ...


class QuoteWorld(Protocol):
    """P8 장중 슬롯 시세 세계 — tests/fixtures/synthetic/p8_world.P8World 가 이 모양(묶음 EVL)."""

    def slots_for(self, code: str, d: date) -> list[QuoteSlot]: ...


def _plain_num(x: float | int | None) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return str(int(x))
    return repr(x) if isinstance(x, float) else str(x)


def slot_quote_body(slot: QuoteSlot) -> dict[str, Any]:
    """슬롯 시세(QuoteSlot) → FHKST01010100 응답 본문(`quotes.parse_quote`·`bar_from_quote` 가 읽는
    칸). 못 받은 칸은 비운다(0 으로 채우지 않는다). 정지면 temp_stop_yn=Y."""
    halted = "halted" in slot.flags
    out: dict[str, Any] = {
        "stck_shrn_iscd": slot.code,
        "hts_kor_isnm": f"합성{slot.code[-2:]}",
        "stck_prpr": _plain_num(slot.price),
        "stck_oprc": _plain_num(slot.open),
        "stck_hgpr": _plain_num(slot.high),
        "stck_lwpr": _plain_num(slot.low),
        "prdy_ctrt": _plain_num(slot.change_pct),
        "acml_vol": _plain_num(slot.volume),
        "iscd_stat_cls_code": "58" if halted else "55",
        "mang_issu_cls_code": "Y" if "managed" in slot.flags else "N",
        "temp_stop_yn": "Y" if halted else "N",
        "sltr_yn": "Y" if "liquidation" in slot.flags else "N",
    }
    if slot.turnover is not None:
        out["acml_tr_pbmn"] = str(slot.turnover)
    return out


def world_slot(world: QuoteWorld, code: str, local: datetime) -> QuoteSlot | None:
    """그 시각(KST)에 보이는 슬롯 — 시각 이하의 마지막 슬롯(장 전·세계 밖 종목이면 None)."""
    got = [s for s in world.slots_for(code, local.date()) if s.slot_ts <= local]
    return max(got, key=lambda s: s.slot_ts) if got else None


def _amount(v: int | None) -> str:
    if v is None:
        return ""
    if v % AMOUNT_UNIT_KRW:
        raise ValueError(f"금액이 출력 단위({AMOUNT_UNIT_KRW}원)로 나누어지지 않는다: {v}")
    return str(v // AMOUNT_UNIT_KRW)


def _plain(x: float | None) -> str:
    if x is None:
        return ""
    return str(int(x)) if float(x).is_integer() else f"{x:.2f}"


def _growth(cur: int | None, prev: int | None) -> str:
    if cur is None or prev is None or prev == 0:
        return ""
    return f"{(cur - prev) / abs(prev) * 100:.2f}"


def estimate_body(code: str, rows: Sequence[ConsensusRow], estdate: date | None) -> dict[str, Any]:
    """`ConsensusRow`(한 스냅) → 종목추정실적 TR 출력(파서 상수 표 모양 — [확인 필요 #29]).

    행이 없으면 빈 출력(커버 없음 — 파서가 KisEmpty). 열은 결산년월 순 최대 5개, 추정 열은 'E'
    표지. 금액은 억원(나누어떨어져야 한다 — 세계는 억원 단위로 맞춘 원), EPS 는 원."""
    cols = sorted(rows, key=lambda r: r.fiscal_ym)[: len(COLUMNS)]
    if not cols:
        return {**OK_MSG, OUT_HEAD: {}, OUT_PL: [], OUT_IND: [], OUT_PERIODS: []}
    head = {"sht_cd": code, "estdate": "" if estdate is None else estdate.strftime("%Y%m%d")}
    periods = [{PERIOD_FIELD: r.fiscal_ym + ("E" if r.is_estimate else "")} for r in cols]
    pl: list[dict[str, str]] = []
    for name in PL_ROWS:
        line: dict[str, str] = {}
        for j, (col, r) in enumerate(zip(COLUMNS, cols, strict=False)):
            if name.endswith("_growth_pct"):
                metric = name.removesuffix("_growth_pct")
                prev = getattr(cols[j - 1], metric) if j else None
                line[col] = _growth(getattr(r, metric), prev)
            else:
                line[col] = _amount(getattr(r, name))
        pl.append(line)
    ind: list[dict[str, str]] = []
    for name in IND_ROWS:
        line = {}
        for col, r in zip(COLUMNS, cols, strict=False):
            if name == "ebitda":
                line[col] = _amount(r.ebitda)
            elif name == "eps":
                line[col] = _plain(r.eps)
            else:
                line[col] = ""  # 비율 칸은 싣지 않는다(계산에 쓰지 않는 칸 — extra)
        ind.append(line)
    return {**OK_MSG, OUT_HEAD: head, OUT_PL: pl, OUT_IND: ind, OUT_PERIODS: periods}


def opinion_body(rows: Sequence[OpinionRow]) -> dict[str, Any]:
    """`OpinionRow` → 종목투자의견 TR 출력(최근 날짜 먼저). 행이 없으면 빈 출력."""
    f = OPINION_FIELDS
    out = [
        {
            f["date"][0]: o.opinion_date.strftime("%Y%m%d"),
            f["broker"][0]: o.broker,
            f["opinion"][0]: o.opinion or "",
            f["opinion_prev"][0]: o.opinion_prev or "",
            f["target"][0]: _plain(o.target_price),
        }
        for o in sorted(rows, key=lambda o: (o.opinion_date, o.broker), reverse=True)
    ]
    return {**OK_MSG, OUT_OPINION: out}


def _ymd(text: str) -> date:
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def synthetic_consensus(code: str, day: date) -> list[ConsensusRow]:
    """세계 밖 종목(시뮬레이션 `SYMBOLS`)의 합성 추정실적 — 고정 시드, 날마다 조금씩 움직인다."""
    s = _seed("consensus", code)
    rev0 = _num(s, 1_000, 90_000)  # 억원
    margin = _num(s >> 9, 3, 18) / 100
    shares = _num(s >> 17, 5_000_000, 500_000_000)
    drift = 1 + (day.toordinal() % 60) / 1_000
    out: list[ConsensusRow] = []
    for k, year in enumerate(range(day.year - 2, day.year + 3)):
        est = k >= 2
        grow = 1.06**k * (drift if est else 1.0)
        rev = round(rev0 * grow)
        op = round(rev * margin)
        ni = round(op * 0.75)
        out.append(
            ConsensusRow(
                code=code,
                snap_date=day,
                fiscal_ym=f"{year}12",
                is_estimate=est,
                revenue=rev * AMOUNT_UNIT_KRW,
                op=op * AMOUNT_UNIT_KRW,
                ni=ni * AMOUNT_UNIT_KRW,
                ni_owner=None,
                eps=float(round(ni * AMOUNT_UNIT_KRW / shares)),
                ebitda=round(op * 1.3) * AMOUNT_UNIT_KRW,
            )
        )
    return out


def synthetic_opinions(code: str, start: date, end: date) -> list[OpinionRow]:
    """세계 밖 종목의 합성 투자의견 — 증권사 셋, 각 [start, end] 안 의견 둘(고정 시드)."""
    s = _seed("opinion", code)
    base = _num(s, 5_000, 300_000)
    out: list[OpinionRow] = []
    for k, broker in enumerate(SYN_BROKERS):
        prev: str | None = None
        for j in (1, 0):
            d = end - timedelta(days=7 * k + 35 * j)
            if d < start:
                continue
            target = float(round(base * (1.15 + 0.05 * k + 0.02 * j), -2))
            out.append(OpinionRow(code, broker, d, "매수", prev, target))
            prev = "매수"
    return out


def consensus_output(
    tr_id: str, params: dict[str, str], day: str, world: ConsensusWorld | None = None
) -> dict[str, Any]:
    """컨센서스 TR 출력. 세계 종목이면 세계의 의미값(없으면 빈 출력), 아니면 고정 시드 합성."""
    covered = {str(getattr(x, "code", "")) for x in world.stocks} if world is not None else set()
    on = _ymd(day)
    if tr_id == TR_ESTIMATE.tr_id:
        code = params.get("SHT_CD", "")
        if world is not None and code in covered:
            rows = [r for r in world.consensus_rows(code) if r.snap_date <= on]
            last = max((r.snap_date for r in rows), default=None)
            snap = [r for r in rows if r.snap_date == last]
            return estimate_body(code, snap, last)
        return estimate_body(code, synthetic_consensus(code, on), on)
    if tr_id == TR_OPINION.tr_id:
        code = params.get("FID_INPUT_ISCD", "")
        start = _ymd(params.get("FID_INPUT_DATE_1", day))
        end = min(_ymd(params.get("FID_INPUT_DATE_2", day)), on)
        if world is not None and code in covered:
            rows = [o for o in world.opinion_rows(code, upto=end) if o.opinion_date >= start]
            return opinion_body(rows)
        return opinion_body(synthetic_opinions(code, start, end))
    raise ValueError(f"컨센서스 TR 이 아니다: {tr_id}")


def synthetic_output(
    tr_id: str, params: dict[str, str], day: str, slot: str = ""
) -> dict[str, Any]:
    """TR 별 합성 응답 본문(정상). 같은 (tr_id, 파라미터, 날짜, 슬롯)이면 같은 값."""
    if tr_id in CONSENSUS_TR_IDS:
        return consensus_output(tr_id, params, day)
    code = params.get("FID_INPUT_ISCD", "") or params.get("fid_input_iscd", "")
    s = _seed(tr_id, sorted(params.items()), day, slot)
    price = _num(s, 5_000, 300_000)
    if tr_id == "FHKST01010100":
        return {**OK_MSG, "output": _quote_out(s, code)}
    if tr_id == "FHKST01010900":
        return {**OK_MSG, "output": _investor_rows(s, day, price)}
    if tr_id == "FHPTJ04040000":
        return {**OK_MSG, "output1": _market_investor_rows(s, day)}
    if tr_id == "FHPTJ04400000":
        rows = [
            {
                "mksc_shrn_iscd": c,
                "hts_kor_isnm": f"합성{c[-3:]}",
                "stck_prpr": str(_num(s >> i, 5_000, 300_000)),
                "frgn_ntby_qty": str(_num(s >> (i + 1), 0, 200_000) - 100_000),
                "orgn_ntby_qty": str(_num(s >> (i + 2), 0, 200_000) - 100_000),
                "frgn_ntby_tr_pbmn": str(_num(s >> (i + 3), 0, 90_000) - 45_000),
                "orgn_ntby_tr_pbmn": str(_num(s >> (i + 4), 0, 90_000) - 45_000),
            }
            for i, c in enumerate(market_symbols(code)[:RANK_ROWS])
        ]
        return {**OK_MSG, "output": rows}
    if tr_id == "FHPST01710000":
        rows = [
            {
                "data_rank": str(i + 1),
                "mksc_shrn_iscd": c,
                "hts_kor_isnm": f"합성{c[-3:]}",
                "stck_prpr": str(_num(s >> i, 5_000, 300_000)),
                "prdy_ctrt": f"{(_num(s >> (i + 1), 0, 600) - 300) / 100:.2f}",
                "acml_vol": str(_num(s >> (i + 2), 1_000, 5_000_000)),
                "acml_tr_pbmn": str((RANK_ROWS - i) * 10_000_000_000 + _num(s >> i, 0, 9_999)),
            }
            for i, c in enumerate(market_symbols(code)[:RANK_ROWS])
        ]
        return {**OK_MSG, "output": rows}
    if tr_id == "FHPST02400000":
        nav = price + _num(s >> 3, 0, 200) / 100 - 1
        return {
            **OK_MSG,
            "output": {
                "stck_prpr": str(price),
                "nav": f"{nav:.2f}",
                "dprt": f"{(price / nav - 1) * 100:.2f}",
                "acml_vol": str(_num(s >> 5, 1_000, 900_000)),
                "acml_tr_pbmn": str(price * _num(s >> 7, 1_000, 900_000)),
            },
        }
    if tr_id == "FHPUP02100000":
        return {
            **OK_MSG,
            "output": {
                "bstp_nmix_prpr": f"{500 + _num(s, 0, 300_000) / 100:.2f}",
                "bstp_nmix_prdy_ctrt": f"{(_num(s >> 3, 0, 600) - 300) / 100:.2f}",
                "acml_vol": str(_num(s >> 5, 1_000_000, 900_000_000)),
                "acml_tr_pbmn": str(_num(s >> 7, 1_000_000, 20_000_000)),
            },
        }
    if tr_id == "FHPUP02140000":
        base = int(code or "0")
        sectors = [
            {
                "bstp_cls_code": f"{base + 5 + i:04d}",
                "hts_kor_isnm": f"합성업종{base + 5 + i:04d}",
                "bstp_nmix_prpr": f"{500 + _num(s >> i, 0, 300_000) / 100:.2f}",
                "bstp_nmix_prdy_ctrt": f"{(_num(s >> (i + 1), 0, 600) - 300) / 100:.2f}",
                "acml_tr_pbmn": str(_num(s >> (i + 2), 10_000, 2_000_000)),
            }
            for i in range(SECTORS_PER_MARKET)
        ]
        return {**OK_MSG, "output1": {"bstp_nmix_prpr": "1.00"}, "output2": sectors}
    if tr_id == MINUTE_TR:
        bars = [
            {
                "stck_bsop_date": day,
                "stck_cntg_hour": f"{15 - i // 60:02d}{59 - i % 60:02d}00",
                "futs_prpr": f"{400 + _num(s >> i, 0, 2_000) / 100:.2f}",
                "cntg_vol": str(_num(s >> (i + 2), 1, 5_000)),
            }
            for i in range(5)
        ]
        return {**OK_MSG, "output1": {"hts_kor_isnm": "합성선물"}, "output2": bars}
    rows = [
        {
            "rank": str(i + 1),
            "mksc_shrn_iscd": SYMBOLS[(_num(s >> i, 0, 99) + i) % len(SYMBOLS)],
            "value": str(_num(s >> (i + 1), 1, 9_000_000)),
        }
        for i in range(3)
    ]
    return {**OK_MSG, "output": rows}


# ── P5 F2 — 장중 투자자 1분·프로그램매매 TR(묶음 F2 — 이 절만 F2 가 고친다) ───────────────────────
# TR id·경로·칸 이름은 파서 모듈(`kbj.data.private.kis.{investors_minute,program}`)의 표를 import
# 한다
# (문자열을 다시 쓰지 않는다 — 칸·단위는 그 표와 같이 [확인 필요 #39~#41]). 값은 (TR, 파라미터,
# 날짜)
# 고정 시드 + **그날 09:00 부터 지난 분 수**로 정해진다 — 1분 수집이 부를 때마다 누적이 늘어난다
# (분 이력이 아니라 '오늘 누적 한 줄' TR 흉내). 검산 ①(현물 합 0 — 모든 조합에 같이 심는다)·②(기관 =
# 7세부 합)·④(전체 = 차익 + 비차익, 합계 = 위탁 + 자기)가 정확히 맞게 만든다. 09:00 전(장전)은 값이
# 모두 0(파서가 공표 전으로 본다), 15:30 뒤는 마지막 누적 그대로.
F2_OPEN_MIN: Final = 9 * 60
F2_SESSION_MIN: Final = 391  # 09:00~15:30
F2_TR_IDS: Final = frozenset(
    {TR_MARKET_MINUTE.tr_id, *(spec.tr_id for spec in PROGRAM_TRS.values())}
)
_ORGN7: Final = ("scrt", "ivtr", "pe_fund", "bank", "insu", "mrbn", "fund")


def f2_minute_index(hhmm: str) -> int:
    """HHMM → 그날 누적에 든 분 수(09:00 = 1분째, 장전 0, 15:30 뒤 391)."""
    if not hhmm:
        return F2_SESSION_MIN
    k = int(hhmm[:2]) * 60 + int(hhmm[2:4]) - F2_OPEN_MIN + 1
    return max(0, min(F2_SESSION_MIN, k))


def _investor_increments(seed: int, i: int) -> dict[str, tuple[int, int, int, int]]:
    """i 번째 분의 증가분(매도 대금·매수 대금(백만원)·매도·매수 수량) — 검산 ①② 가 맞게."""
    inc: dict[str, tuple[int, int, int, int]] = {}
    for j, inv in enumerate((*_ORGN7, "frgn", "etc_corp", "etc_orgt")):
        s = _seed(seed, i, j)
        sv, bv = _num(s, 0, 400), _num(s >> 9, 0, 400)
        inc[inv] = (sv, bv, sv * 10, bv * 10)
    inc["orgn"] = tuple(sum(inc[x][k] for x in _ORGN7) for k in range(4))  # type: ignore[assignment]
    rest = ("frgn", "orgn", "etc_corp", "etc_orgt")
    net = sum(inc[x][1] - inc[x][0] for x in rest)
    qty = sum(inc[x][3] - inc[x][2] for x in rest)
    ps = _num(_seed(seed, i, "prsn"), 200, 800) + max(0, net)
    inc["prsn"] = (ps, ps - net, ps * 10, ps * 10 - qty)
    return inc


@functools.lru_cache(maxsize=64)
def _investor_cum_table(seed: int) -> tuple[dict[str, tuple[int, int, int, int]], ...]:
    """k = 0..391 분 누적표(한 조합·하루) — 매분 부를 때 다시 더하지 않게 한 번만 만든다."""
    cum = {inv: (0, 0, 0, 0) for inv in MINUTE_INVESTORS}
    out = [dict(cum)]
    for i in range(F2_SESSION_MIN):
        for inv, inc in _investor_increments(seed, i).items():
            c = cum[inv]
            cum[inv] = (c[0] + inc[0], c[1] + inc[1], c[2] + inc[2], c[3] + inc[3])
        out.append(dict(cum))
    return tuple(out)


def investor_minute_row(params: dict[str, str], day: str, k: int) -> dict[str, Any]:
    """FHPTJ04030000 한 줄 — k 분까지의 누적(백만원·계약). k = 0 이면 모두 '0'."""
    seed = _seed(
        "F2-minute", params.get("FID_INPUT_ISCD", ""), params.get("FID_INPUT_ISCD_2", ""), day
    )
    cum = _investor_cum_table(seed)[k]
    row: dict[str, Any] = {}
    for inv in MINUTE_INVESTORS:
        sv, bv, sq, bq = cum[inv]
        names = minute_field_names(inv)
        net_qty_name = names["net_qty"][1 if inv in ("pe_fund", "etc_orgt", "etc_corp") else 0]
        row[names["sell_value"][0]] = str(sv)
        row[names["buy_value"][0]] = str(bv)
        row[names["net_value"][0]] = str(bv - sv)
        row[names["sell_qty"][0]] = str(sq)
        row[names["buy_qty"][0]] = str(bq)
        row[net_qty_name] = str(bq - sq)
    return row


def _program_group(
    seed: int, k: int, scale: int
) -> dict[tuple[str, str], tuple[int, int, int, int]]:
    """(kind, side) → (매도 수량, 매수 수량, 매도 대금, 매수 대금) — 검산 ④ 가 맞게.
    k = 0 이면 0."""
    base: dict[tuple[str, str], tuple[int, int, int, int]] = {}
    for kind in ("arbt", "nabt"):
        for side in ("entm", "onsl"):
            s = _seed(seed, kind, side)
            sv = _num(s, 0, scale) * k
            bv = _num(s >> 11, 0, scale) * k
            base[(kind, side)] = (sv // 10, bv // 10, sv, bv)
    for kind in ("arbt", "nabt"):
        e, o = base[(kind, "entm")], base[(kind, "onsl")]
        base[(kind, "smtn")] = tuple(a + b for a, b in zip(e, o, strict=True))  # type: ignore[assignment]
    for side in ("entm", "onsl", "smtn"):
        a, n = base[("arbt", side)], base[("nabt", side)]
        base[("whol", side)] = tuple(x + y for x, y in zip(a, n, strict=True))  # type: ignore[assignment]
    return base


def _program_fields(g: dict[tuple[str, str], tuple[int, int, int, int]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for (kind, side), (sq, bq, sv, bv) in g.items():
        out[program_market_field(kind, side, "sell_qty")[0]] = str(sq)
        out[program_market_field(kind, side, "buy_qty")[0]] = str(bq)
        out[program_market_field(kind, side, "net_qty")[0]] = str(bq - sq)
        out[program_market_field(kind, side, "sell_value")[0]] = str(sv)
        out[program_market_field(kind, side, "buy_value")[0]] = str(bv)
        out[program_market_field(kind, side, "net_value")[0]] = str(bv - sv)
    return out


def _weekdays_back(end: str, start: str) -> list[str]:
    d = datetime.strptime(end, "%Y%m%d").replace(tzinfo=KST)
    lo = datetime.strptime(start, "%Y%m%d").replace(tzinfo=KST)
    out: list[str] = []
    while d >= lo:
        if d.weekday() < 5:
            out.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return out


def _hhmmss(k: int) -> str:
    t = F2_OPEN_MIN + max(k, 1) - 1
    return f"{t // 60:02d}{t % 60:02d}00"


def _stock_fields(seed: int, k: int, price: int) -> dict[str, str]:
    sq = _num(seed, 0, 3_000) * k
    bq = _num(seed >> 13, 0, 3_000) * k
    return {
        program_stock_field("sell_qty")[0]: str(sq),
        program_stock_field("buy_qty")[0]: str(bq),
        program_stock_field("net_qty")[0]: str(bq - sq),
        program_stock_field("sell_value")[0]: str(sq * price),
        program_stock_field("buy_value")[0]: str(bq * price),
        program_stock_field("net_value")[0]: str((bq - sq) * price),
    }


INVESTOR_TODAY_CODES: Final = tuple(
    PROGRAM_INVESTOR_CODES
)  # 합성 응답에 싣는 구분 코드(파서 표 순서)


def f2_output(tr_id: str, params: dict[str, str], day: str, hhmm: str = "") -> dict[str, Any]:
    """F2 TR 의 합성 응답(정상). hhmm = 지금 KST 분(장중 누적의 기준 — 비면 마감 뒤)."""
    k = f2_minute_index(hhmm)
    if tr_id == TR_MARKET_MINUTE.tr_id:
        return {**OK_MSG, "output": [investor_minute_row(params, day, k)]}
    market = params.get("FID_MRKT_CLS_CODE", "") or params.get("MRKT_DIV_CLS_CODE", "")
    venue = params.get("FID_COND_MRKT_DIV_CODE", "") or params.get("EXCH_DIV_CLS_CODE", "")
    if tr_id == PROGRAM_TRS["program_market_daily"].tr_id:
        start = params.get("FID_INPUT_DATE_1", day)
        end = params.get("FID_INPUT_DATE_2", day)
        rows = []
        for d in _weekdays_back(end, start):
            seed = _seed("F2-pgm-day", market, venue, d)
            rows.append(
                {
                    "stck_bsop_date": d,
                    "bstp_nmix_prpr": f"{2_000 + _num(seed, 0, 80_000) / 100:.2f}",
                    **_program_fields(_program_group(seed, F2_SESSION_MIN, 900)),
                }
            )
        return {**OK_MSG, "output": rows}
    if tr_id == PROGRAM_TRS["program_market_intraday"].tr_id:
        seed = _seed("F2-pgm-min", market, venue, day)
        rows = [
            {"bsop_hour": _hhmmss(j), **_program_fields(_program_group(seed, j, 900))}
            for j in range(k, max(k - 3, 0), -1)
        ] or [{"bsop_hour": _hhmmss(0), **_program_fields(_program_group(seed, 0, 900))}]
        return {**OK_MSG, "output": rows}
    if tr_id == PROGRAM_TRS["program_investor_today"].tr_id:
        rows = []
        for code in INVESTOR_TODAY_CODES:
            seed = _seed("F2-pgm-inv", market, venue, day, code)
            parts: dict[str, tuple[int, int]] = {}
            for kind in ("arbt", "nabt"):
                s = _seed(seed, kind)
                parts[kind] = (_num(s, 0, 900) * k, _num(s >> 7, 0, 900) * k)
            parts["whol"] = (
                parts["arbt"][0] + parts["nabt"][0],
                parts["arbt"][1] + parts["nabt"][1],
            )
            row: dict[str, Any] = {"invr_cls_code": code}
            for kind, (sv, bv) in parts.items():
                row[program_investor_field(kind, "sell_qty")[0]] = str(sv // 10)
                row[program_investor_field(kind, "buy_qty")[0]] = str(bv // 10)
                row[program_investor_field(kind, "net_qty")[0]] = str(bv // 10 - sv // 10)
                row[program_investor_field(kind, "sell_value")[0]] = str(sv)
                row[program_investor_field(kind, "buy_value")[0]] = str(bv)
                row[program_investor_field(kind, "net_value")[0]] = str(bv - sv)
            rows.append(row)
        return {**OK_MSG, "output": rows}
    code = params.get("FID_INPUT_ISCD", "")
    if tr_id == PROGRAM_TRS["program_stock_intraday"].tr_id:
        seed = _seed("F2-pgm-stock", code, venue, day)
        price = _num(seed >> 21, 5_000, 300_000)
        chg = f"{(_num(seed >> 29, 0, 600) - 300) / 100:.2f}"
        rows = [
            {"bsop_hour": _hhmmss(j), "stck_prpr": str(price), "prdy_ctrt": chg,
             **_stock_fields(seed, j, price)}
            for j in range(k, max(k - 3, 0), -1)
        ]  # fmt: skip
        if not rows:  # 장전 — 누적 0
            rows = [{"bsop_hour": _hhmmss(0), "stck_prpr": str(price), "prdy_ctrt": "0.00",
                     **_stock_fields(seed, 0, price)}]  # fmt: skip
        return {**OK_MSG, "output": rows}
    if tr_id == PROGRAM_TRS["program_stock_daily"].tr_id:
        end = params.get("FID_INPUT_DATE_1", day)
        rows = []
        for d in _days_back(end, 5):
            if datetime.strptime(d, "%Y%m%d").replace(tzinfo=KST).weekday() >= 5:
                continue
            seed = _seed("F2-pgm-stock-day", code, venue, d)
            price = _num(seed >> 21, 5_000, 300_000)
            rows.append(
                {"stck_bsop_date": d, "stck_clpr": str(price),
                 "prdy_ctrt": f"{(_num(seed >> 29, 0, 600) - 300) / 100:.2f}",
                 **_stock_fields(seed, F2_SESSION_MIN, price)}
            )  # fmt: skip
        return {**OK_MSG, "output": rows}
    raise ValueError(f"F2 TR 이 아니다: {tr_id}")


def master_zip() -> bytes:
    """합성 마스터 줄로 만든 배포 zip(안에 `.mst` 한 개, cp949)."""
    lines = json.loads((FIX / "master_lines.json").read_text(encoding="utf-8"))["lines"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("fo_idx_code_mts.mst", ("\n".join(lines) + "\n").encode("cp949"))
    return buf.getvalue()


class FakeKisServer:
    """가짜 KIS REST + 마스터 배포. 시각은 주입한 시계(`now`)로 잰다."""

    def __init__(
        self,
        now: Callable[[], datetime],
        *,
        app_key: str = APP_KEY,
        app_secret: str = APP_SECRET,
        token_life: timedelta = TOKEN_LIFE,
        consensus: ConsensusWorld | None = None,
        quotes: QuoteWorld | None = None,
    ) -> None:
        self._now = now
        self.consensus = consensus  # P4 컨센서스 TR 의 의미값 세계(없으면 고정 시드 합성만)
        self.quotes = quotes  # P8 장중 슬롯 시세 세계(FHKST01010100 — 세계 종목만, 없으면 합성)
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_life = token_life
        self.calls: list[KisCall] = []
        self.issued: list[Issued] = []
        self.master_downloads: list[tuple[datetime, str]] = []
        self._rules: list[_Rule] = []
        self._lock = threading.Lock()

    # ── 조립 ─────────────────────────────────────────────────────────────────────────
    def transport(self, consumer: str) -> httpx.MockTransport:
        """consumer(프로세스 이름 — auth·scheduler·gx·legacy …)가 쓰는 전송."""

        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def master_transport(self, consumer: str) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            if req.method != "GET" or req.url.path != MASTER_PATH:
                return httpx.Response(404, text="not found")
            with self._lock:
                self.master_downloads.append((self._now(), consumer))
            return httpx.Response(200, content=master_zip())

        return httpx.MockTransport(handle)

    def inject(
        self,
        code: str,
        count: int = 1,
        *,
        start: datetime | None = None,
        until: datetime | None = None,
        tr_id: str | None = None,
        consumers: Iterable[str] | None = None,
    ) -> None:
        """다음 해당 요청 count 번에 오류를 준다(`EGW00201`·`EGW00123`·`EGW00133`·`HTTP500`·
        `HTTP429`·`TIMEOUT` — 뒤 둘은 P5 F2: 본문 없는 429·`httpx.ReadTimeout`)."""
        if code not in ERRORS and code not in INJECT_PLAIN:
            raise ValueError(f"모르는 오류 주입: {code}")
        who = frozenset(consumers) if consumers is not None else None
        with self._lock:
            self._rules.append(_Rule(code, count, start, until, tr_id, who))

    # ── 처리 ─────────────────────────────────────────────────────────────────────────
    def _take_rule(self, now: datetime, tr_id: str, consumer: str, *, post: bool) -> str | None:
        for r in self._rules:
            if (r.code == "EGW00133") != post:
                continue
            if r.active(now, tr_id, consumer):
                r.count -= 1
                r.hits += 1
                return r.code
        return None

    def _record(
        self,
        consumer: str,
        req: httpx.Request,
        tr_id: str,
        bearer: str | None,
        status: int,
        msg_cd: str,
    ) -> None:
        params = {k: v for k, v in req.url.params.items()}
        self.calls.append(
            KisCall(
                self._now(),
                consumer,
                req.method,
                req.url.path,
                tr_id,
                params,
                bearer,
                status,
                msg_cd,
            )
        )

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        with self._lock:
            if req.method == "POST":
                return self._post(req, consumer)
            return self._get(req, consumer)

    def _post(self, req: httpx.Request, consumer: str) -> httpx.Response:
        now = self._now()
        path = req.url.path
        kind = {TOKEN_PATH: "token", APPROVAL_PATH: "ws_key"}.get(path)
        if kind is None:
            self._record(consumer, req, "", None, 404, "")
            return httpx.Response(404, json={"error_code": "404", "error_description": "없음"})
        try:
            body: dict[str, Any] = json.loads(req.content or b"{}")
        except ValueError:
            body = {}
        secret = body.get("appsecret") if kind == "token" else body.get("secretkey")
        if body.get("appkey") != self.app_key or secret != self.app_secret:
            self._record(consumer, req, "", None, 403, "EGW00103")
            return httpx.Response(
                403, json={"error_code": "EGW00103", "error_description": "유효하지 않은 AppKey"}
            )
        rule = self._take_rule(now, path, consumer, post=True)
        if rule is not None:
            status, err = ERRORS[rule]
            self._record(consumer, req, "", None, status, rule)
            return httpx.Response(status, json=err)
        n = sum(1 for i in self.issued if i.kind == kind) + 1
        if kind == "token":
            value = f"SIM-ACCESS-{n:03d}"
            expires = now + self.token_life
            self.issued.append(Issued(kind, value, now, expires, consumer))
            self._record(consumer, req, "", None, 200, "")
            expired_kst = expires.astimezone(KST).strftime("%Y-%m-%d %H:%M:%S")
            return httpx.Response(
                200,
                json={
                    "access_token": value,
                    "token_type": "Bearer",
                    "expires_in": int(self.token_life.total_seconds()),
                    "access_token_token_expired": expired_kst,
                },
            )
        value = f"SIM-WS-KEY-{n:03d}"
        # 접속키 만료는 KIS 가 알려 주지 않는다 — 서버 쪽은 넉넉히 24시간 받아 준다
        self.issued.append(Issued(kind, value, now, now + timedelta(hours=24), consumer))
        self._record(consumer, req, "", None, 200, "")
        return httpx.Response(200, json={"approval_key": value})

    def _token_ok(self, token: str | None, now: datetime) -> bool:
        return any(
            i.kind == "token" and i.value == token and i.at <= now < i.expires_at
            for i in self.issued
        )

    def _get(self, req: httpx.Request, consumer: str) -> httpx.Response:
        now = self._now()
        tr_id = req.headers.get("tr_id", "")
        auth = req.headers.get("authorization", "")
        bearer = auth[len("Bearer ") :] if auth.startswith("Bearer ") else None

        def reply(status: int, body: dict[str, Any], msg_cd: str = "") -> httpx.Response:
            self._record(consumer, req, tr_id, bearer, status, msg_cd)
            return httpx.Response(status, json=body)

        if req.headers.get("appkey") != self.app_key or (
            req.headers.get("appsecret") != self.app_secret
        ):
            return reply(500, {"rt_cd": "1", "msg_cd": "EGW00103", "msg1": "유효하지 않은 AppKey"})
        if TRS.get(tr_id) != req.url.path:
            return reply(500, {"rt_cd": "1", "msg_cd": "OPSQ0002", "msg1": "없는 서비스 코드"})
        rule = self._take_rule(now, tr_id, consumer, post=False)
        if rule == "HTTP500":
            self._record(consumer, req, tr_id, bearer, 500, "HTTP500")
            return httpx.Response(500, text="Internal Server Error")
        if rule == "HTTP429":  # P5 F2 — 게이트웨이 한도(본문 없음)
            self._record(consumer, req, tr_id, bearer, 429, "HTTP429")
            return httpx.Response(429, text="Too Many Requests")
        if rule == "TIMEOUT":  # P5 F2 — 응답 없음(호출 쪽 시간 초과)
            self._record(consumer, req, tr_id, bearer, 0, "TIMEOUT")
            raise httpx.ReadTimeout("가짜 KIS 응답 시간 초과", request=req)
        if rule is not None:
            status, err = ERRORS[rule]
            return reply(status, err, rule)
        if not self._token_ok(bearer, now):
            status, err = ERRORS["EGW00123"]
            return reply(status, err, "EGW00123")
        local = now.astimezone(KST)
        day = local.strftime("%Y%m%d")
        # 장중 TR 은 10분 슬롯마다 값이 바뀐다
        slot = f"{local:%H}{local.minute - local.minute % 10:02d}"
        params = {k: v for k, v in req.url.params.items()}
        if tr_id in F2_TR_IDS:  # P5 F2 — 1분마다 누적이 늘어난다(지금 분 기준)
            return reply(200, f2_output(tr_id, params, day, f"{local:%H%M}"))
        if tr_id in CONSENSUS_TR_IDS:
            return reply(200, consensus_output(tr_id, params, day, self.consensus))
        if tr_id == "FHKST01010100" and self.quotes is not None:  # P8 — 세계의 슬롯 시세
            hit = world_slot(self.quotes, params.get("FID_INPUT_ISCD", ""), local)
            if hit is not None:
                return reply(200, {**OK_MSG, "output": slot_quote_body(hit)})
        return reply(200, synthetic_output(tr_id, params, day, slot))

    # ── 조회(시험) ───────────────────────────────────────────────────────────────────
    def posts(self, path: str | None = None) -> list[KisCall]:
        with self._lock:
            return [c for c in self.calls if c.method == "POST" and path in (None, c.path)]

    @property
    def token_posts(self) -> int:
        """접근토큰 발급 요청 수(성공·실패 모두)."""
        return len(self.posts(TOKEN_PATH))

    @property
    def approval_posts(self) -> int:
        return len(self.posts(APPROVAL_PATH))

    def tokens(self) -> list[Issued]:
        with self._lock:
            return [i for i in self.issued if i.kind == "token"]

    def approval_keys(self) -> list[Issued]:
        with self._lock:
            return [i for i in self.issued if i.kind == "ws_key"]

    def gets(self, consumer: str | None = None) -> list[KisCall]:
        with self._lock:
            return [c for c in self.calls if c.method == "GET" and consumer in (None, c.consumer)]

    @property
    def bearers(self) -> list[str | None]:
        return [c.bearer for c in self.gets()]

    def valid_approval_key(self, key: str) -> bool:
        now = self._now()
        return any(
            i.kind == "ws_key" and i.value == key and i.at <= now < i.expires_at
            for i in self.approval_keys()
        )

    def max_in_window(self, seconds: float = 1.0) -> int:
        """어떤 반열린 창 [t, t+seconds) 에 든 요청(발급 포함) 수의 최댓값."""
        with self._lock:
            times = sorted(c.at for c in self.calls)
        best, j = 0, 0
        width = timedelta(seconds=seconds)
        for i, t in enumerate(times):
            while times[j] <= t - width:
                j += 1
            best = max(best, i - j + 1)
        return best


def make_client(
    server: FakeKisServer,
    creds: KisCredentials,
    redis: Redis,
    *,
    consumer: str,
    clock: Clock,
    now: Callable[[], datetime],
    by: str | None = None,
) -> KisRestClient:
    """읽기 전용 토큰(reader) + 앱키 리미터(`rl:kis:<해시>`)를 끼운 KIS 클라이언트.

    발급하지 않는다(발급자는 auth 에만 — ADR 0004)."""
    limiter = RedisRateLimiter.scoped(redis, "kis", creds.app_key.get_secret_value(), clock=clock)
    provider = reader(redis, creds, now=now, by=by or consumer)
    return KisRestClient(creds, provider, limiter, transport=server.transport(consumer))


def fake_credentials() -> KisCredentials:
    return KisCredentials(app_key=SecretStr(APP_KEY), app_secret=SecretStr(APP_SECRET), env="real")


__all__ = [
    "APP_KEY",
    "APP_SECRET",
    "CONSENSUS_TR_IDS",
    "MASTER_PATH",
    "RANK_ROWS",
    "SECTORS_PER_MARKET",
    "SYMBOLS",
    "SYN_BROKERS",
    "TRS",
    "ConsensusWorld",
    "FakeKisServer",
    "Issued",
    "KisCall",
    "consensus_output",
    "estimate_body",
    "fake_credentials",
    "make_client",
    "market_symbols",
    "master_zip",
    "opinion_body",
    "synthetic_consensus",
    "synthetic_opinions",
    "synthetic_output",
]
