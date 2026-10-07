"""한국은행 ECOS Open API — 공개 등급은 **한국은행 작성 표만**(DATA_TIERS §1, probe_results F6·§4).

승격 원본: ETF-Traker `monitor/bok/fetch-ecos.js`(:21 BASE, :23~32 8계열, :55 URL 형식, :62~63 오류)
— 로직만 Python 으로(bok 은 이식하지 않았다). 명세 `docs/probe_results.md` §4.1·§4.3.

- 주소: `/api/{서비스}/{키}/json/kr/{시작건}/{끝건}/{경로 인자…}` — **키가 경로에 들어간다**. 오류
  문구는 키(원문·URL 인코딩)를 지운 뒤에만 만든다(절대 규칙 5). 호스트는 이 폴더에만 둔다
  (scripts/check_canonical.py 그룹 `ecos`).
- 두 층으로 나눈다.
    - `EcosTransport` — 전송·쪽 넘기기·오류 처리. **등급을 보지 않는다**. 로그인 등급의 타기관 표
      (802Y001 한국거래소·731Y001 서울외국환중개·901Y056 금융투자협회·901Y009 결정 전)는
      `kbj.data.private.ecos_restricted`(묶음 D)가 이 전송층을 재사용해 받는다(로그인 → 공개 import
      는 계약 ①이 허용한다).
    - `EcosClient` — 공개 쪽 입구. `PUBLIC_TABLES` 밖의 표는 `TierError` 로 거절한다.
- 오류(`RESULT.CODE`, 개발 가이드 메시지 표):
    - `INFO-200` 데이터 없음 → 빈 목록(오류 아님)
    - `INFO-100` 키 무효 → `EcosKeyError`(critical)
    - `ERROR-602` 과도한 호출 → 리미터 감속(`on_rate_limited` — limits.yaml `ecos.hold_s` 10분)
      + `EcosThrottled`. 같은 KST 날짜에 `close_after_throttles`(3)번이면 그날 닫는다(`EcosClosed`
      — 그날은 부르지 않는다) [제안]
    - `ERROR-400` 검색 범위 과다(60초 타임아웃) → `statistic_search` 가 기간을 반으로 나눠 다시
      (일·월·분기·연 주기만, 6번까지)
    - 그 밖 → `EcosError`
- 값(`DATA_VALUE`)은 문자열이다. 숫자 → `Decimal`, 빈 값·`-` → None(행은 남기고 `quality=invalid`),
  그 밖은 형식이 바뀐 것으로 보고 실패한다.
- 한도: 일·분 한도 미공표(602 로만 막는다) — 2/s [추정](limits.yaml `ecos`). 한 요청 최대 건수
  미표기 — 1~10,000 으로 쪽을 넘긴다(fetch-ecos.js 와 같음) [실측 필요].
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Final, Literal, NoReturn
from urllib.parse import quote, quote_plus

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.core.time import kst_date, utcnow
from kbj.data.datago import parse_amount
from kbj.data.http import FetchError, get_capped, make_client, scrub
from kbj.data.limits import Limits, load_limits
from kbj.data.ratelimit import Clock, Priority, RateLimiter, RedisRateLimiter

SOURCE: Final = "ECOS"
LIMITS_SOURCE: Final = "ecos"
_BASE: Final = "https://ecos.bok.or.kr/api"

CONNECT_S: Final = 5.0
TIMEOUT_S: Final = 60.0  # ECOS 는 검색 범위가 크면 60초 뒤 ERROR-400 을 준다
MAX_BYTES: Final = 64 * 1024 * 1024
PAGE_SIZE: Final = 10_000
MAX_PAGES: Final = 50
MAX_SPLIT_DEPTH: Final = 6

# 공개 등급 표 — 작성기관이 한국은행인 것만(probe_results §4.3, ORG_NAME 확인)
PUBLIC_TABLES: Final = frozenset(
    {
        "722Y001",  # 한국은행 기준금리 및 여수신금리
        "817Y002",  # 시장금리(일별) — 국고채·회사채 AA-·BBB-(공개 신용스프레드)
        "404Y014",  # 생산자물가지수(기본분류)
        "402Y014",  # 수출물가지수(기본분류)
        "401Y015",  # 수입물가지수(기본분류)
        "161Y005",  # M2 평잔 계절조정
        "301Y013",  # 국제수지
        "200Y102",  # 주요지표(분기지표) — GDP
        "513Y001",  # 경제심리지수
        "731Y003",  # 원화의 대미달러·대위안·대엔 환율(한국은행 작성) [실측 필요: 항목 코드]
        "721Y001",  # 시장금리(월·분기·연)
    }
)
# 공개 쪽에서 받지 않는 표와 작성기관(로그인 — kbj.data.private.ecos_restricted)
RESTRICTED_TABLES: Final[Mapping[str, str]] = {
    "802Y001": "한국거래소",
    "731Y001": "서울외국환중개",
    "901Y056": "금융투자협회",
    "901Y009": "국가데이터처(공개 판정 전 — probe_results §7 #11)",
}
PUBLIC_ORG: Final = "한국은행"

Cycle = Literal["A", "S", "Q", "M", "SM", "D"]
CYCLES: Final = ("A", "S", "Q", "M", "SM", "D")
_MISSING_VALUE: Final = frozenset({"", "-"})


class TierError(PermissionError):
    """공개 쪽에서 로그인 등급(타기관 작성) 표를 부르려 했다."""


class EcosError(FetchError):
    """ECOS 호출 실패. `code` 는 `RESULT.CODE`(예: `ERROR-101`), 전송 오류면 빈 문자열."""

    critical: bool = False

    def __init__(
        self, code: str, message: str, *, service: str = "", status: int | None = None
    ) -> None:
        self.code = code
        self.message = message
        reason = f"{code} {message}".strip() if code else message
        super().__init__(SOURCE, service, status, reason)


class EcosKeyError(EcosError):
    """`INFO-100` 인증키 무효 — 운영 critical."""

    critical = True


class EcosThrottled(EcosError):
    """`ERROR-602` 과도한 호출 — 감속했다. 다음 주기에 다시."""


class EcosClosed(EcosThrottled):
    """같은 날 602 를 정해진 횟수만큼 받아 그날 ECOS 호출을 닫았다(부르지 않았다)."""


class EcosRangeTooLarge(EcosError):
    """`ERROR-400` 검색 범위 과다(60초 타임아웃) — 기간을 나눠 다시 부른다."""


class EcosRow(BaseModel):
    """StatisticSearch 한 행. `source` = `ECOS:<표>`, 기준 시점은 `time`(주기별 형식)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    stat_code: str
    stat_name: str
    cycle: str
    item_codes: tuple[str, ...]
    item_names: tuple[str, ...]
    unit: str
    weight: str
    time: str  # D=YYYYMMDD, M=YYYYMM, Q=YYYYQn, A=YYYY …
    value: Decimal | None
    raw_value: str

    @property
    def as_of(self) -> str:
        """값이 가리키는 시점(`TIME` — 주기별 형식, 받은 시각이 아니다)."""
        return self.time

    @property
    def quality(self) -> Quality:
        """값이 있으면 ok, 비었으면 invalid(화면·계산에 쓰지 않는다)."""
        return Quality.OK if self.value is not None else Quality.INVALID


