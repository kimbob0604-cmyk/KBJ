"""가짜 KIS REST 서버 — `httpx.MockTransport` 라우터 (poller·클라이언트 테스트용).

실제 KIS 를 부르지 않는다. 응답은 커밋된 fixture(`tests/fixtures/kis/*.json` — KBJ P1 에서
probe 실측 발췌를 같은 형태의 합성 데이터로 바꿨다, `scripts/make_synthetic_fixtures.py`)다.

- 라우팅: 경로 + `tr_id` 헤더 + 파라미터 (경로·TR 은 `scripts/probe_common.py` 실측값)
  - 전광판 콜/풋(FHPIF05030100): `FID_COND_MRKT_CLS_CODE`·`FID_MTRT_CNT` 로 만기별. 시장구분이
    `O` 가 아니면 `OPSQ2001`(#19 주간 대조군: `EU`·`CM` 거절)
  - 선물 전광판(FHPIF05030200): 시장구분 `F` 만. 행은 체인의 `futures` 순서(없으면 기본 2종목)로,
    fixture 행을 틀로 코드·이름·잔존일수만 바꾼다(기본 구성은 fixture 원문과 같다).
    `futures_price` 로 첫 행 가격을 바꾼다
  - 기초자산(display-board-top, FHPIF05030000): 응답 필드 미실측 — 빈 output 으로 정상 응답만 한다
  - 월물리스트: `FID_COND_MRKT_CLS_CODE` 별 fixture. 체인에 `listed` 를 주면 그 목록(다른 날짜
    구성 — 예: 분기 만기일 2026-12-10)
  - 투자자별(FHPTJ04030000): `FID_INPUT_ISCD`/`FID_INPUT_ISCD_2` 7조합 fixture
  - 단건 현재가(FHMIF10000000): 코드별. 시장구분 F·O·CM·EU 를 받는다(#19)
  - 분봉(FHKIF03020200): `add_minute_bars` 로 넣은 봉(KST 분 시작 시각)을 입력 시각부터 과거로
    `minute_page`(102)개, 최신부터(fixture 순서). 세션·날짜 경계를 넘어 앞 봉도 준다(F 는 휴장일
    입력에도 앞 거래일 봉이 온다 — probe `has_bars_near`). 입력 시각은 분으로 올림해 그 분 봉까지
    포함한다 — (D, 235959) 에 D 밤 24:00 봉이 온 관측(설계 §6). 야간 `CM` 봉은 야간 시작일 날짜 +
    18~30시 표기. `CM` 입력 (D, HHMMSS) 는 18~30시 확장 표기를 D 자정부터 잰 시각으로 읽는다 —
    (D, 300000) → D 밤 30:00 봉부터, (D, 240400) → 자정을 넘어 23:xx 로 이어짐(2026-09-29 실측
    #17b, services/scheduler/minute.py 가 쓰는 입력). 18시 미만 입력은 옛 관측대로 입력 날짜
    X(비거래일이면 앞 거래일)의 전 거래일 밤 달력 D+1 시각으로 읽지만, 실제 KIS 는 (09-29, 055959)
    에 09-28 밤이 아니라 09-22 밤을 줬다(#17b) — 적재는 이 입력을 쓰지 않는다. 입력 (날짜, 시각)이
    가짜 시계보다 뒤면 `FAKEFUTR` 오류
- `FakeChain` 을 주면 시리즈별 전 행사가를 fixture 행을 틀로 만든다. 전광판은 KIS 처럼 행사가
  내림차순 상위 100행(#11 — 월물은 ATM 구간이 빠진다), 단건은 코드별 한 행(최종거래일 포함).
  체인 밖 요청은 fixture 원문(전광판 WKM 260904·월물 202610 발췌, 단건 6코드)이다
- 오류 주입(`inject`): 한도초과 `EGW00201`, HTTP 500(본문 JSON 아님), 토큰 거절 `EGW00123`,
  정상 본문을 고친 malformed
- 호출마다 가짜 시계 시각·경로·tr_id·파라미터·상태를 `calls` 에 남긴다
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import SecretStr

from config.settings import Settings
from core.calendar import KST, TradingCalendar
from data.kis.auth_client import RedisTokenCache, TokenRecord, token_owner
from data.kis.master import MasterRow, parse_master
from data.kis.ratelimit import RateLimiter
from data.kis.rest import KisClient
from data.store import MinuteBarRecord, StoreError
from scripts.probe_common import (
    P_CALLPUT,
    P_FUT_BOARD,
    P_INVESTOR,
    P_MINUTE,
    P_OPTION_LIST,
    P_PRICE,
    P_TOP,
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_MINUTE,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
)
from services.auth.service import TOKEN_KEY
from services.poller.records import QuarantineRecord
from services.recorder.envelope import RawEnvelope

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "kis"
# 발급 경로 — KBJ P2: 발급자는 kbj/services/auth/issuer.py 에만 있고 legacy GX 코드에는 이 상수가
# 없다. 가짜 서버는 옛 클라이언트·시험이 보낼 수 있는 발급 요청을 그대로 받아 센다(token_posts).
TOKEN_PATH = "/oauth2/" + "tokenP"  # 경로일 뿐 비밀 아님
US = 1_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FAKE_TOKEN = "fake-access-token-for-tests"
CACHED_TOKEN = "cached-token-from-auth"  # seed_cached_token 이 Redis 에 두는 토큰

OK_MSG = {"rt_cd": "0", "msg_cd": "MCA00000", "msg1": "정상처리 되었습니다."}
RATE_LIMIT_BODY = {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}
TOKEN_EXPIRED_BODY = {"rt_cd": "1", "msg_cd": "EGW00123", "msg1": "기간이 만료된 token 입니다."}
PRICE_MARKETS = frozenset({"F", "O", "CM", "EU"})  # 단건 현재가가 받는 시장구분 (#19)


@cache
def _load(name: str) -> dict[str, Any]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def load_fixture(name: str) -> dict[str, Any]:
    """fixture 사본 (`_source` 설명은 뺀다 — KIS 응답에는 없다)."""
    body = copy.deepcopy(_load(name))
    body.pop("_source", None)
    return body


def _err(msg_cd: str, msg1: str) -> dict[str, Any]:
    return {"rt_cd": "1", "msg_cd": msg_cd, "msg1": msg1}


# ── 가짜 시계 ────────────────────────────────────────────────────────────────


class FakeClock:
    """레이트리미터 `Clock`(now_us·sleep) + aware datetime. sleep 은 시각만 옮긴다."""

    def __init__(self, start: datetime) -> None:
        self.t = 0
        self.set(start)

    def set(self, when: datetime) -> None:
        if when.tzinfo is None:
            raise ValueError("naive datetime 금지")
        self.t = (when - EPOCH) // timedelta(microseconds=1)

    def now_us(self) -> int:
        return self.t

    def sleep(self, seconds: float, /) -> None:
        if seconds > 0:
            self.t += round(seconds * US)

    def now(self) -> datetime:
        return EPOCH + timedelta(microseconds=self.t)


# ── 가짜 체인 (마스터·전광판·단건이 같은 행사가·코드를 쓴다) ─────────────────

_CLS_TAG = {"": "0", "WKM": "M", "WKI": "I"}
_KIND = {
    ("", "C"): "5",
    ("", "P"): "6",
    ("WKI", "C"): "L",
    ("WKI", "P"): "M",
    ("WKM", "C"): "N",
    ("WKM", "P"): "O",
}
# 마스터 종목명 표기 (data/kis/master.py)
_NAME_PREFIX = {"": "", "WKI": "위클리", "WKM": "위클리M "}


@dataclass(frozen=True)
class FakeSeries:
    """옵션 한 시리즈. cls 는 월물리스트·전광판 시장분류('' 월물, WKM, WKI), mtrt 는 6자리."""

    cls: str
    mtrt: str
    last_tr_date: date
    lo: Decimal
    hi: Decimal
    step: Decimal = Decimal("2.5")

    @property
    def strikes(self) -> tuple[Decimal, ...]:
        n = int((self.hi - self.lo) / self.step)
        return tuple(self.lo + self.step * i for i in range(n + 1))

    @property
    def token(self) -> str:
        """마스터 종목명의 만기 표기 (월물 `202610`, 위클리 `2609W4`)."""
        return self.mtrt if self.cls == "" else f"{self.mtrt[:4]}W{int(self.mtrt[4:])}"

    def name(self, cp: str, strike: Decimal) -> str:
        return f"{_NAME_PREFIX[self.cls]}{cp} {self.token} {strike:,.1f}"

    def code(self, cp: str, strike: Decimal) -> str:
        """가짜 단축코드 — 시리즈·콜풋·행사가마다 다르다."""
        i = self.strikes.index(strike)
        return f"{'B' if cp == 'C' else 'C'}{_CLS_TAG[self.cls]}{self.mtrt}{i:03d}"

    def master_line(self, cp: str, strike: Decimal) -> str:
        code = self.code(cp, strike)
        kind = _KIND[(self.cls, cp)]
        return f"{kind}|{code}|KR4{code}|{self.name(cp, strike)}|3|{strike:08.2f}| |2001|KOSPI200"


@dataclass(frozen=True)
class FakeFutures:
    code: str
    name: str
    last_tr_date: date
    remaining_days: int


DEFAULT_FUTURES = (
    FakeFutures("A01612", "F 202612", date(2026, 12, 10), 74),  # fixture futures_board 첫 행
    FakeFutures("A01703", "F 202703", date(2027, 3, 11), 165),
)


@dataclass
class FakeChain:
    series: tuple[FakeSeries, ...]
    board_rows: int = 100  # 전광판 콜·풋 각 행 수 상한 (#11)
    # 전광판 atm_cls_name 이 'ATM' 인 행 — 선물가 기준 ATM 과 다르다(#11)
    display_atm: Decimal = Decimal("1125.0")
    futures: tuple[FakeFutures, ...] = DEFAULT_FUTURES
    # 월물리스트 시장분류('' WKM WKI) → 6자리 만기 목록. None 이면 fixture(2026-09-28 13:33)
    listed: Mapping[str, tuple[str, ...]] | None = None
    _by_code: dict[str, tuple[FakeSeries, str, Decimal]] = field(
        default_factory=dict[str, tuple[FakeSeries, str, Decimal]], repr=False
    )

    def __post_init__(self) -> None:
        for s in self.series:
            for k in s.strikes:
                for cp in ("C", "P"):
                    self._by_code[s.code(cp, k)] = (s, cp, k)

    def find(self, cls: str, mtrt: str) -> FakeSeries | None:
        return next((s for s in self.series if (s.cls, s.mtrt) == (cls, mtrt)), None)

    def lookup(self, code: str) -> tuple[FakeSeries, str, Decimal] | None:
        return self._by_code.get(code)

    def master_lines(self) -> list[str]:
        out = [
            f"1|{f.code}|KR4{f.code}|{f.name}| |00000.00|{i}|2001|KOSPI200"
            for i, f in enumerate(self.futures, start=1)
        ]
        for s in self.series:
            out += [s.master_line(cp, k) for k in s.strikes for cp in ("C", "P")]
        return out

    def master_rows(self) -> list[MasterRow]:
        return parse_master("\n".join(self.master_lines()))


def default_chain() -> FakeChain:
    """2026-09-28 상장 구성 (#11a·#12b·#19 2회차: 최종거래일 09-28·10-01·10-06·10-08).

    행사가 범위는 실측(2609W4 970.0~1245.0 111개, 202610 745.0~1595.0 341개)을 따르고, 나머지
    위클리는 같은 범위로 둔다. 202611 은 계산값(11월 둘째 목요일).
    """
    wk_lo, wk_hi = Decimal("970.0"), Decimal("1245.0")
    m_lo, m_hi = Decimal("745.0"), Decimal("1595.0")
    return FakeChain(
        series=(
            FakeSeries("WKM", "260904", date(2026, 9, 28), wk_lo, wk_hi),
            FakeSeries("WKI", "261001", date(2026, 10, 1), wk_lo, wk_hi),
            FakeSeries("WKM", "261001", date(2026, 10, 6), wk_lo, wk_hi),
            FakeSeries("", "202610", date(2026, 10, 8), m_lo, m_hi),
            FakeSeries("", "202611", date(2026, 11, 12), m_lo, m_hi),
        )
    )


def quarterly_expiry_chain() -> FakeChain:
    """SYNTHETIC 2026-12-10(목, 12월 둘째 목요일) 분기 만기일 구성 — 선물 12월물·월물 202612 가
    그날 15:20 에 만기다. 최종거래일은 캘린더 계산과 같게 둔다(WKM 2612W2 = 12-14 월, WKI 2612W3 =
    12-17 목, 202701 = 2027-01-14). 월물 만기 주라 목요일 위클리는 다음 주 것만 있다.

    선물 가격은 fixture 행 그대로(12월물 1095.10·3월물 1085.00 — ATM 4행사가 차이). 만기일
    전광판 잔존일수(12월물 1)는 미실측 가정이다.
    """
    wk_lo, wk_hi = Decimal("970.0"), Decimal("1245.0")
    m_lo, m_hi = Decimal("745.0"), Decimal("1595.0")
    return FakeChain(
        series=(
            FakeSeries("", "202612", date(2026, 12, 10), m_lo, m_hi),
            FakeSeries("WKM", "261202", date(2026, 12, 14), wk_lo, wk_hi),
            FakeSeries("WKI", "261203", date(2026, 12, 17), wk_lo, wk_hi),
            FakeSeries("", "202701", date(2027, 1, 14), m_lo, m_hi),
        ),
        futures=(
            FakeFutures("A01612", "F 202612", date(2026, 12, 10), 1),
            FakeFutures("A01703", "F 202703", date(2027, 3, 11), 91),
        ),
        listed={
            "": ("202612", "202701", "202702", "202703"),
            "WKM": ("261202",),
            "WKI": ("261203",),
        },
    )


# ── 가짜 분봉 ────────────────────────────────────────────────────────────────

MINUTE_PAGE = 102  # 한 번에 최대 102봉 (#17)


@dataclass(frozen=True)
class FakeBar:
    """분봉 하나 — at 은 분 시작 시각(KST aware)."""

    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    cum_value: int

    def night_start(self) -> date:
        """야간 봉이 속한 밤의 시작일 (18시 이후면 그날, 아니면 전날)."""
        k = self.at.astimezone(KST)
        return k.date() if k.time() >= time(18, 0) else k.date() - timedelta(days=1)

    def row(self, market: str) -> dict[str, str]:
        """KIS output2 한 행 — `CM` 은 야간 시작일 + 18~30시 표기."""
        k = self.at.astimezone(KST)
        if market == "CM":
            start = self.night_start()
            off = k - datetime.combine(start, time(0), tzinfo=KST)
            secs = int(off.total_seconds())
            ymd, hms = start, f"{secs // 3600:02d}{secs % 3600 // 60:02d}{secs % 60:02d}"
        else:
            ymd, hms = k.date(), k.strftime("%H%M%S")
        return {
            "stck_bsop_date": ymd.strftime("%Y%m%d"),
            "stck_cntg_hour": hms,
            "futs_prpr": f"{self.close:.2f}",
            "futs_oprc": f"{self.open:.2f}",
            "futs_hgpr": f"{self.high:.2f}",
            "futs_lwpr": f"{self.low:.2f}",
            "cntg_vol": str(self.volume),
            "acml_tr_pbmn": str(self.cum_value),
        }


def _kst(d: date, h: int, m: int = 0) -> datetime:
    return datetime.combine(d, time(0), tzinfo=KST) + timedelta(hours=h, minutes=m)


def minutes(start: datetime, end: datetime) -> list[datetime]:
    """start 부터 end 까지(양끝 포함) 1분 간격."""
    n = int((end - start).total_seconds() // 60)
    return [start + timedelta(minutes=i) for i in range(n + 1)]


def day_bar_times(d: date) -> list[datetime]:
    """주간 D 의 봉 시각 — 08:45~15:34 매분 + 종가 단일가 15:45 (411봉 — 실측 13:54~15:45 102봉 =
    112분처럼 15:35~15:44 는 봉이 없다)."""
    return [*minutes(_kst(d, 8, 45), _kst(d, 15, 34)), _kst(d, 15, 45)]


def night_bar_times(d: date, *, first: tuple[int, int] = (18, 0)) -> list[datetime]:
    """D 에 시작한 밤의 봉 시각 — first(기본 18:00)부터 30:00(D+1 06:00)까지 매분."""
    return minutes(_kst(d, *first), _kst(d, 30))


# ── 서버 ────────────────────────────────────────────────────────────────────

FaultKind = Literal["rate_limit", "http500", "token", "malformed"]


@dataclass
class Fault:
    """주입할 오류 하나. 조건(tr_id·파라미터·시각)에 맞는 요청 `times` 건에 적용된다."""

    kind: FaultKind
    tr_id: str | None = None
    params: Mapping[str, str] = field(default_factory=dict[str, str])
    times: int = 1
    mutate: Callable[[dict[str, Any]], None] | None = None  # malformed: 정상 본문을 고친다
    after_us: int | None = None
    used: int = 0

    def matches(self, tr_id: str, params: Mapping[str, str], t_us: int) -> bool:
        if self.used >= self.times:
            return False
        if self.tr_id is not None and self.tr_id != tr_id:
            return False
        if self.after_us is not None and t_us < self.after_us:
            return False
        return all(params.get(k) == v for k, v in self.params.items())


@dataclass(frozen=True)
class Call:
    t_us: int
    path: str
    tr_id: str
    params: dict[str, str]
    status: int
    msg_cd: str
    fault: FaultKind | None


class FakeKisServer:
    def __init__(
        self,
        clock: FakeClock,
        chain: FakeChain | None = None,
        calendar: TradingCalendar | None = None,
    ) -> None:
        self.clock = clock
        self.chain = chain
        self.calendar = calendar or TradingCalendar.default()
        self.minute_bars: dict[tuple[str, str], list[FakeBar]] = {}
        self.minute_page = MINUTE_PAGE
        self.calls: list[Call] = []
        self.faults: list[Fault] = []
        self.token_posts = 0
        self.bearers: list[str] = []  # 조회마다 받은 authorization 헤더 (토큰 발급 제외)
        self.futures_price: Decimal | None = None  # None 이면 fixture 값(1095.10)
        board = _load("callput_wkm_260904.json")
        self._call_tpl: dict[str, Any] = board["output1"][0]
        self._put_tpl: dict[str, Any] = board["output2"][0]
        items: list[dict[str, Any]] = _load("price_options.json")["items"]
        self._price_fix: dict[str, dict[str, Any]] = {it["code"]: it["body"] for it in items}
        self._price_tpl = {
            "C": next(it["body"]["output1"] for it in items if it["series"].startswith("C")),
            "P": next(it["body"]["output1"] for it in items if it["series"].startswith("P")),
        }

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def inject(
        self,
        kind: FaultKind,
        *,
        tr_id: str | None = None,
        params: Mapping[str, str] | None = None,
        times: int = 1,
        mutate: Callable[[dict[str, Any]], None] | None = None,
        after: datetime | None = None,
    ) -> Fault:
        f = Fault(
            kind,
            tr_id=tr_id,
            params=dict(params or {}),
            times=times,
            mutate=mutate,
            after_us=None if after is None else (after - EPOCH) // timedelta(microseconds=1),
        )
        self.faults.append(f)
        return f

    def add_minute_bars(
        self,
        code: str,
        market: str,
        times: Iterable[datetime],
        *,
        base: Decimal = Decimal("1100.00"),
    ) -> list[FakeBar]:
        """분봉을 넣는다(같은 시각은 새 값으로). 가격은 시각마다 조금씩 다르게."""
        book = {b.at: b for b in self.minute_bars.get((code, market), [])}
        added: list[FakeBar] = []
        for at in times:
            if at.tzinfo is None:
                raise ValueError("naive datetime 금지")
            i = int(at.timestamp() // 60)
            close = base + Decimal(i % 13) * Decimal("0.05")
            bar = FakeBar(
                at=at,
                open=close - Decimal("0.10"),
                high=close + Decimal("0.20"),
                low=close - Decimal("0.25"),
                close=close,
                volume=100 + i % 50,
                cum_value=1_000_000 + i % 1000,
            )
            book[at] = bar
            added.append(bar)
        self.minute_bars[(code, market)] = sorted(book.values(), key=lambda b: b.at)
        return added

    def minute_calls(self) -> list[tuple[str, str, str, str]]:
        """분봉 조회 입력 (시장, 코드, 날짜, 시각) — 부른 순서대로."""
        return [
            (
                c.params.get("FID_COND_MRKT_DIV_CODE", ""),
                c.params.get("FID_INPUT_ISCD", ""),
                c.params.get("FID_INPUT_DATE_1", ""),
                c.params.get("FID_INPUT_HOUR_1", ""),
            )
            for c in self.calls
            if c.tr_id == TR_MINUTE
        ]

    # ── 기록 조회 ──

    def quote_calls(self, tr_id: str | None = None, **params: str) -> list[Call]:
        return [
            c
            for c in self.calls
            if (tr_id is None or c.tr_id == tr_id)
            and all(c.params.get(k) == v for k, v in params.items())
        ]

    def max_in_window(self, window_s: float = 1.0) -> int:
        """어떤 반열린 창 [t, t + window) 에 든 호출 수의 최댓값."""
        ts = sorted(c.t_us for c in self.calls)
        w = round(window_s * US)
        best, j = 0, 0
        for i, t in enumerate(ts):
            while ts[j] <= t - w:
                j += 1
            best = max(best, i - j + 1)
        return best

    # ── 처리 ──

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == TOKEN_PATH:
            self.token_posts += 1
            return httpx.Response(
                200,
                json={
                    "access_token": FAKE_TOKEN,
                    "token_type": "Bearer",
                    "expires_in": 86400,
                    "access_token_token_expired": "2099-01-01 00:00:00",
                },
            )
        tr_id = request.headers.get("tr_id", "")
        params = dict(request.url.params)
        self.bearers.append(request.headers.get("authorization", ""))
        t = self.clock.now_us()
        fault = next((f for f in self.faults if f.matches(tr_id, params, t)), None)
        if fault is not None:
            fault.used += 1
        resp = self._respond(request.url.path, tr_id, params, fault)
        msg_cd = ""
        if resp.headers.get("content-type", "").startswith("application/json"):
            msg_cd = str(json.loads(resp.content).get("msg_cd", ""))
        self.calls.append(
            Call(
                t,
                request.url.path,
                tr_id,
                params,
                resp.status_code,
                msg_cd,
                None if fault is None else fault.kind,
            )
        )
        return resp

    def _respond(
        self, path: str, tr_id: str, params: dict[str, str], fault: Fault | None
    ) -> httpx.Response:
        if fault is not None and fault.kind == "rate_limit":
            return httpx.Response(500, json=RATE_LIMIT_BODY)
        if fault is not None and fault.kind == "token":
            return httpx.Response(500, json=TOKEN_EXPIRED_BODY)
        if fault is not None and fault.kind == "http500":
            return httpx.Response(500, text="Internal Server Error")
        status, body = self._route(path, tr_id, params)
        if fault is not None and fault.mutate is not None:
            fault.mutate(body)
        return httpx.Response(status, json=body)

    def _route(self, path: str, tr_id: str, params: dict[str, str]) -> tuple[int, dict[str, Any]]:
        routes: dict[str, tuple[str, Callable[[dict[str, str]], dict[str, Any]]]] = {
            P_CALLPUT: (TR_CALLPUT, self._callput),
            P_FUT_BOARD: (TR_FUT_BOARD, self._futures_board),
            P_TOP: (TR_TOP, self._top),
            P_OPTION_LIST: (TR_OPTION_LIST, self._option_list),
            P_INVESTOR: (TR_INVESTOR, self._investor),
            P_PRICE: (TR_PRICE, self._price),
            P_MINUTE: (TR_MINUTE, self._minute),
        }
        route = routes.get(path)
        if route is None:
            return 404, _err("FAKE404", f"(fake) 모르는 경로 {path}")
        want_tr, fn = route
        if tr_id != want_tr:
            return 200, _err("FAKETRID", f"(fake) 경로와 tr_id 가 다르다: {tr_id}")
        return 200, fn(params)

    def _callput(self, p: dict[str, str]) -> dict[str, Any]:
        if p.get("FID_COND_MRKT_DIV_CODE") != "O":
            return _err("OPSQ2001", "INVALID FID_COND_MRKT_DIV_CODE")
        cls, mtrt = p.get("FID_COND_MRKT_CLS_CODE", ""), p.get("FID_MTRT_CNT", "")
        s = self.chain.find(cls, mtrt) if self.chain is not None else None
        if s is not None:
            return self._board_body(s)
        name = {("WKM", "260904"): "callput_wkm_260904.json", ("", "202610"): "callput_202610.json"}
        fix = name.get((cls, mtrt))
        if fix is not None:
            return OK_MSG | load_fixture(fix)
        return OK_MSG | {"output1": [], "output2": []}  # 모르는 만기는 0행 (#12 4자리 코드처럼)

    def _board_body(self, s: FakeSeries) -> dict[str, Any]:
        assert self.chain is not None
        ks = sorted(s.strikes, reverse=True)[: self.chain.board_rows]
        return OK_MSG | {
            "output1": [self._board_row(self._call_tpl, s, "C", k) for k in ks],
            "output2": [self._board_row(self._put_tpl, s, "P", k) for k in ks],
        }

    def _board_row(self, tpl: dict[str, Any], s: FakeSeries, cp: str, k: Decimal) -> dict[str, Any]:
        assert self.chain is not None
        return tpl | {
            "acpr": f"{k:.2f}",
            "optn_shrn_iscd": s.code(cp, k),
            "atm_cls_name": "ATM" if k == self.chain.display_atm else "OTM",
        }

    def _futures_board(self, p: dict[str, str]) -> dict[str, Any]:
        if p.get("FID_COND_MRKT_DIV_CODE") != "F":
            return _err("OPSQ2001", "INVALID FID_COND_MRKT_DIV_CODE")
        body = load_fixture("futures_board.json")
        tpl: list[dict[str, Any]] = body["output"]
        futs = self.chain.futures if self.chain is not None else DEFAULT_FUTURES
        body["output"] = [
            next((r for r in tpl if r["futs_shrn_iscd"] == f.code), tpl[-1])
            | {
                "futs_shrn_iscd": f.code,
                "hts_kor_isnm": f.name,
                "hts_rmnn_dynu": str(f.remaining_days),
            }
            for f in futs
        ]
        if self.futures_price is not None:
            body["output"][0]["futs_prpr"] = f"{self.futures_price:.2f}"
        return OK_MSG | body

    def _top(self, p: dict[str, str]) -> dict[str, Any]:
        # 응답 필드 미실측 — 정상 응답 모양만 흉내 낸다
        return OK_MSG | {"output1": {}, "output2": []}

    def _option_list(self, p: dict[str, str]) -> dict[str, Any]:
        cls = p.get("FID_COND_MRKT_CLS_CODE", "")
        if self.chain is not None and self.chain.listed is not None:
            mtrts = self.chain.listed.get(cls, ())
            return OK_MSG | {"output": [{"mtrt_yymm_code": m[-4:], "mtrt_yymm": m} for m in mtrts]}
        by_class: dict[str, Any] = _load("option_list.json")["by_class"]
        got = by_class.get(cls or "(blank)")
        rows: list[dict[str, str]] = copy.deepcopy(got["output"]) if got else []
        return OK_MSG | {"output": rows}

    def _investor(self, p: dict[str, str]) -> dict[str, Any]:
        pairs: dict[str, Any] = _load("investor.json")["pairs"]
        got = pairs.get(f"{p.get('FID_INPUT_ISCD', '')}/{p.get('FID_INPUT_ISCD_2', '')}")
        if got is None:
            return _err("OPSQ0002", "(fake) 모르는 시장·업종 조합")
        return OK_MSG | copy.deepcopy(got)

    def _futures_row(self, f: FakeFutures) -> dict[str, Any]:
        rows: list[dict[str, Any]] = _load("futures_board.json")["output"]
        row = next((r for r in rows if r["futs_shrn_iscd"] == f.code), rows[-1])
        price = row["futs_prpr"]
        if self.futures_price is not None and f.code == rows[0]["futs_shrn_iscd"]:
            price = f"{self.futures_price:.2f}"
        return {
            "hts_kor_isnm": f.name,
            "futs_prpr": price,
            "acml_vol": row["acml_vol"],
            "hts_otst_stpl_qty": row["hts_otst_stpl_qty"],
            "futs_last_tr_date": f.last_tr_date.strftime("%Y%m%d"),
            "hts_rmnn_dynu": str(f.remaining_days),
            "acpr": "0.00",
        }

    def _price(self, p: dict[str, str]) -> dict[str, Any]:
        if p.get("FID_COND_MRKT_DIV_CODE") not in PRICE_MARKETS:
            return _err("OPSQ2001", "INVALID FID_COND_MRKT_DIV_CODE")
        code = p.get("FID_INPUT_ISCD", "")
        futs = self.chain.futures if self.chain is not None else DEFAULT_FUTURES
        f = next((x for x in futs if x.code == code), None)
        if f is not None:
            return OK_MSG | {"output1": self._futures_row(f)}
        hit = self.chain.lookup(code) if self.chain is not None else None
        if hit is not None:
            s, cp, k = hit
            out = self._price_tpl[cp] | {
                "hts_kor_isnm": s.name(cp, k),
                "acpr": f"{k:.2f}",
                "futs_last_tr_date": s.last_tr_date.strftime("%Y%m%d"),
            }
            return OK_MSG | {"output1": out}
        fix = self._price_fix.get(code)
        if fix is not None:
            return OK_MSG | copy.deepcopy(fix)
        return _err("OPSQ0002", "(fake) 모르는 종목코드")

    def _minute(self, p: dict[str, str]) -> dict[str, Any]:
        market, code = p.get("FID_COND_MRKT_DIV_CODE", ""), p.get("FID_INPUT_ISCD", "")
        if market not in ("F", "CM"):
            return _err("OPSQ2001", "INVALID FID_COND_MRKT_DIV_CODE")
        ymd, hms = p.get("FID_INPUT_DATE_1", ""), p.get("FID_INPUT_HOUR_1", "")
        if len(ymd) != 8 or len(hms) != 6 or not (ymd + hms).isdigit():
            return _err("OPSQ0002", "(fake) 날짜·시각 형식 오류")
        day = date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:]))
        h, m, sec = int(hms[:2]), int(hms[2:4]), int(hms[4:])
        literal = datetime.combine(day, time(0), tzinfo=KST) + timedelta(
            hours=h, minutes=m, seconds=sec
        )
        if literal > self.clock.now():
            return _err("FAKEFUTR", "(fake) 미래 날짜·시각")
        ceil = timedelta(hours=h, minutes=m + (1 if sec else 0))
        base = day
        if market == "CM" and h < 18:  # 그날(비거래일이면 앞 거래일)의 전 거래일 밤, 달력 D+1
            cal = self.calendar
            x = day if cal.is_trading_day(day) else cal.prev_trading_day(day)
            base = cal.prev_trading_day(x) + timedelta(days=1)
        cutoff = datetime.combine(base, time(0), tzinfo=KST) + ceil
        bars = [b for b in self.minute_bars.get((code, market), []) if b.at <= cutoff]
        page = bars[-self.minute_page :] if self.minute_page > 0 else []
        out1 = load_fixture("minute_day.json")["output1"] | {"futs_shrn_iscd": code}
        return OK_MSG | {"output1": out1, "output2": [b.row(market) for b in reversed(page)]}


# ── 클라이언트 조립 ─────────────────────────────────────────────────────────


class StaticTokenProvider:
    """캐시·발급 없이 고정 토큰을 준다. KIS 가 거절하면 센다."""

    def __init__(self, token: str = FAKE_TOKEN) -> None:
        self.token = token
        self.invalidated = 0

    def get(self) -> str:
        return self.token

    def invalidate(self) -> None:
        self.invalidated += 1


def fake_settings() -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr("fake-key-for-tests"),
        kis_app_secret=SecretStr("fake-secret-for-tests"),
    )


def seed_cached_token(redis: Any, now: datetime, token: str = CACHED_TOKEN) -> None:
    """auth 가 둔 것처럼 Redis `kis:token`(fake_settings 앱키 소유) — 서비스의 읽기 전용 제공자
    (`services.auth.service.reader`)가 읽는다. 발급은 auth 몫이라 값만 둔다."""
    s = fake_settings()
    assert s.kis_app_key is not None
    rec = TokenRecord(
        access_token=SecretStr(token),
        expires_at=now + timedelta(hours=20),
        issued_at=now,
        owner=token_owner(s.kis_base, s.kis_app_key.get_secret_value()),
    )
    RedisTokenCache(redis, TOKEN_KEY).store(rec, now)


def make_client(
    server: FakeKisServer, limiter: RateLimiter, tokens: StaticTokenProvider | None = None
) -> KisClient:
    return KisClient(
        fake_settings(),
        token_provider=tokens or StaticTokenProvider(),
        rate_limiter=limiter,
        transport=server.transport,
    )


# ── 분봉 적재용 메모리 저장소 ────────────────────────────────────────────────


class MinuteMemoryStore:
    """scheduler 분봉 적재 저장소 흉내 — minute_bars 는 (code, market, ts) 로 덮어쓴다."""

    def __init__(self) -> None:
        self.bars: dict[tuple[str, str, datetime], MinuteBarRecord] = {}
        self.batches: list[int] = []  # 쓴 묶음마다 행 수
        self.raw: list[RawEnvelope] = []
        self.quarantined: list[QuarantineRecord] = []
        self.fail_write = False
        self.fail_raw = False
        self._lock = threading.Lock()

    def write_minute_bars(self, rows: Sequence[MinuteBarRecord]) -> None:
        with self._lock:
            if self.fail_write:
                raise StoreError("minute_bars: OperationalError: server closed the connection")
            for r in rows:
                self.bars[(r.code, r.market, r.ts)] = r
            self.batches.append(len(rows))

    def write_raw(self, envelopes: Sequence[RawEnvelope]) -> None:
        with self._lock:
            if self.fail_raw:
                raise StoreError("raw_messages: OperationalError: connection refused")
            self.raw.extend(envelopes)

    def write_quarantine(self, rows: Sequence[QuarantineRecord]) -> None:
        with self._lock:
            self.quarantined.extend(rows)

    def of(self, code: str, market: str) -> list[MinuteBarRecord]:
        """그 종목·시장 봉, 시각 순."""
        return sorted(
            (r for (c, m, _), r in self.bars.items() if (c, m) == (code, market)),
            key=lambda r: r.ts,
        )
