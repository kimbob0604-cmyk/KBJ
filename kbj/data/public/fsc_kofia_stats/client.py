"""금융위원회 금융투자협회종합통계정보(공공데이터포털 15094809) — 공개 등급.

명세: `docs/probe_results.md` §3.1(포털 "제한 없음", 기술문서 공공누리 1유형 — 출처 표시).
서비스 `/1160100/service/GetKofiaStatisticsInfoService`(호스트는 `kbj.data.datago`)의 일 단위 넷:

- `credit_balance` — `getGrantingOfCreditBalanceInfo`(`15094809/credit`): 신용거래융자·대주
  (전체·유가·코스닥)·청약자금대출·예탁증권담보융자
- `market_capital` — `getSecuritiesMarketTotalCapitalInfo`(`15094809/capital`): 투자자예탁금·
  장내파생 예수금·RP·미수금·반대매매
- `fund_nav` — `getFundTotalNetEssetInfo`(`15094809/fund`): 펀드 순자산총액(유형·공모/사모)
- `cma` — `getCMAStatus`(`15094809/cma`): CMA 회사 수·계좌 수·잔고

- `resultType=json`, `numOfRows` 최대 10,000 — 다 받을 때까지 쪽을 넘긴다.
- **`endBasDt` 는 '미만'** 이다(문서). 끝 날을 포함하려고 하루 더해 부르고, 받은 행은 요청 구간
  [begin, end] 으로 다시 거른다 — '이하'로 동작해도 결과가 같다 [확인 필요: 실측 체크리스트 7].
- 공표: 적재 일 1회, 연계받은 당일 13시 이후 개방(영업일 D 값이 D+1 13시?) [실측 필요].
- 값 단위는 원으로 보인다(예시 `33337971124201` ≈ 33조) [실측 필요]. 숫자가 아니면 실패한다.
- 대문자·띄어쓰기로 적힌 오퍼레이션 이름(`GetSecuritiesMarketTotalCapitalI nfo`)은 문서 오타로
  보고 Call Back URL 표기(`getSecuritiesMarketTotalCapitalInfo`)를 쓴다 [실측 필요].
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict

from kbj.core.quality import Quality
from kbj.data.datago import DatagoFormatError, DatagoTransport, parse_amount

DATASET: Final = "15094809"
_SERVICE: Final = "/1160100/service/GetKofiaStatisticsInfoService"
PAGE_MAX: Final = 10_000  # numOfRows 최대(넘겨도 1만 건)

Op = Literal["credit", "capital", "fund", "cma"]
OPERATIONS: Final[Mapping[Op, str]] = {
    "credit": "getGrantingOfCreditBalanceInfo",
    "capital": "getSecuritiesMarketTotalCapitalInfo",
    "fund": "getFundTotalNetEssetInfo",
    "cma": "getCMAStatus",
}
# 오퍼레이션별 숫자 열(응답에 반드시 있어야 한다)과 구분 열
NUMERIC: Final[Mapping[Op, tuple[str, ...]]] = {
    "credit": (
        "crdTrFingWhl",
        "crdTrFingScrs",
        "crdTrFingKosdaq",
        "crdTrLndrWhl",
        "crdTrLndrScrs",
        "crdTrLndrKosdaq",
        "sbscCapLn",
        "dpsgScrtMogFing",
    ),
    "capital": (
        "invrDpsgAmt",
        "onbdDrvPrdTrRcAdvAmt",
        "toCstRpchCndBndSlgBal",
        "brkTrdUcolMny",
        "brkTrdUcolMnyVsOppsTrdAmt",
        "ucolMnyVsOppsTrdRlImpt",
    ),
    "fund": ("nPptTotAmt",),
    "cma": ("scrtCmpyCnt", "actCnt", "actBal"),
}
# 오퍼레이션별 대표 값 — 이 값이 비면 그 행은 invalid(화면·계산에 쓰지 않는다)
PRIMARY: Final[Mapping[Op, str]] = {
    "credit": "crdTrFingWhl",
    "capital": "invrDpsgAmt",
    "fund": "nPptTotAmt",
    "cma": "actBal",
}
LABELS: Final[Mapping[Op, tuple[str, ...]]] = {
    "credit": (),
    "capital": (),
    "fund": ("ctg", "tstMthdCtg"),
    "cma": ("mngInvTgt", "invrCtg"),
}


class KofiaFormatError(DatagoFormatError):
    """응답 열이 명세와 다르다(필수 열 없음·날짜·숫자 형식)."""


class KofiaRow(BaseModel):
    """한 기준일·한 구분의 값들. `source` = `DATAGO:15094809/<op>`, `as_of` 는 `bas_dt`."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: str
    op: Op
    bas_dt: date
    values: dict[str, Decimal | None]
    labels: dict[str, str]

    @property
    def as_of(self) -> date:
        """기준일(`basDt` — 받은 시각이 아니다)."""
        return self.bas_dt

    @property
    def quality(self) -> Quality:
        """대표 값(`PRIMARY`)이 있으면 ok, 비면 invalid."""
        return Quality.OK if self.values.get(PRIMARY[self.op]) is not None else Quality.INVALID