class EcosTable(BaseModel):
    """StatisticTableList 한 행 — `org_name`(작성기관)으로 등급을 본다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stat_code: str
    stat_name: str
    cycle: str
    org_name: str
    searchable: bool
    parent_code: str


class EcosItem(BaseModel):
    """StatisticItemList 한 행."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stat_code: str
    item_code: str
    item_name: str
    group_code: str
    cycle: str
    start_time: str
    end_time: str
    data_count: int | None
    unit: str


def _s(row: Mapping[str, Any], key: str) -> str:
    v = row.get(key)
    return "" if v is None else str(v).strip()


def parse_value(raw: object) -> Decimal | None:
    """`DATA_VALUE` → Decimal. 빈 값·`-` → None, 숫자가 아니면 `ValueError`."""
    s = "" if raw is None else str(raw).strip()
    if s in _MISSING_VALUE:
        return None
    return parse_amount(s)


# ── 기간 나누기(ERROR-400) ──────────────────────────────────────────────────────────────────


def _period_index(cycle: str, p: str) -> int:
    if cycle == "A" and len(p) == 4 and p.isdigit():
        return int(p)
    if cycle == "M" and len(p) == 6 and p.isdigit() and 1 <= int(p[4:]) <= 12:
        return int(p[:4]) * 12 + int(p[4:]) - 1
    if cycle == "Q" and len(p) == 6 and p[4] == "Q" and p[5] in "1234" and p[:4].isdigit():
        return int(p[:4]) * 4 + int(p[5]) - 1
    if cycle == "D" and len(p) == 8 and p.isdigit():
        return date(int(p[:4]), int(p[4:6]), int(p[6:])).toordinal()
    raise ValueError(f"주기 {cycle} 의 시점 형식이 아니다: {p!r}")


