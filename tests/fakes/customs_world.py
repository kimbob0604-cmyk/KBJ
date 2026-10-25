"""가짜 관세청(공공데이터포털 관세청 가지) — 합성 세계 `p6_world` 에서 **파라미터대로** XML.

근거: docs/p6_design.md §1.2(묶음 COL)·§0.3(하루 운영 시뮬레이션 ①~⑥). 실데이터 아님 — 값은 전부
`tests/fixtures/synthetic/p6_world.py`(고정 시드)의 합성 값이다.

- 경로 끝(오퍼레이션)으로 고른다: 월간 4종(`getItemtradeList`·`getNationtradeList`·
  `getNitemtradeList`·`getSigunguPerPrlstPerAcrs`)·10일 잠정치 4종(`getPrlstMmUtPrviExpAcrs` …).
- 파라미터: 기간(`strtYymm`~`endYymm`), `hsSgn`(품목 접두)·`cntyCd`(국가)·`sidoCd`·`HsSgn`(시군구
  HS6). 15100475 는 국가 필수·12개월 이내(아니면 기관 오류 `99`), 없는 행은 `03`(데이터 없음).
- **공표 시각**: 10일 구간은 기본 그 구간 다음 날(1~10일 → 11일, 1~20일 → 21일, 말일 → 다음 달
  1일) 10:47 KST 에 열린다(주말이어도 — 공표 규칙 미상 #55). `delay_ten(ym, seg, at)` 로 바꾼다(늦은
  공표·주말 공표). 월간은 다음 달 15일 10:00(`delay_month`). 공표 전 달·구간은 응답에 없다.
- **정정**: 세계의 정정 칸(`revised_at` 이 있는 칸)은 그 시각 전에는 발표 당시 값(`first_usd`)을,
  뒤에는 정정 값(`usd`)을 준다.
- **주입**: `change_columns(kind)` — 그 종류 10일 응답에 `itemUsdAmt11` 열을 더한다(열 구성 변경 —
  어댑터가 `TenDayColumnsChanged`), `truncate(op, n)` — 다음 n 번 응답의 `totalCount` 를 행 수보다
  크게(잘림 — `CustomsTruncated`).
- 숫자 함정: 15101609 `hsCode` 는 숫자형처럼 앞자리 0 을 뗀다(`0302` → `302` — 어댑터가 되살린다),
  10일 금액은 천 달러 + 앞 공백·쉼표(`" 13,886,115"`), 시군구 금액은 쉼표 문자열.
- 수집기 설정 도우미: `trade_map()`(세계 매핑표의 지역 행 → `TradeMap`), `trade_regions()`.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Final
from xml.sax.saxutils import escape

from kbj.config.trade import TradeMap, TradeRegions, parse_trade_map, parse_trade_regions
from kbj.core.time import KST, to_kst
from kbj.core.trade_rows import TenDayRow, prev_ym
from tests.fixtures.synthetic.p6_world import P6World, build_world

TEN_OPS: Final[Mapping[str, str]] = {
    "getPrlstMmUtPrviExpAcrs": "exp_item",
    "getCntyMmUtPrviExpAcrs": "exp_country",
    "getPrlstMmUtPrviImpAcrs": "imp_item",
    "getCntyMmUtPrviImpAcrs": "imp_country",
}
MONTH_OPS: Final[Mapping[str, str]] = {
    "getItemtradeList": "items",
    "getNationtradeList": "countries",
    "getNitemtradeList": "item_country",
    "getSigunguPerPrlstPerAcrs": "sigungu",
}
OPS: Final = frozenset({*TEN_OPS, *MONTH_OPS})
TEN_PUBLISH: Final = time(10, 47)
MONTH_PUBLISH_DAY: Final = 15
MONTH_PUBLISH: Final = time(10, 0)
_HEAD: Final = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    "<!-- SYNTHETIC — tests/fakes/customs_world.py(p6_world 합성 세계) -->\n"
)


def _ym(flat: str) -> str:
    return f"{flat[:4]}-{flat[4:]}"


def _in(ym: str, lo: str, hi: str) -> bool:
    return lo <= ym <= hi


def _comma(v: Decimal | int | None) -> str:
    return "" if v is None else f"{int(v):,}"


def _plain(v: Decimal | int | None) -> str:
    return "" if v is None else str(int(v))


def _comma_thousand(v: Decimal | int | None) -> str:
    """시군구(15134343) 금액 — 실제처럼 천 달러. 세계 값은 1000 의 배수다
    (`p6_world._make_sigungu`)."""
    if v is None:
        return ""
    if int(v) % 1000:
        raise ValueError(f"시군구 합성 값은 1000 달러 단위여야 한다: {v}")
    return f"{int(v) // 1000:,}"


def _dotted(ym: str) -> str:
    return ym.replace("-", ".")


def _xml(items: Sequence[Mapping[str, str]], *, total: int | None = None) -> str:
    if not items:
        return (
            _HEAD + "<response><header><resultCode>03</resultCode>"
            "<resultMsg>NODATA_ERROR</resultMsg></header></response>"
        )
    body = "".join(
        "<item>" + "".join(f"<{k}>{escape(v)}</{k}>" for k, v in it.items()) + "</item>"
        for it in items
    )
    n = len(items) if total is None else total
    return (
        _HEAD + "<response><header><resultCode>00</resultCode><resultMsg>정상 서비스.</resultMsg>"
        f"</header><body><items>{body}</items><totalCount>{n}</totalCount></body></response>"
    )


def _agency(code: str, msg: str) -> str:
    return (
        _HEAD + f"<response><header><resultCode>{code}</resultCode><resultMsg>{escape(msg)}"
        "</resultMsg></header></response>"
    )


class CustomsWorld:
    """`p6_world` 를 관세청 응답으로 — `FakeDatago(customs=CustomsWorld())` 가 부른다."""

    def __init__(self, world: P6World | None = None) -> None:
        self.world = world or build_world()
        self._ten_at: dict[tuple[str, int], datetime] = {}
        self._month_at: dict[str, datetime] = {}
        self._extra_cols: set[str] = set()
        self._truncate: dict[str, int] = {}
        self._lock = threading.Lock()
        self._names = {h.hs: h.name_ko for h in self.world.hs}
        self._ten = self.world.ten_day

    # ── 공표 시각 ─────────────────────────────────────────────────────────────────────────

    def ten_publish_at(self, ym: str, seg: int) -> datetime:
        """그 10일 구간(ym `YYYY-MM`)이 포털에 열리는 시각(KST)."""
        got = self._ten_at.get((ym, seg))
        if got is not None:
            return got
        y, m = int(ym[:4]), int(ym[5:])
        if seg == 3:
            nxt = prev_ym(ym, -1)
            return datetime.combine(date(int(nxt[:4]), int(nxt[5:]), 1), TEN_PUBLISH, tzinfo=KST)
        return datetime(y, m, 11 if seg == 1 else 21, TEN_PUBLISH.hour, TEN_PUBLISH.minute,
                        tzinfo=KST)  # fmt: skip

    def month_publish_at(self, ym: str) -> datetime:
        got = self._month_at.get(ym)
        if got is not None:
            return got
        nxt = prev_ym(ym, -1)
        return datetime(int(nxt[:4]), int(nxt[5:]), MONTH_PUBLISH_DAY, MONTH_PUBLISH.hour,
                        tzinfo=KST)  # fmt: skip

    def delay_ten(self, ym: str, seg: int, at: datetime) -> None:
        """10일 구간 공표 시각을 바꾼다(늦은 공표·주말 공표)."""
        self._ten_at[(ym, seg)] = at

    def delay_month(self, ym: str, at: datetime) -> None:
        self._month_at[ym] = at

    # ── 주입 ─────────────────────────────────────────────────────────────────────────────

    def change_columns(self, kind: str, on: bool = True) -> None:
        """그 종류 10일 응답에 열(`itemUsdAmt11`)을 더한다 — 열 구성 변경."""
        with self._lock:
            (self._extra_cols.add if on else self._extra_cols.discard)(kind)

    def truncate(self, op: str, count: int = 1) -> None:
        """다음 count 번 그 오퍼레이션 응답의 totalCount 를 행보다 크게(응답 잘림)."""
        with self._lock:
            self._truncate[op] = self._truncate.get(op, 0) + count

    # ── 응답 ─────────────────────────────────────────────────────────────────────────────

    def respond(self, op: str, params: Mapping[str, str], now: datetime) -> str:
        """경로 끝·파라미터·지금 시각 → XML 본문."""
        lo_flat, hi_flat = params.get("strtYymm", ""), params.get("endYymm", "")
        if len(lo_flat) != 6 or len(hi_flat) != 6 or lo_flat > hi_flat:
            return _agency("99", "조회기간을 확인하세요")
        lo, hi = _ym(lo_flat), _ym(hi_flat)
        k = to_kst(now)
        if op in TEN_OPS:
            items = self._ten_items(TEN_OPS[op], lo, hi, k)
        elif op == "getItemtradeList":
            items = self._items(lo, hi, params.get("hsSgn"), k)
        elif op == "getNationtradeList":
            items = self._countries(lo, hi, params.get("cntyCd"), k)
        elif op == "getNitemtradeList":
            cc = params.get("cntyCd")
            months = (int(hi[:4]) - int(lo[:4])) * 12 + int(hi[5:]) - int(lo[5:]) + 1
            if not cc:
                return _agency("99", "국가코드는 필수입니다")
            if months > 12:
                return _agency("99", "조회기간은 1년 이내입니다")
            items = self._item_country(lo, hi, cc, params.get("hsSgn"), k)
        elif op == "getSigunguPerPrlstPerAcrs":
            items = self._sigungu(lo, hi, params.get("sidoCd", ""), params.get("HsSgn", ""), k)
        else:
            return _agency("12", "NO_OPENAPI_SERVICE_ERROR")
        total = None
        with self._lock:
            if items and self._truncate.get(op, 0) > 0:
                self._truncate[op] -= 1
                total = len(items) + 1
        return _xml(items, total=total)

    def _month_open(self, ym: str, k: datetime) -> bool:
        return k >= self.month_publish_at(ym)

    def _ten_items(self, kind: str, lo: str, hi: str, k: datetime) -> list[dict[str, str]]:
        by: dict[tuple[str, int], list[TenDayRow]] = {}
        for r in self._ten:
            if r.kind == kind and _in(r.ym, lo, hi):
                by.setdefault((r.ym, r.seg), []).append(r)
        out: list[dict[str, str]] = []
        for (ym, seg), rows in sorted(by.items()):
            if k < self.ten_publish_at(ym, seg):
                continue
            it: dict[str, str] = {
                "priodDt": rows[0].span_text,
                "priodMon": ym.replace("-", ""),
                "priodYear": ym[:4],
            }
            for r in sorted(rows, key=lambda x: x.col_no):
                v = r.usd
                if r.revised_at is not None and k < r.revised_at:
                    v = r.first_usd
                it[f"itemUsdAmt{r.col_no:02d}"] = (
                    "" if v is None else " " + _comma(Decimal(v) / 1000)
                )
            if kind in self._extra_cols:
                it["itemUsdAmt11"] = " 1,000"
            out.append(it)
        return out

    def _items(self, lo: str, hi: str, hs: str | None, k: datetime) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in self.world.items:
            if not _in(r.ym, lo, hi) or not self._month_open(r.ym, k):
                continue
            if hs and not r.hs.startswith(hs):
                continue
            out.append(
                {
                    "balPayments": _plain(r.bal_usd),
                    "expDlr": _plain(r.exp_usd),
                    "expWgt": _plain(r.exp_kg),
                    "hsCode": r.hs.lstrip("0") or "0",  # 숫자형 — 앞자리 0 손실 함정
                    "impDlr": _plain(r.imp_usd),
                    "impWgt": _plain(r.imp_kg),
                    "statKor": self._names.get(r.hs, f"가상품목 {r.hs}"),
                    "year": _dotted(r.ym),
                }
            )
        return out

    def _countries(self, lo: str, hi: str, cc: str | None, k: datetime) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in self.world.countries:
            if not _in(r.ym, lo, hi) or not self._month_open(r.ym, k):
                continue
            if cc and r.country != cc.upper():
                continue
            out.append(
                {
                    "balPayments": _plain(r.bal_usd),
                    "expCnt": _plain(r.exp_cnt),
                    "expDlr": _plain(r.exp_usd),
                    "impCnt": _plain(r.imp_cnt),
                    "impDlr": _plain(r.imp_usd),
                    "statCd": r.country,
                    "statCdCntnKor1": r.country_name,
                    "year": _dotted(r.ym),
                }
            )
        return out

    def _item_country(
        self, lo: str, hi: str, cc: str, hs: str | None, k: datetime
    ) -> list[dict[str, str]]:
        names = {r.country: r.country_name for r in self.world.countries}
        out: list[dict[str, str]] = []
        for r in self.world.item_countries:
            if r.country != cc.upper() or not _in(r.ym, lo, hi) or not self._month_open(r.ym, k):
                continue
            if hs and not r.hs.startswith(hs):
                continue
            out.append(
                {
                    "balPayments": _plain(r.bal_usd),
                    "expDlr": _plain(r.exp_usd),
                    "expWgt": _plain(r.exp_kg),
                    "hsCd": r.hs,
                    "impDlr": _plain(r.imp_usd),
                    "impWgt": _plain(r.imp_kg),
                    "statCd": r.country,
                    "statCdCntnKor1": names.get(r.country, r.country),
                    "statKor": self._names.get(r.hs, f"가상품목 {r.hs}"),
                    "year": _dotted(r.ym),
                }
            )
        return out

    def _sigungu(self, lo: str, hi: str, sido: str, hs6: str, k: datetime) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for r in self.world.sigungu:
            if r.sido_cd != sido or r.hs6 != hs6:
                continue
            if not _in(r.ym, lo, hi) or not self._month_open(r.ym, k):
                continue
            out.append(
                {
                    "cmtrBlncAmt": _comma_thousand(r.bal_usd),
                    "expCnt": _comma(r.exp_cnt),
                    "expUsdAmt": _comma_thousand(r.exp_usd),
                    "hsSgn": r.hs6,
                    "impCnt": _comma(r.imp_cnt),
                    "impUsdAmt": _comma_thousand(r.imp_usd),
                    "korePrlstNm": self._names.get(r.hs6, f"가상품목 {r.hs6}"),
                    "priodTitle": _dotted(r.ym),
                    "sggNm": r.sgg_nm,
                }
            )
        return out

    # ── 수집기 설정 도우미 ─────────────────────────────────────────────────────────────────

    def trade_map(self, origins: Iterable[str] = ("public", "local")) -> TradeMap:
        """세계 매핑표의 **지역 행** 만 담은 `TradeMap`(시군구 수집 대상 HS6 를 정한다)."""
        stocks: dict[str, dict[str, Any]] = {}
        for r in self.world.stock_map:
            if r.origin not in origins or not r.region_id:
                continue
            s = stocks.setdefault(
                r.code,
                {
                    "code": r.code,
                    "name": r.name,
                    "reviewed": False,
                    "regions": [],
                    "evidence": [{"kind": "synthetic", "note": "합성 근거(실측 아님)"}],
                },
            )
            s["regions"].append({"region_id": r.region_id, "hs6": r.hs, "weight": r.weight})
        return parse_trade_map({"version": 1, "stocks": list(stocks.values())})

    def trade_regions(self) -> TradeRegions:
        return parse_trade_regions(
            {
                "version": 1,
                "regions": [
                    {
                        "id": g.id,
                        "sido_cd": g.sido_cd,
                        "sgg_nm": g.sgg_nm,
                        "valid_from": g.valid_from,
                        "valid_to": g.valid_to,
                    }
                    for g in self.world.regions
                ],
            }
        )


__all__ = ["MONTH_OPS", "OPS", "TEN_OPS", "CustomsWorld"]
