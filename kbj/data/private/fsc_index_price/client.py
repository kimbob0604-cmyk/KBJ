"""금융위원회 지수시세정보(공공데이터포털) — 로그인 등급(공공누리 4유형, 원천 KRX).

승격 원본: ETF-Traker `board/ingest/datago.py`(`INDEX`:30, `fetch_index`:169). 설계
`docs/p2_design.md` §1.5.

- **데이터셋 ID [확인 필요]**: DATA_TIERS·probe_results 에 지수시세정보 ID 가 없다. 포털의
  '금융위원회_지수시세정보' 를 `15094807` 로 두었다(주식시세 15094808 과 같은 묶음 [추정]) — 키를
  받은 뒤 활용신청 목록에서 확인하고 고친다(ET `board/CLAUDE.md:251` — 같은 키로 지수 API 가 403
  이었던 것은 활용신청을 따로 안 했기 때문이다).
- 경로는 ET 가 쓰던 V1(`/1160100/service/GetMarketIndexInfoService/getStockMarketIndex`)이다.
  주식시세가 V2 로 옮겨 갔듯(F7) 지수도 V2 가 있는지 [실측 필요].
- 전송·키·리미터·예산·게이트웨이 오류는 `kbj.data.datago.DatagoTransport`(묶음 C). 쪽 넘기기·행
  격리는 주식시세와 같은 도우미(`fsc_stock_price.client.fetch_pages`·`parse_checked`)를 쓴다.
- **`endBasDt` 는 '미만'**(금융위 공통 문서 표기) — 하루 더해 부르고 [start, end] 로 다시 거른다
  [확인 필요].
- ET 와 다른 점: 거래량을 못 받으면 0 이 아니라 None 이다(ET `num(pick(x, 'trqu'), 0.0)` — D-019
  위반). 빈 결과는 빈 목록이다(ET 는 '지수 없음' 으로 실패했다 — 부르는 쪽이 판단한다).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field
from redis import Redis

from kbj.config.settings import Settings
from kbj.core.quality import Quality
from kbj.data.datago import DatagoKeyError, DatagoTransport
from kbj.data.private.fsc_stock_price.client import fetch_pages, parse_checked, ymd
from kbj.data.private.krx.models import Num, OptText, Qty, RowError, Text, Won, Ymd

DATASET_ID: Final = "15094807"  # [확인 필요] — 머리말
SOURCE: Final = f"DATAGO:{DATASET_ID}"
PATH: Final = "/1160100/service/GetMarketIndexInfoService/getStockMarketIndex"
PAGE_MAX: Final = 1_000


class FscIndexRow(BaseModel):
    """지수 시세 한 행. `as_of` = `bas_dt`. 필드 이름은 ET 가 쓰던 이름 + 문서 [실측 필요]."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=False)

    source: str = SOURCE
    bas_dt: Ymd = Field(validation_alias="basDt")
    idx_nm: Text = Field(validation_alias="idxNm")  # 예: 코스피, 코스닥
    idx_csf: OptText = Field(default=None, validation_alias="idxCsf")  # 지수 분류
    clpr: Num = Field(default=None, validation_alias="clpr")
    vs: Num = Field(default=None, validation_alias="vs")
    flt_rt: Num = Field(default=None, validation_alias="fltRt")
    mkp: Num = Field(default=None, validation_alias="mkp")
    hipr: Num = Field(default=None, validation_alias="hipr")
    lopr: Num = Field(default=None, validation_alias="lopr")
    trqu: Qty = Field(default=None, validation_alias="trqu")
    tr_prc: Won = Field(default=None, validation_alias="trPrc")  # 거래대금(원)
    lstg_mrkt_tot_amt: Won = Field(default=None, validation_alias="lstgMrktTotAmt")

    @property
    def as_of(self) -> date:
        return self.bas_dt

    @property
    def quality(self) -> Quality:
        return Quality.OK if self.clpr is not None else Quality.INVALID


class FscIndexPriceClient:
    """금융위 지수시세. 반환은 `(행, 틀린 행)`."""

    def __init__(
        self, datago: DatagoTransport, *, page_size: int = PAGE_MAX, max_pages: int = 20
    ) -> None:
        if DATASET_ID not in datago.datasets():
            raise ValueError(f"전송층에 {DATASET_ID} 이 없다 — for_datasets 에 넣는다")
        if not 1 <= page_size <= 10_000:
            raise ValueError("page_size 는 1~10000")
        self._t = datago
        self._page = page_size
        self._max_pages = max_pages

    @classmethod
    def for_settings(cls, settings: Settings, redis: Redis, **kw: Any) -> FscIndexPriceClient:
        """`KBJ_DATAGO_KEY` + Redis 리미터·예산(포털 ID 단위)."""
        if settings.datago_key is None:
            raise DatagoKeyError(DATASET_ID, None, "KBJ_DATAGO_KEY 가 없다")
        transport = DatagoTransport.for_datasets(settings.datago_key, redis, [DATASET_ID])
        return cls(transport, **kw)

    def index(self, name: str, start: date, end: date) -> tuple[list[FscIndexRow], list[RowError]]:
        """지수 하나(`name` 예: '코스피')의 구간 일별(오름차순). 이름이 다른 행·구간 밖 행은
        거른다(`idxNm` 이 부분 일치일 수 있다 [실측 필요])."""
        if not name.strip():
            raise ValueError("지수 이름이 비었다")
        if start > end:
            raise ValueError(f"시작({start})이 끝({end})보다 늦다")
        items = fetch_pages(
            self._t,
            DATASET_ID,
            PATH,
            {
                "idxNm": name,
                "beginBasDt": ymd(start),
                "endBasDt": ymd(end + timedelta(days=1)),
            },
            page_size=self._page,
            max_pages=self._max_pages,
        )
        rows, bad = parse_checked(FscIndexRow, items, DATASET_ID, keep=lambda _: None)
        mine = [r for r in rows if r.idx_nm == name.strip() and start <= r.bas_dt <= end]
        return sorted(mine, key=lambda r: r.bas_dt), bad
