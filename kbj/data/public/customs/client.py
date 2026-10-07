"""관세청 수출입 클라이언트(공공데이터포털) — 공개 등급(DATA_TIERS §1, probe_results §2).

| 메서드 | 포털 ID | 필수 | 기간 한도 |
|---|---|---|---|
| `item_country` | 15100475 | 기간·**국가** | **12개월** |
| `items` | 15101609 | 기간 | [실측 필요] |
| `countries` | 15101612 | 기간 | [실측 필요] |
| `sigungu` | 15134343 | 기간·**시도·HS6** | [실측 필요] |
| `ten_day` | 15157908·941·901·909 | 기간 | **120개월** |

오퍼레이션 경로는 `PATHS`(호스트는 `kbj.data.datago` 한 곳).

- 관세청은 **XML 만** 준다(JSON 없음). 전송·키·한도·게이트웨이 오류는 `kbj.data.datago`.
- 페이징 파라미터가 없다 — `totalCount` 가 받은 행 수보다 크면 응답이 잘린 것으로 보고 실패한다
  (`CustomsTruncated` — 기간·품목을 나눠 다시 부른다) [실측 필요: 잘림 여부].
- 기간 한도·코드 형식은 **부르기 전에** 검사한다(관세청은 `99` 로 거절하지만 예산 한 몫을
  쓴다).
- 응답 열이 명세와 다르면(필수 열 없음·값 형식) `CustomsFormatError` — 조용히 버리지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from kbj.data.datago import DatagoFormatError, DatagoTransport, parse_amount
from kbj.data.public.customs.models import (
    SIDO_CODES,
    TEN_DAY_KINDS,
    CustomsFormatError,
    SigunguRow,
    TenDayKind,
    TenDayRow,
    TradeRow,
    normalize_hs,
    parse_period,
    ten_day_amount_columns,
    ten_day_segment,
)

ITEM_COUNTRY: Final = "15100475"
ITEMS: Final = "15101609"
COUNTRIES: Final = "15101612"
SIGUNGU: Final = "15134343"
TEN_DAY: Final[Mapping[TenDayKind, str]] = {
    "exp_item": "15157908",
    "exp_country": "15157941",
    "imp_item": "15157901",
    "imp_country": "15157909",
}
DATASET_IDS: Final[tuple[str, ...]] = (
    ITEM_COUNTRY,
    ITEMS,
    COUNTRIES,
    SIGUNGU,
    *(TEN_DAY[k] for k in TEN_DAY_KINDS),
)

# 경로(호스트 없음 — 호스트는 kbj/data/datago.py 한 곳)
PATHS: Final[Mapping[str, str]] = {
    ITEM_COUNTRY: "/1220000/nitemtrade/getNitemtradeList",
    ITEMS: "/1220000/Itemtrade/getItemtradeList",
    COUNTRIES: "/1220000/nationtrade/getNationtradeList",
    SIGUNGU: "/1220000/sigunguperprlstperacrs/getSigunguPerPrlstPerAcrs",
    "15157908": "/1220000/prlstMmUtPrviExpAcrs/getPrlstMmUtPrviExpAcrs",
    "15157941": "/1220000/cntyMmUtPrviExpAcrs/getCntyMmUtPrviExpAcrs",
    "15157901": "/1220000/prlstMmUtPrviImpAcrs/getPrlstMmUtPrviImpAcrs",
    "15157909": "/1220000/cntyMmUtPrviImpAcrs/getCntyMmUtPrviImpAcrs",
}

ITEM_COUNTRY_MAX_MONTHS: Final = 12  # "조회기간 1년 이내"(DOC:15100475 2-2)
TEN_DAY_MAX_MONTHS: Final = 120  # "조회기간 10년 이내"(15157909 는 같은 계열 기준 [실측 필요])


class CustomsTruncated(DatagoFormatError):
    """`totalCount` 가 받은 행보다 많다 — 페이징이 없어 잘렸다. 기간·품목을 나눠 다시."""


def month_index(ym: str) -> int:
    """`YYYYMM` → 연×12+월(검사 포함)."""
    if len(ym) != 6 or not ym.isdigit() or not 1 <= int(ym[4:]) <= 12:
        raise ValueError(f"기간은 YYYYMM 이어야 한다: {ym!r}")
    return int(ym[:4]) * 12 + int(ym[4:]) - 1


def months_between(start_ym: str, end_ym: str) -> int:
    """양 끝 포함 개월 수. 시작이 끝보다 늦으면 `ValueError`."""
    n = month_index(end_ym) - month_index(start_ym) + 1
    if n < 1:
        raise ValueError(f"시작({start_ym})이 끝({end_ym})보다 늦다")
    return n


def _hs_param(hs: str | None, *, exact: int | None = None) -> str | None:
    if hs is None:
        return None
    if not hs.isdigit() or not 2 <= len(hs) <= 10:
        raise ValueError(f"HS 코드는 숫자 2~10자리: {hs!r}")
    if exact is not None and len(hs) != exact:
        raise ValueError(f"HS 코드는 {exact}자리여야 한다: {hs!r}")
    return hs


def _country_param(country: str) -> str:
    if len(country) != 2 or not country.isascii() or not country.isalpha():
        raise ValueError(f"국가 코드는 ISO 2자리 영문: {country!r}")
    return country.upper()


class CustomsClient:
    """관세청 수출입 5종(+10일 잠정치 4종). 전송층은 `DatagoTransport`(데이터셋 ID 8개 등록)."""

    def __init__(self, datago: DatagoTransport) -> None:
        missing = [d for d in DATASET_IDS if d not in datago.datasets()]
        if missing:
            raise ValueError(f"전송층에 관세청 데이터셋이 없다: {', '.join(missing)}")
        self._t = datago

    # ── 월간 ────────────────────────────────────────────────────────────────────────────

    def item_country(
        self, start_ym: str, end_ym: str, country: str, hs: str | None = None
    ) -> list[TradeRow]:
        """품목별 국가별(15100475). 기간 12개월 이내, 국가 필수."""
        n = months_between(start_ym, end_ym)
        if n > ITEM_COUNTRY_MAX_MONTHS:
            raise ValueError(f"15100475 조회기간은 12개월 이내다({n}개월)")
        params = {"strtYymm": start_ym, "endYymm": end_ym, "cntyCd": _country_param(country)}
        if (h := _hs_param(hs)) is not None:
            params["hsSgn"] = h
        rows = self._rows(ITEM_COUNTRY, params)
        return [
            self._trade(
                ITEM_COUNTRY,
                r,
                need=(
                    "year",
                    "statCd",
                    "statCdCntnKor1",
                    "hsCd",
                    "statKor",
                    "expWgt",
                    "expDlr",
                    "impWgt",
                    "impDlr",
                    "balPayments",
                ),
                hs_key="hsCd",
            )
            for r in rows
        ]

    def items(self, start_ym: str, end_ym: str, hs: str | None = None) -> list[TradeRow]:
        """품목별(15101609). `hsCode` 앞자리 0 을 되살린다(`normalize_hs`)."""
        months_between(start_ym, end_ym)
        params = {"strtYymm": start_ym, "endYymm": end_ym}
        if (h := _hs_param(hs)) is not None:
            params["hsSgn"] = h
        rows = self._rows(ITEMS, params)
        need = ("year", "hsCode", "statKor", "expWgt", "expDlr", "impWgt", "impDlr", "balPayments")
        return [self._trade(ITEMS, r, need=need, hs_key="hsCode") for r in rows]

    def countries(self, start_ym: str, end_ym: str, country: str | None = None) -> list[TradeRow]:
        """국가별(15101612). 중량 대신 건수(`expCnt`·`impCnt`)."""
        months_between(start_ym, end_ym)
        params = {"strtYymm": start_ym, "endYymm": end_ym}
        if country is not None:
            params["cntyCd"] = _country_param(country)
        rows = self._rows(COUNTRIES, params)
        need = (
            "year",
            "statCd",
            "statCdCntnKor1",
            "expCnt",
            "expDlr",
            "impCnt",
            "impDlr",
            "balPayments",
        )
        return [self._trade(COUNTRIES, r, need=need, hs_key=None) for r in rows]

    def sigungu(self, start_ym: str, end_ym: str, sido: str, hs6: str) -> list[SigunguRow]:
        """시군구별 품목별(15134343). 시도 × HS6 한 쌍씩(probe_results F3).

        시도 코드는 신고 기간에 맞아야 한다 — 2026-07 개편을 걸치는 기간은 개편 전후로 나눠
        부른다(`SIDO_CODES`).
        """
        months_between(start_ym, end_ym)
        code = SIDO_CODES.get(sido)
        if code is None:
            raise ValueError(f"모르는 시도 코드: {sido!r}")
        if not (code.valid_in(start_ym) and code.valid_in(end_ym)):
            raise ValueError(
                f"시도 {sido}({code.name})는 {code.valid_from or '처음'}~{code.valid_to or '지금'} "
                f"신고분에만 쓴다 — {start_ym}~{end_ym} 은 2026-07 개편 전후로 나눠 부른다"
            )
        params = {
            "strtYymm": start_ym,
            "endYymm": end_ym,
            "HsSgn": _hs_param(hs6, exact=6),
            "sidoCd": sido,
        }
        rows = self._rows(SIGUNGU, params)
        need = (
            "priodTitle",
            "sggNm",
            "hsSgn",
            "korePrlstNm",
            "expCnt",
            "expUsdAmt",
            "impCnt",
            "impUsdAmt",
            "cmtrBlncAmt",
        )
        out: list[SigunguRow] = []
        for r in rows:
            _need(SIGUNGU, r, need)
            try:
                out.append(
                    SigunguRow(
                        source=f"DATAGO:{SIGUNGU}",
                        period=parse_period(r["priodTitle"]),
                        sido=sido,
                        sigungu_name=str(r["sggNm"]).strip(),
                        hs6=normalize_hs(r["hsSgn"]),
                        item_name=_text(r["korePrlstNm"]),
                        exp_count=parse_amount(r["expCnt"]),
                        exp_usd=parse_amount(r["expUsdAmt"]),
                        imp_count=parse_amount(r["impCnt"]),
                        imp_usd=parse_amount(r["impUsdAmt"]),
                        balance_usd=parse_amount(r["cmtrBlncAmt"]),
                    )
                )
            except ValueError as e:
                raise CustomsFormatError(f"{SIGUNGU}: {e}") from None
        return out

    # ── 10일 단위 잠정치 ──────────────────────────────────────────────────────────────────

    def ten_day(self, kind: TenDayKind, start_ym: str, end_ym: str) -> list[TenDayRow]:
        """10일 단위 잠정치(천 달러 누계). 기간 120개월 이내. 열 구성이 바뀌면 실패."""
        if kind not in TEN_DAY:
            raise ValueError(f"10일 잠정치 종류는 {', '.join(TEN_DAY_KINDS)} 중 하나: {kind!r}")
        n = months_between(start_ym, end_ym)
        if n > TEN_DAY_MAX_MONTHS:
            raise ValueError(f"10일 잠정치 조회기간은 120개월 이내다({n}개월)")
        dataset = TEN_DAY[kind]
        rows = self._rows(dataset, {"strtYymm": start_ym, "endYymm": end_ym})
        out: list[TenDayRow] = []
        for r in rows:
            _need(dataset, r, ("priodYear", "priodMon", "priodDt"))
            cols = ten_day_amount_columns(r)
            month = str(r["priodMon"]).strip()
            span = str(r["priodDt"]).strip()
            try:
                month_index(month)
                if str(r["priodYear"]).strip() != month[:4]:
                    raise ValueError(f"priodYear 와 priodMon 이 다르다: {month}")
                amounts = {c: parse_amount(r[f"itemUsdAmt{c}"]) for c in cols}
            except ValueError as e:
                raise CustomsFormatError(f"{dataset}: {e}") from None
            out.append(
                TenDayRow(
                    source=f"DATAGO:{dataset}",
                    kind=kind,
                    month=month,
                    span=span,
                    segment=ten_day_segment(span),
                    amounts=amounts,
                )
            )
        return out

    # ── 공통 ────────────────────────────────────────────────────────────────────────────

    def _rows(self, dataset: str, params: Mapping[str, str | None]) -> list[dict[str, str]]:
        sent = {k: v for k, v in params.items() if v is not None}
        resp = self._t.call(dataset, PATHS[dataset], sent, want="xml")
        rows, total = resp.items()
        if total is not None and total > len(rows):
            raise CustomsTruncated(
                dataset,
                resp.status,
                f"totalCount {total} > 받은 행 {len(rows)} — 응답이 잘렸다(페이징 없음). "
                "기간·품목을 나눠 다시 부른다",
            )
        return [{k: str(v) for k, v in r.items()} for r in rows]

    @staticmethod
    def _trade(
        dataset: str, r: Mapping[str, str], *, need: Sequence[str], hs_key: str | None
    ) -> TradeRow:
        _need(dataset, r, need)
        try:
            return TradeRow(
                source=f"DATAGO:{dataset}",
                period=parse_period(r["year"]),
                country=_text(r.get("statCd")),
                country_name=_text(r.get("statCdCntnKor1")),
                hs=normalize_hs(r[hs_key]) if hs_key is not None else None,
                item_name=_text(r.get("statKor")),
                exp_weight_kg=parse_amount(r.get("expWgt")),
                exp_usd=parse_amount(r["expDlr"]),
                imp_weight_kg=parse_amount(r.get("impWgt")),
                imp_usd=parse_amount(r["impDlr"]),
                exp_count=parse_amount(r.get("expCnt")),
                imp_count=parse_amount(r.get("impCnt")),
                balance_usd=parse_amount(r["balPayments"]),
            )
        except ValueError as e:
            raise CustomsFormatError(f"{dataset}: {e}") from None


def _need(dataset: str, row: Mapping[str, object], keys: Sequence[str]) -> None:
    missing = [k for k in keys if k not in row]
    if missing:
        raise CustomsFormatError(f"{dataset}: 응답 열이 없다 — {', '.join(missing)}")


def _text(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None
