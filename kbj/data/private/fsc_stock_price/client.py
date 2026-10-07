"""금융위원회 주식시세정보(공공데이터포털 15094808) — 로그인 등급(공공누리 4유형, 원천 KRX).

승격 원본: ETF-Traker `board/ingest/datago.py`(`PRICE`:29, `_norm`:104, `fetch_day`:127,
`fetch_ohlcv`:148). 명세 `docs/probe_results.md` §3.3·F7. 설계 `docs/p2_design.md` §1.5.

- **V2 경로**로 부른다(F7): `/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2`. ET 는
  옛 V1(`/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo`)을 썼다 — 옛 경로 존속과
  V2 응답 필드 차이는 [실측 필요](probe_results §7 #8). 필드 이름은 V1 문서 기준이다.
- 전송·키 형태·리미터·일 예산·게이트웨이 오류는 `kbj.data.datago.DatagoTransport`(묶음 C)가 한다 —
  여기서는 파라미터와 행 모양만. 전송층에 포털 ID `15094808` 이 등록돼 있어야 한다.
- `numOfRows` 최대 1만(문서) — 다 받을 때까지 쪽을 넘긴다. `totalCount` 가 없거나 0 이어도
  '쪽이 꽉 찼는지'로 판단한다(ET `fetch_day` 교훈 — 3,900종목 중 1,000개만 받고 정상 종료).
- **`endBasDt` 는 '미만'**(금융위 공통 문서 표기)이라 끝 날을 넣으려고 하루 더해 부르고, 받은 행은
  요청 구간 [start, end] 로 다시 거른다 — '이하'로 동작해도 결과가 같다 [확인 필요].
- 공표: 영업일 D+1 13시 이후(일 1회), 30 tps. 수정주가가 아니다(액면분할·무상증자가 과거에 반영
  안 됨 — ET 머리말).
- 값: 금액(거래대금 `trPrc`·시가총액 `mrktTotAmt`)은 원 정수, 빈 값·`-` 는 None(0 으로 채우지 않는다
  — ET D-019). 종가가 없는 행은 버리지 않고 `quality=invalid`, 코드가 없거나 형식이 틀린 행은
  `RowError` 로 돌려준다(절대 규칙 4). 행이 있는데 하나도 맞지 않으면 `FscFormatError`.
- 응답 등급이 로그인이라 원본 응답·fixture 는 레포에 넣지 않는다(시험은 합성 — CLAUDE.md §2).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from datetime import date, timedelta
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.data.datago import DatagoFormatError, DatagoKeyError, DatagoTransport
from kbj.data.private.krx.models import (
    Num,
    OptText,
    Qty,
    RowError,
    Text,
    Won,
    Ymd,
    parse_rows,
)
from kbj.data.spec import Venue

DATASET_ID: Final = "15094808"
SOURCE: Final = f"DATAGO:{DATASET_ID}"
PATH: Final = "/1160100/GetStockSecuritiesInfoService_V2/getStockPriceInfo_V2"
PAGE_MAX: Final = 10_000  # numOfRows 최대(문서)
_CODE = re.compile(r"[0-9A-Z]{6}")


class FscFormatError(DatagoFormatError):
    """응답 행이 명세와 다르다(한 행도 맞지 않음·쪽 수 초과)."""


class FscPriceRow(BaseModel):
    """주식 시세 한 행. `as_of` = `bas_dt`(거래일), 금액은 원 정수(필드 이름은 V1 문서 기준
    [실측 필요])."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=False)

    source: str = SOURCE
    bas_dt: Ymd = Field(validation_alias="basDt")
    srtn_cd: Text = Field(validation_alias="srtnCd")  # 단축코드(앞 'A' 는 code 가 뗀다)
    isin_cd: OptText = Field(default=None, validation_alias="isinCd")
    itms_nm: Text = Field(validation_alias="itmsNm")
    mrkt_ctg: OptText = Field(default=None, validation_alias="mrktCtg")  # KOSPI·KOSDAQ·KONEX
    clpr: Num = Field(default=None, validation_alias="clpr")  # 종가
    vs: Num = Field(default=None, validation_alias="vs")  # 대비
    flt_rt: Num = Field(default=None, validation_alias="fltRt")  # 등락률(%)
    mkp: Num = Field(default=None, validation_alias="mkp")  # 시가
    hipr: Num = Field(default=None, validation_alias="hipr")  # 고가
    lopr: Num = Field(default=None, validation_alias="lopr")  # 저가
    trqu: Qty = Field(default=None, validation_alias="trqu")  # 거래량
    tr_prc: Won = Field(default=None, validation_alias="trPrc")  # 거래대금(원)
    lstg_st_cnt: Qty = Field(default=None, validation_alias="lstgStCnt")  # 상장주식수
    mrkt_tot_amt: Won = Field(default=None, validation_alias="mrktTotAmt")  # 시가총액(원)
    venue: Venue = Venue.KRX  # 원천 KRX — NXT 체결분 포함 여부 [실측 필요]

    @property
    def code(self) -> str:
        """단축코드 6자리(ET: 앞 'A' 를 뗀다)."""
        return self.srtn_cd.removeprefix("A")

    @property
    def as_of(self) -> date:
        return self.bas_dt

    @property
    def quality(self) -> Quality:
        """종가가 없으면 invalid(화면·계산에 쓰지 않는다)."""
        return Quality.OK if self.clpr is not None else Quality.INVALID


