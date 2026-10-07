"""KRX 주식 일별 행 → 저장 모양(`prv_market.daily_bar`·`stock_snapshot`) — 로그인 등급.

승격 원본: ETF-Traker `board/ingest/krx.py`(`PATHS`:29, `FIELD`:36, `_isu_to_code`:83, `_norm`:103,
`fetch_day`:123, `fetch_index`:136, `sector_map`:153). 설계 `docs/p2_design.md` §1.5.

- 호출은 `kbj.data.private.krx.client.KrxClient` 가 하고, 여기서는 받은 행만 다룬다(네트워크 없음).
  ET `fetch_day` 의 '시장별로 불러 행을 모은다'는 수집 작업(`krx.daily`, P3)이 한다.
- 필드 후보(ET `FIELD`)는 `models.STOCK_FIELDS` 한 곳에 둔다 — 응답이 개편되면 거기만 고친다.
- ET 와 다른 점:
    - 금액(거래대금·시가총액)은 **원 단위 정수**로 둔다(ET 는 억원 float 으로 접었다 — metrics §0).
    - 종가·코드가 없는 행을 조용히 버리지 않는다. 코드가 없으면 그 행은 `RowError`, 종가가 없으면
      `quality=invalid` 로 남긴다(화면·계산은 `quality.usable` 로 거른다 — 절대 규칙 4).
    - 행이 있는데 한 행도 맞지 않으면 `KrxRowsError` — 필드 이름이 바뀐 것을 휴장일(빈 목록)과
      구분한다(ET `_isu_to_code` 머리말의 교훈).
    - 거래소 구분(`venue`)을 행에 싣는다 — KRX OpenAPI 는 KRX 체결분으로 본다 [실측 필요].
- 거래대금을 못 받은 행은 `turnover=None`·`turnover_is_estimate=True`(ET 계약 그대로 — 0 은
  '거래 없음', None 은 '못 받음', D-019).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import date
from decimal import Decimal
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, ValidationError

from kbj.core.quality import Quality
from kbj.data.private.krx.client import STOCK_DAILY, StockMarket
from kbj.data.private.krx.models import (
    STOCK_FIELDS,
    KrxIndexDaily,
    KrxRowsError,
    KrxStockDaily,
    RowError,
    parse_index_daily_rows,
)
from kbj.data.spec import Venue

FIELD: Final = STOCK_FIELDS  # ET 이름(`board/ingest/krx.py:FIELD`) — 후보 표는 models 한 곳

_DIGITS6 = re.compile(r"\d{6}")
_ISIN_DIGITS = re.compile(r"KR\w(\d{6})\d*")  # KR7 005930 003 (ET)
_ISIN_ALNUM = re.compile(r"KR[0-9A-Z]([0-9A-Z]{6})[0-9A-Z]{3}")
_ALNUM6 = re.compile(r"[0-9A-Z]{6}")


def isu_to_code(raw: object) -> str:
    """단축코드 6자리를 뽑는다. 못 뽑으면 빈 문자열(ET `_isu_to_code`).

    ISIN(KR7005930003)이 오면 가운데 6자리가 단축코드다. ET 의 옛 `re.sub(r'^KR\\w+$', '', code)`
    는 문자열 **전체**에 매치해 결과가 빈 문자열이 됐고, 필드명 오판이 휴장일과 구분되지 않았다.

    KBJ 추가: 숫자만이 아닌 영숫자 단축코드(예: `0009K0` 꼴)와 그 ISIN 도 받는다 [추정 — KRX 가 신규
    종목에 영문 섞인 단축코드를 쓰기 시작했다는 공지 기준, 실측 필요].
    """
    c = str(raw or "").strip().upper()
    if _DIGITS6.fullmatch(c):
        return c
    m = _ISIN_DIGITS.fullmatch(c)
    if m:
        return m.group(1)
    m = _ISIN_ALNUM.fullmatch(c)
    if m:
        return m.group(1)
    if _ALNUM6.fullmatch(c) and any(ch.isdigit() for ch in c):
        return c
    m = _DIGITS6.search(c)
    return m.group(0) if m else ""


class KrxStockRow(BaseModel):
    """주식 일별 한 행의 저장 모양(ET `_norm` 의 칸 + 출처·품질·거래소).

    금액은 원 정수. `as_of` 는 거래일(받은 시각 아님). `sector` 는 ET 후보 순서(업종명 → 소속부)로
    고른 값 — 소속부는 업종이 아니라 코스닥 우량·벤처기업부 같은 구분일 수 있다 [실측 필요].
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    name: str
    market: str  # 응답의 시장 구분(KOSPI·KOSDAQ·KONEX) — 없으면 부른 시장 이름
    sector: str | None
    as_of: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    chg_pct: Decimal | None
    volume: int | None
    turnover: int | None  # 거래대금(원)
    mktcap: int | None  # 시가총액(원)
    shares: int | None  # 상장주식수
    turnover_is_estimate: bool
    source: str  # KRX:sto/stk_bydd_trd 등
    quality: Quality
    venue: Venue = Venue.KRX


