"""KIS 응답 공용 해석 도구 — 숫자·날짜·필드 후보·응답 봉투(docs/p3_design.md §1.2).

투자자(`investors`)·현재가(`quotes`)·순위(`ranks`)·ETF(`etf`)·지수(`index`) 파서가 같이 쓴다. 이
모듈은 받은 본문(dict)만 다룬다 — 네트워크·토큰은 `kbj.data.private.kis.rest.KisRestClient`.

- **숫자**: KIS 는 숫자를 문자열로 준다(`"+1,234"`·`"-0"`·`""`). 빈 값·`-` 는 **None**(못 받음 — 0
  으로 채우지 않는다). 음의 0 은 0. 정수 칸에 소수가 오면 오류(금액을 조용히 자르지 않는다).
- **필드 후보**: 같은 뜻의 칸 이름이 TR 마다 다르다(ET `board/ingest/kis.py:FIELD`:71). 후보 표는 각
  파서 모듈 머리에 두고 `pick` 이 앞에서부터 처음 값이 있는 칸을 고른다. 응답이 개편되면 표만
  고친다.
- **단위**(R1): KIS 투자자 금액은 **백만원**으로 읽는다(ET `kis.py`:188·225 — 실호출로 확정된 적
  없음 [실측 필요 — 체크리스트 #22]). 상수 하나(`MILLION_KRW`)로 두고 파서가 **원**으로 바꿔 넘긴다.
- **응답 봉투**: `rt_cd` 가 '0' 이 아니거나 HTTP 200 이 아니면 `KisRejected`(사유는 msg_cd·msg1 앞
  부분 — 토큰·앱키는 응답에 없고 싣지도 않는다). 결과 칸이 없으면 `KisShapeError`(받은 키 이름만).
- **거래소 구분 파라미터**(D-P3-9): `FID_COND_MRKT_DIV_CODE` J(KRX)·NX(NXT)·UN(통합) [추정 — 실측
  필요 #25]. 실측 전 설정은 KRX 하나(`config/markets.yaml` `kis.venues`).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Final

__all__ = [
    "MILLION_KRW",
    "PATHS",
    "VENUE_PARAM",
    "KisEmpty",
    "KisParseError",
    "KisRejected",
    "KisShapeError",
    "TrSpec",
    "has_any",
    "keys_of",
    "kis_decimal",
    "kis_float",
    "kis_int",
    "pick",
    "require_ok",
    "rows_of",
    "venue_param",
    "ymd",
]

# 투자자 순매수 금액 단위 — 백만원 [실측 필요 #22]. 틀리면 금액이 10⁶ 배 틀린다(R1) — 그래서
# investors.unit_check 가 수량 × 종가와 자릿수를 대조한다.
MILLION_KRW: Final = 1_000_000

# 거래소 구분 → KIS 시장 분류 코드 [추정 — 실측 필요 #25]
VENUE_PARAM: Final[Mapping[str, str]] = {"KRX": "J", "NXT": "NX", "TOTAL": "UN"}


@dataclass(frozen=True)
class TrSpec:
    """TR 하나 — 경로와 tr_id. 경로는 KIS 문서 기준(ET `board/ingest/kis.py:ENDPOINTS`:47)."""

    tr_id: str
    path: str


# 이 단계(P3)에서 부르는 TR. FHPUP02100000·FHPUP02140000 은 [추정 TR — conflict_map §1.13, #24]
PATHS: Final[Mapping[str, TrSpec]] = {
    "stock_quote": TrSpec("FHKST01010100", "/uapi/domestic-stock/v1/quotations/inquire-price"),
    "stock_investor": TrSpec(
        "FHKST01010900", "/uapi/domestic-stock/v1/quotations/inquire-investor"
    ),
    "market_investor": TrSpec(
        "FHPTJ04040000", "/uapi/domestic-stock/v1/quotations/inquire-investor-daily-by-market"
    ),
    "inst_foreign_total": TrSpec(
        "FHPTJ04400000", "/uapi/domestic-stock/v1/quotations/foreign-institution-total"
    ),
    "volume_rank": TrSpec("FHPST01710000", "/uapi/domestic-stock/v1/quotations/volume-rank"),
    "etf_quote": TrSpec("FHPST02400000", "/uapi/etfetn/v1/quotations/inquire-price"),
    "index_quote": TrSpec(
        "FHPUP02100000", "/uapi/domestic-stock/v1/quotations/inquire-index-price"
    ),
    "sector_quotes": TrSpec(
        "FHPUP02140000", "/uapi/domestic-stock/v1/quotations/inquire-index-category-price"
    ),
}

_BLANK: Final = frozenset({"", "-", "N/A"})
_YMD = re.compile(r"\d{8}")
MSG_MAX: Final = 120


class KisParseError(ValueError):
    """응답을 값으로 읽을 수 없다(형식). 메시지에는 키 이름·건수만 — 값은 싣지 않는다."""


class KisShapeError(KisParseError):
    """결과 칸이 없거나 모양이 다르다(필드 이름이 바뀐 날)."""


class KisEmpty(KisParseError):
    """질의가 성립하지 않았다 — 결과가 비었거나 값이 모두 0(ET `market_flows`:201~208).

    0 을 사실로 내보내지 않는다(CLAUDE.md 절대 규칙 2·4)."""


class KisRejected(RuntimeError):
    """KIS 가 거절했다(HTTP ≠ 200 또는 rt_cd ≠ '0'). `retryable` = 다음 시도에서 나을 수 있다."""

    def __init__(self, what: str, status: int, msg_cd: str, msg: str) -> None:
        self.status = status
        self.msg_cd = msg_cd
        self.retryable = status >= 500 or status == 429 or msg_cd.startswith("EGW")
        super().__init__(f"{what}: HTTP {status} {msg_cd} {msg[:MSG_MAX]}".rstrip())


def require_ok(what: str, status: int, body: Mapping[str, Any]) -> None:
    """HTTP 200 + rt_cd '0' 이 아니면 `KisRejected`."""
    rt = str(body.get("rt_cd", ""))
    if status == 200 and rt == "0":
        return
    msg_cd = str(body.get("msg_cd", "") or ("HTTP500" if status >= 500 else ""))
    raise KisRejected(what, status, msg_cd, str(body.get("msg1", "") or ""))


def venue_param(venue: str) -> str:
    """거래소 구분 → `FID_COND_MRKT_DIV_CODE`. 모르는 구분은 오류(지어내지 않는다)."""
    try:
        return VENUE_PARAM[venue]
    except KeyError:
        raise ValueError(f"KIS 거래소 구분 파라미터를 모른다: {venue!r}") from None


def _clean(v: object) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        raise KisParseError("숫자 칸에 bool")
    s = str(v).strip().replace(",", "")
    if s in _BLANK:
        return None
    if s.startswith("+"):
        s = s[1:]
    return s


def kis_decimal(v: object) -> Decimal | None:
    """KIS 숫자 문자열 → Decimal. 빈 값은 None, 숫자가 아니면 `KisParseError`."""
    s = _clean(v)
    if s is None:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise KisParseError(f"숫자가 아니다: {s[:20]!r}") from None
    if not d.is_finite():
        raise KisParseError("유한한 수가 아니다")
    return d + 0  # -0 → 0


def kis_float(v: object) -> float | None:
    d = kis_decimal(v)
    return None if d is None else float(d)


def kis_int(v: object, scale: int = 1) -> int | None:
    """정수(원·주). scale 을 곱한 뒤에도 정수가 아니면 `KisParseError`(조용히 자르지 않는다)."""
    d = kis_decimal(v)
    if d is None:
        return None
    d = d * scale
    if d != d.to_integral_value():
        raise KisParseError(f"정수 칸에 소수: {d}")
    return int(d)


def pick(row: Mapping[str, Any], *keys: str) -> Any:
    """후보 키 가운데 앞에서부터 처음으로 값이 있는 칸(빈 문자열·'-' 는 건너뛴다). 없으면 None."""
    for k in keys:
        if k in row:
            v = row[k]
            if v is None:
                continue
            if isinstance(v, str) and v.strip() in _BLANK:
                continue
            return v
    return None


def has_any(row: Mapping[str, Any], keys: Sequence[str]) -> bool:
    """후보 키 가운데 하나라도 값이 있는가."""
    return pick(row, *keys) is not None


def rows_of(body: Mapping[str, Any], *keys: str, what: str) -> list[dict[str, Any]]:
    """결과 배열. 첫 후보 키부터 본다. dict 는 한 행으로. 키가 하나도 없으면 `KisShapeError`."""
    for k in keys:
        if k not in body:
            continue
        v = body[k]
        if v is None:
            return []
        if isinstance(v, dict):
            return [dict(v)]  # pyright: ignore[reportUnknownArgumentType]
        if isinstance(v, list):
            out: list[dict[str, Any]] = []
            for item in v:  # pyright: ignore[reportUnknownVariableType]
                if not isinstance(item, dict):
                    raise KisShapeError(f"{what}: 결과 행이 객체가 아니다")
                out.append(dict(item))  # pyright: ignore[reportUnknownArgumentType]
            return out
        raise KisShapeError(f"{what}: 결과 칸 {k} 가 목록·객체가 아니다")
    got = ",".join(sorted(str(k) for k in body)[:8])
    raise KisShapeError(f"{what}: 결과 칸({'/'.join(keys)})이 없다 — 받은 키 {got}")


def ymd(v: object) -> date | None:
    """`YYYYMMDD` → date. 형식이 다르면 None(그 행은 날짜 없는 행 — 버린다)."""
    s = str(v or "").strip()
    if not _YMD.fullmatch(s):
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def keys_of(rows: Sequence[Mapping[str, Any]], limit: int = 12) -> str:
    """사유 문구용 — 받은 칸 이름(값 없음)."""
    names = sorted({str(k) for r in rows[:5] for k in r})
    return ",".join(names[:limit]) + ("…" if len(names) > limit else "")