def ymd(d: date) -> str:
    return f"{d:%Y%m%d}"


def fetch_pages(
    datago: DatagoTransport,
    dataset_id: str,
    path: str,
    params: Mapping[str, Any],
    *,
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    """`resultType=json` 으로 쪽을 넘기며 모든 `item` 을 모은다(금융위 시세 2종 공용)."""
    out: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        resp = datago.call(
            dataset_id,
            path,
            {**params, "resultType": "json", "numOfRows": page_size, "pageNo": page},
            want="json",
        )
        items, total = resp.items()
        out.extend(items)
        # totalCount 가 없거나 0 이면 '쪽이 꽉 찼는지'로만 판단한다(ET fetch_day 교훈)
        if len(items) < page_size or (total and len(out) >= total):
            return out
    raise FscFormatError(dataset_id, 200, f"{path}: {max_pages}쪽을 넘었다 — 기간을 나눠 부른다")


def parse_checked[M: BaseModel](
    model: type[M],
    items: Iterable[Mapping[str, Any]],
    dataset_id: str,
    *,
    keep: Callable[[M], str | None],
) -> tuple[list[M], list[RowError]]:
    """행 단위로 검증하고 `keep` 이 사유를 돌려주는 행은 `RowError` 로 뺀다.

    행이 있는데 하나도 남지 않으면 `FscFormatError`(필드 이름이 바뀐 것 — 빈 날과 구분).
    """
    rows = list(items)
    kept: list[M] = []
    bad: list[RowError] = []
    for i, r in enumerate(rows):
        ok, errs = parse_rows(model, [r])
        if errs:
            bad.append(RowError(i, errs[0].error))
            continue
        why = keep(ok[0])
        if why is None:
            kept.append(ok[0])
        else:
            bad.append(RowError(i, why))
    if rows and not kept:
        first = bad[0].error[:300] if bad else ""
        raise FscFormatError(dataset_id, 200, f"{len(rows)}행 모두 형식 오류 — 첫 행: {first}")
    return kept, bad


class FscStockPriceClient:
    """금융위 주식시세(V2). 반환은 `(행, 틀린 행)` — 틀린 행을 버리지 않고 돌려준다."""

    def __init__(
        self, datago: DatagoTransport, *, page_size: int = PAGE_MAX, max_pages: int = 20
    ) -> None:
        if DATASET_ID not in datago.datasets():
            raise ValueError(f"전송층에 {DATASET_ID} 이 없다 — for_datasets 에 넣는다")
        if not 1 <= page_size <= PAGE_MAX:
            raise ValueError(f"page_size 는 1~{PAGE_MAX}")
        self._t = datago
        self._page = page_size
        self._max_pages = max_pages

    @classmethod
    def for_settings(cls, settings: Settings, redis: Redis, **kw: Any) -> FscStockPriceClient:
        """`KBJ_DATAGO_KEY` + Redis 리미터·예산(`rl:datago:<ID 해시>`·
        `budget:datago:15094808:<날짜>`)."""
        if settings.datago_key is None:
            raise DatagoKeyError(DATASET_ID, None, "KBJ_DATAGO_KEY 가 없다")
        transport = DatagoTransport.for_datasets(settings.datago_key, redis, [DATASET_ID])
        return cls(transport, **kw)

    def day(self, bas_dt: date) -> tuple[list[FscPriceRow], list[RowError]]:
        """하루치 전 종목. 휴장일이면 빈 목록(실패 아님). 기준일이 다른 행은 `RowError`."""
        items = fetch_pages(
            self._t,
            DATASET_ID,
            PATH,
            {"basDt": ymd(bas_dt)},
            page_size=self._page,
            max_pages=self._max_pages,
        )

        def keep(r: FscPriceRow) -> str | None:
            if not _CODE.fullmatch(r.code):
                return f"단축코드 형식이 아니다: {r.srtn_cd[:12]!r}"
            if r.bas_dt != bas_dt:
                return f"기준일 {r.bas_dt} 이 요청 {bas_dt} 과 다르다"
            return None

        rows, bad = parse_checked(FscPriceRow, items, DATASET_ID, keep=keep)
        return sorted(rows, key=lambda r: r.code), bad

    def ohlcv(self, code: str, start: date, end: date) -> tuple[list[FscPriceRow], list[RowError]]:
        """종목 하나의 구간 일봉(오름차순). 다른 종목(`likeSrtnCd` 는 부분 일치)과 구간 밖 행은
        조용히 거른다 — 요청한 것이 아니다. 없으면 빈 목록."""
        if not _CODE.fullmatch(code):
            raise ValueError(f"단축코드 6자리가 아니다: {code!r}")
        if start > end:
            raise ValueError(f"시작({start})이 끝({end})보다 늦다")
        items = fetch_pages(
            self._t,
            DATASET_ID,
            PATH,
            {
                "likeSrtnCd": code,
                "beginBasDt": ymd(start),
                "endBasDt": ymd(end + timedelta(days=1)),
            },
            page_size=self._page,
            max_pages=self._max_pages,
        )

        def keep(r: FscPriceRow) -> str | None:
            return None if _CODE.fullmatch(r.code) else f"단축코드 형식이 아니다: {r.srtn_cd!r}"

        rows, bad = parse_checked(FscPriceRow, items, DATASET_ID, keep=keep)
        mine = [r for r in rows if r.code == code and start <= r.bas_dt <= end]
        return sorted(mine, key=lambda r: r.bas_dt), bad