def normalize_stock_row(
    r: Mapping[str, Any], *, market: StockMarket | None = None, endpoint: str | None = None
) -> KrxStockRow:
    """응답 한 행 → `KrxStockRow`. 형식이 틀리거나 코드를 못 뽑으면 `ValueError`.

    `market` 을 주면 출처가 그 시장 엔드포인트(`KRX:sto/stk_bydd_trd` 등)가 되고, 응답에 시장
    구분이 없을 때 그 이름(대문자)을 쓴다.
    """
    ep = endpoint or (STOCK_DAILY[market] if market is not None else "")
    source = f"KRX:{ep.lstrip('/')}" if ep else "KRX"
    try:
        m = KrxStockDaily.model_validate({**dict(r), "source": source})
    except ValidationError as e:
        raise ValueError(str(e.errors(include_url=False, include_input=False))) from None
    code = isu_to_code(m.isu_cd)
    if not code:
        raise ValueError(f"종목코드를 뽑지 못했다: {m.isu_cd[:20]!r}")
    return KrxStockRow(
        code=code,
        name=m.isu_nm,
        market=m.mkt_nm or (market.upper() if market else ""),
        sector=m.idx_ind_nm or m.sect_tp_nm,
        as_of=m.bas_dd,
        open=m.tdd_opnprc,
        high=m.tdd_hgprc,
        low=m.tdd_lwprc,
        close=m.tdd_clsprc,
        chg_pct=m.fluc_rt,
        volume=m.acc_trdvol,
        turnover=m.acc_trdval,
        mktcap=m.mktcap,
        shares=m.list_shrs,
        turnover_is_estimate=m.acc_trdval is None,
        source=m.source,
        quality=m.quality,
    )


def parse_stock_rows(
    rows: Iterable[Mapping[str, Any]], *, market: StockMarket
) -> tuple[list[KrxStockRow], list[RowError]]:
    """한 시장 하루치 행들(ET `fetch_day` 의 정규화 부분). 빈 목록 = 휴장·갱신 전(실패 아님).

    틀린 행은 `RowError` 로 따로 돌려준다. 행이 있는데 하나도 맞지 않으면 `KrxRowsError`.
    """
    items = list(rows)
    ok: list[KrxStockRow] = []
    bad: list[RowError] = []
    for i, r in enumerate(items):
        try:
            ok.append(normalize_stock_row(r, market=market))
        except ValueError as e:
            bad.append(RowError(i, str(e)))
    if items and not ok:
        raise KrxRowsError(f"KRX:{STOCK_DAILY[market].lstrip('/')}", len(items), bad[0])
    return ok, bad


def parse_index_rows(
    rows: Iterable[Mapping[str, Any]], *, endpoint: str
) -> tuple[list[KrxIndexDaily], list[RowError]]:
    """지수 일별 행들(ET `fetch_index` 의 정규화 부분). 이름으로 찾으려면 `index_by_name`."""
    return parse_index_daily_rows(rows, endpoint=endpoint)


def index_by_name(rows: Iterable[KrxIndexDaily]) -> dict[str, KrxIndexDaily]:
    """지수 이름 → 행(ET `fetch_index` 의 반환 모양). 같은 이름이 둘이면 `ValueError`."""
    out: dict[str, KrxIndexDaily] = {}
    for r in rows:
        if r.idx_nm in out:
            raise ValueError(f"지수 이름이 겹친다: {r.idx_nm!r}")
        out[r.idx_nm] = r
    return out


def sector_map(rows: Iterable[KrxStockRow]) -> dict[str, str]:
    """종목 → 업종(ET `sector_map` — 1층 분류의 원본). 업종이 하나도 없으면 `ValueError`.

    `sector` 가 소속부(우량기업부 등)인지 업종인지는 [실측 필요] — 업종 분류 사전은 P5
    (`pub_themes.sector_map`, ET board48)가 정본이다.
    """
    m = {x.code: x.sector for x in rows if x.sector}
    if not m:
        raise ValueError("업종 필드가 비었다 — models.STOCK_FIELDS 의 업종 후보 확인 필요")
    return m