def _period_str(cycle: str, i: int) -> str:
    if cycle == "A":
        return f"{i:04d}"
    if cycle == "M":
        return f"{i // 12:04d}{i % 12 + 1:02d}"
    if cycle == "Q":
        return f"{i // 4:04d}Q{i % 4 + 1}"
    if cycle == "D":
        return f"{date.fromordinal(i):%Y%m%d}"
    raise ValueError(f"나눌 수 없는 주기: {cycle}")


def split_period(
    cycle: str, start: str, end: str
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """기간을 반으로. 한 시점뿐이거나 나눌 수 없는 주기(S·SM)면 None."""
    if cycle not in ("A", "M", "Q", "D"):
        return None
    a, b = _period_index(cycle, start), _period_index(cycle, end)
    if b <= a:
        return None
    mid = (a + b) // 2
    return (start, _period_str(cycle, mid)), (_period_str(cycle, mid + 1), end)


# ── 전송층 ─────────────────────────────────────────────────────────────────────────────────


class EcosTransport:
    """ECOS 전송·쪽 넘기기·오류 처리. 등급을 보지 않는다 — 공개 쪽은 `EcosClient` 를 쓴다."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        page_size: int = PAGE_SIZE,
        max_pages: int = MAX_PAGES,
        close_after_throttles: int = 3,
        priority: Priority = Priority.P3,
        acquire_timeout: float | None = 120.0,
        lang: Literal["kr", "en"] = "kr",
        now: Callable[[], datetime] = utcnow,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        key = api_key.get_secret_value().strip()
        if not key or "/" in key:
            raise ValueError("ECOS 키가 비었거나 형식이 틀렸다")
        if not 1 <= page_size <= PAGE_SIZE or close_after_throttles < 1:
            raise ValueError("page_size 는 1~10000, close_after_throttles 는 1 이상")
        self._key = key
        self._secrets = [key, quote(key, safe=""), quote_plus(key)]
        self._limiter = limiter
        self._timeout = timeout
        self._page = page_size
        self._max_pages = max_pages
        self._close_after = close_after_throttles
        self._priority = priority
        self._acquire_timeout = acquire_timeout
        self._lang = lang
        self._now = now
        self._monotonic = monotonic
        self._http = make_client(_BASE, connect_s=CONNECT_S, read_s=timeout, transport=transport)
        self._throttle_day: date | None = None
        self._throttles = 0
        self._closed_day: date | None = None
        self.calls = 0

    def __repr__(self) -> str:
        return "EcosTransport(key=***)"

    def close(self) -> None:
        self._http.close()

    # ── 서비스 ──────────────────────────────────────────────────────────────────────────

    def statistic_search(
        self, stat_code: str, cycle: str, start: str, end: str, items: Sequence[str] = ()
    ) -> list[EcosRow]:
        """StatisticSearch(OA-1040). 항목코드는 앞에서부터(빈칸 = 전체 — 뒤를 비운다).

        ERROR-400(범위 과다)이면 기간을 반으로 나눠 다시 부른다(최대 6번 나눔).
        """
        _check_stat_code(stat_code)
        if cycle not in CYCLES:
            raise ValueError(f"주기는 {', '.join(CYCLES)} 중 하나: {cycle!r}")
        if len(items) > 4 or any(not i or "/" in i or i != i.strip() for i in items):
            raise ValueError("항목코드는 4개까지, 비지 않고 '/' 없이")
        return self._search(stat_code, cycle, start, end, tuple(items), 0)

    def _search(
        self, stat_code: str, cycle: str, start: str, end: str, items: tuple[str, ...], depth: int
    ) -> list[EcosRow]:
        try:
            rows = self._paged("StatisticSearch", [stat_code, cycle, start, end, *items])
        except EcosRangeTooLarge:
            halves = split_period(cycle, start, end) if depth < MAX_SPLIT_DEPTH else None
            if halves is None:
                raise
            (a1, b1), (a2, b2) = halves
            return self._search(stat_code, cycle, a1, b1, items, depth + 1) + self._search(
                stat_code, cycle, a2, b2, items, depth + 1
            )
        out: list[EcosRow] = []
        for r in rows:
            raw = _s(r, "DATA_VALUE")
            try:
                value = parse_value(raw)
            except ValueError:
                raise EcosError(
                    "", f"{stat_code} DATA_VALUE 가 숫자가 아니다: {raw[:20]!r}", service="search"
                ) from None
            out.append(
                EcosRow(
                    source=f"{SOURCE}:{stat_code}",
                    stat_code=_s(r, "STAT_CODE") or stat_code,
                    stat_name=_s(r, "STAT_NAME"),
                    cycle=cycle,
                    item_codes=tuple(c for i in range(1, 5) if (c := _s(r, f"ITEM_CODE{i}"))),
                    item_names=tuple(n for i in range(1, 5) if (n := _s(r, f"ITEM_NAME{i}"))),
                    unit=_s(r, "UNIT_NAME"),
                    weight=_s(r, "WGT"),
                    time=_s(r, "TIME"),
                    value=value,
                    raw_value=raw,
                )
            )
        return out

    def statistic_table_list(self) -> list[EcosTable]:
        """StatisticTableList(OA-1020) — 통계표 목록과 작성기관."""
        return [
            EcosTable(
                stat_code=_s(r, "STAT_CODE"),
                stat_name=_s(r, "STAT_NAME"),
                cycle=_s(r, "CYCLE"),
                org_name=_s(r, "ORG_NAME"),
                searchable=_s(r, "SRCH_YN") == "Y",
                parent_code=_s(r, "P_STAT_CODE"),
            )
            for r in self._paged("StatisticTableList", [])
        ]

    def statistic_item_list(self, stat_code: str) -> list[EcosItem]:
        """StatisticItemList(OA-1030) — 표의 항목 코드·수록 기간."""
        _check_stat_code(stat_code)
        out: list[EcosItem] = []
        for r in self._paged("StatisticItemList", [stat_code]):
            cnt = _s(r, "DATA_CNT")
            out.append(
                EcosItem(
                    stat_code=_s(r, "STAT_CODE") or stat_code,
                    item_code=_s(r, "ITEM_CODE"),
                    item_name=_s(r, "ITEM_NAME"),
                    group_code=_s(r, "GRP_CODE"),
                    cycle=_s(r, "CYCLE"),
                    start_time=_s(r, "START_TIME"),
                    end_time=_s(r, "END_TIME"),
                    data_count=int(cnt) if cnt.isdigit() else None,
                    unit=_s(r, "UNIT_NAME"),
                )
            )
        return out

    # ── 쪽 넘기기·한 번 부르기 ─────────────────────────────────────────────────────────────

    def _paged(self, service: str, args: Sequence[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        first = 1
        for _ in range(self._max_pages):
            rows, total = self._request(service, args, first, first + self._page - 1)
            out.extend(rows)
            if not rows or total is None or len(out) >= total or len(rows) < self._page:
                return out
            first += self._page
        raise EcosError("", f"{service}: {self._max_pages}쪽을 넘었다 — 기간을 나눠 부른다")

    def _request(
        self, service: str, args: Sequence[str], first: int, last: int
    ) -> tuple[list[dict[str, Any]], int | None]:
        today = kst_date(self._now())
        if self._closed_day == today:
            raise EcosClosed(
                "ERROR-602",
                f"오늘({today}) {self._close_after}번 이용 제한을 받아 닫았다",
                service=service,
            )
        segments = [service, self._key, "json", self._lang, str(first), str(last), *args]
        path = "/" + "/".join(quote(s, safe="") for s in segments)
        self._limiter.acquire(self._priority, f"ecos:{service}", self._acquire_timeout)
        self.calls += 1
        try:
            status, content = get_capped(
                self._http,
                path,
                max_bytes=MAX_BYTES,
                total_s=self._timeout * 2,
                clock=self._monotonic,
                source=SOURCE,
                dataset=service,
            )
        except FetchError as e:
            raise EcosError("", self._scrub(e.reason), service=service, status=e.status) from None
        except httpx.HTTPError as e:
            raise EcosError("", self._scrub(f"{type(e).__name__}: {e}"), service=service) from None
        try:
            js: Any = json.loads(content)
        except ValueError:
            head = content[:200].decode("utf-8", "replace")
            raise EcosError(
                "",
                self._scrub(f"HTTP {status} JSON 이 아니다: {' '.join(head.split())}"),
                service=service,
                status=status,
            ) from None
        if not isinstance(js, dict):
            raise EcosError("", "응답 최상위가 객체가 아니다", service=service, status=status)
        result = js.get("RESULT")
        box: Any = js.get(service)
        if result is None and isinstance(box, dict):
            result = box.get("RESULT")
        if isinstance(result, dict):
            code = str(result.get("CODE", "")).strip()
            msg = self._scrub(str(result.get("MESSAGE", "")).strip())
            if code == "INFO-200":
                return [], 0
            self._raise_result(code, msg, service, status)
        if status != 200 or not isinstance(box, dict):
            raise EcosError("", f"HTTP {status} — {service} 봉투가 없다", service=service)
        rows: Any = box.get("row") or []
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise EcosError("", f"{service}.row 가 목록이 아니다", service=service)
        total_raw = str(box.get("list_total_count", "")).strip()
        total = int(total_raw) if total_raw.isdigit() else None
        out: list[dict[str, Any]] = []
        for r in rows:  # 객체가 아닌 행을 조용히 건너뛰지 않는다 — 형식이 바뀐 것이다
            if not isinstance(r, dict):
                raise EcosError("", f"{service}.row 에 객체가 아닌 행이 있다", service=service)
            out.append({str(k): v for k, v in r.items()})
        return out, total

    def _raise_result(self, code: str, msg: str, service: str, status: int) -> NoReturn:
        num = code.rsplit("-", 1)[-1]
        if code == "INFO-100":
            raise EcosKeyError(code, msg, service=service, status=status)
        if num == "602":
            self._limiter.on_rate_limited()
            today = kst_date(self._now())
            if self._throttle_day != today:
                self._throttle_day, self._throttles = today, 0
            self._throttles += 1
            if self._throttles >= self._close_after:
                self._closed_day = today
                raise EcosClosed(
                    code, f"{msg} — 오늘 {self._throttles}번째, 그날 닫음", service=service
                )
            raise EcosThrottled(code, msg, service=service, status=status)
        if num == "400":
            raise EcosRangeTooLarge(code, msg, service=service, status=status)
        raise EcosError(code, msg, service=service, status=status)

    def _scrub(self, msg: str) -> str:
        return scrub(msg, None, self._secrets)


def _check_stat_code(stat_code: str) -> None:
    if len(stat_code) != 7 or not stat_code.isalnum():
        raise ValueError(f"통계표 코드 형식이 아니다(7자): {stat_code!r}")


# ── 공개 쪽 입구 ──────────────────────────────────────────────────────────────────────────


class EcosClient:
    """공개 등급 ECOS — `PUBLIC_TABLES`(한국은행 작성 표)만 받는다."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        limiter: RateLimiter,
        transport: httpx.BaseTransport | None = None,
        timeout: float = TIMEOUT_S,
        **kw: Any,
    ) -> None:
        self.transport = EcosTransport(
            api_key, limiter=limiter, transport=transport, timeout=timeout, **kw
        )

    def __repr__(self) -> str:
        return "EcosClient(key=***)"

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        redis: Redis,
        *,
        limits: Limits | None = None,
        clock: Clock | None = None,
        **kw: Any,
    ) -> EcosClient:
        """`KBJ_ECOS_KEY` + Redis 리미터 `rl:ecos:<키 해시>`(limits.yaml `ecos`)."""
        if settings.ecos_key is None:
            raise EcosKeyError("", "KBJ_ECOS_KEY 가 없다")
        src = (limits or load_limits(settings=settings)).source(LIMITS_SOURCE)
        limiter = RedisRateLimiter.scoped(
            redis, LIMITS_SOURCE, settings.ecos_key.get_secret_value(), src.rate_config(), clock
        )
        close_after = src.close_after_throttles or 3
        return cls(settings.ecos_key, limiter=limiter, close_after_throttles=close_after, **kw)

    def search(
        self, stat_code: str, cycle: str, start: str, end: str, items: tuple[str, ...] = ()
    ) -> list[EcosRow]:
        """값 조회(StatisticSearch). 공개 표가 아니면 `TierError`(부르지 않는다)."""
        require_public(stat_code)
        return self.transport.statistic_search(stat_code, cycle, start, end, items)

    def table_list(self) -> list[EcosTable]:
        """통계표 목록(값이 아니라 목록 — 등급 검사 없음). `ORG_NAME` 으로 공개 표를 점검한다."""
        return self.transport.statistic_table_list()

    def item_list(self, stat_code: str) -> list[EcosItem]:
        """항목 목록. 공개 표만."""
        require_public(stat_code)
        return self.transport.statistic_item_list(stat_code)


def require_public(stat_code: str) -> None:
    """공개 표가 아니면 `TierError`(작성기관을 담는다)."""
    if stat_code in PUBLIC_TABLES:
        return
    org = RESTRICTED_TABLES.get(stat_code)
    who = f"작성기관 {org} — " if org else "공개 판정 전 표 — "
    raise TierError(
        f"ECOS {stat_code}: {who}공개 쪽은 한국은행 작성 표(PUBLIC_TABLES)만 받는다. "
        "로그인 등급 표는 kbj.data.private.ecos_restricted 로"
    )


def public_table_problems(tables: Sequence[EcosTable]) -> list[str]:
    """`PUBLIC_TABLES` 가 목록에 있고 작성기관이 한국은행인지 — 키 받은 뒤 점검용.

    빈 목록이면 문제 없음.
    """
    by_code = {t.stat_code: t for t in tables}
    out: list[str] = []
    for code in sorted(PUBLIC_TABLES):
        t = by_code.get(code)
        if t is None:
            out.append(f"{code}: 목록에 없다")
        elif t.org_name and t.org_name != PUBLIC_ORG:
            out.append(f"{code}: 작성기관이 {t.org_name} — 공개 표에서 뺀다")
    return out