def _ymd(d: date) -> str:
    return f"{d:%Y%m%d}"


def _parse_bas_dt(raw: object) -> date:
    s = str(raw if raw is not None else "").strip()
    try:
        return datetime.strptime(s, "%Y%m%d").date()  # noqa: DTZ007 — 날짜만 쓴다(시각 없음)
    except ValueError:
        raise KofiaFormatError(DATASET, 200, f"basDt 형식이 틀렸다: {s[:12]!r}") from None


class KofiaStatsClient:
    """금투협 종합통계 일 단위 4종. 전송층에 포털 ID `15094809` 가 등록돼 있어야 한다."""

    def __init__(
        self, datago: DatagoTransport, *, page_size: int = PAGE_MAX, max_pages: int = 50
    ) -> None:
        if DATASET not in datago.datasets():
            raise ValueError(f"전송층에 {DATASET} 이 없다")
        if not 1 <= page_size <= PAGE_MAX:
            raise ValueError(f"page_size 는 1~{PAGE_MAX}")
        self._t = datago
        self._page = page_size
        self._max_pages = max_pages

    def credit_balance(self, begin: date, end: date) -> list[KofiaRow]:
        """신용공여잔고추이(일)."""
        return self._range("credit", begin, end)

    def market_capital(self, begin: date, end: date) -> list[KofiaRow]:
        """증시자금추이(일) — 투자자예탁금 등."""
        return self._range("capital", begin, end)

    def cma(self, begin: date, end: date) -> list[KofiaRow]:
        """일자별 CMA 현황."""
        return self._range("cma", begin, end)

    def fund_nav(self, bas_dt: date, ctg: str, tst_mthd_ctg: str) -> list[KofiaRow]:
        """펀드순자산총액 — 문서가 `basDt`·`ctg`·`tstMthdCtg` 를 필수로 적었다(기간 조회 여부
        [실측 필요]). `ctg` 예: 주식형·채권형·MMF, `tst_mthd_ctg`: 공모·사모."""
        if not ctg.strip() or not tst_mthd_ctg.strip():
            raise ValueError("ctg·tst_mthd_ctg 는 비울 수 없다")
        params = {"basDt": _ymd(bas_dt), "ctg": ctg, "tstMthdCtg": tst_mthd_ctg}
        return [r for r in self._fetch("fund", params) if r.bas_dt == bas_dt]

    # ── 공통 ────────────────────────────────────────────────────────────────────────────

    def _range(self, op: Op, begin: date, end: date) -> list[KofiaRow]:
        if begin > end:
            raise ValueError(f"시작({begin})이 끝({end})보다 늦다")
        params = {"beginBasDt": _ymd(begin), "endBasDt": _ymd(end + timedelta(days=1))}
        rows = self._fetch(op, params)
        return sorted(
            (r for r in rows if begin <= r.bas_dt <= end),
            key=lambda r: (r.bas_dt, *r.labels.values()),
        )

    def _fetch(self, op: Op, params: Mapping[str, str]) -> list[KofiaRow]:
        path = f"{_SERVICE}/{OPERATIONS[op]}"
        out: list[KofiaRow] = []
        for page in range(1, self._max_pages + 1):
            resp = self._t.call(
                DATASET,
                path,
                {**params, "resultType": "json", "numOfRows": self._page, "pageNo": page},
                want="json",
            )
            items, total = resp.items()
            out.extend(self._row(op, x) for x in items)
            # totalCount 가 없거나 0 이면 '쪽이 꽉 찼는지'로만 판단한다(ET fetch_day 교훈)
            if len(items) < self._page or (total and len(out) >= total):
                return out
        raise KofiaFormatError(
            DATASET, 200, f"{OPERATIONS[op]}: {self._max_pages}쪽을 넘었다 — 기간을 나눠 부른다"
        )

    @staticmethod
    def _row(op: Op, x: Mapping[str, Any]) -> KofiaRow:
        need = ("basDt", *NUMERIC[op], *LABELS[op])
        missing = [k for k in need if k not in x]
        if missing:
            raise KofiaFormatError(
                DATASET, 200, f"{OPERATIONS[op]}: 응답 열이 없다 — {', '.join(missing)}"
            )
        try:
            values = {k: parse_amount(x[k]) for k in NUMERIC[op]}
        except ValueError as e:
            raise KofiaFormatError(DATASET, 200, f"{OPERATIONS[op]}: {e}") from None
        return KofiaRow(
            source=f"DATAGO:{DATASET}/{op}",
            op=op,
            bas_dt=_parse_bas_dt(x["basDt"]),
            values=values,
            labels={k: str(x[k]).strip() for k in LABELS[op]},
        )
