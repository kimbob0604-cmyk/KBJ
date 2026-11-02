"""글로벌 프록시 — 한국 장이 닫힌 동안 한국 주식을 비춰 보는 해외 가격(stock-dashboard 화면을 옮김).

받아 둔 시세(`prv_market.global_quote` — 작업 global.proxy)와 국내 종가로 화면 값을 만든다
(순수 계산).

- 24시간 선물: Hyperliquid(xyz dex) 무기한 선물. 가격은 달러(코스피200 은 지수 포인트). 직전 국내
  정규장 종가와 견준다(환율은 같은 묶음의 USD/KRW). 펀딩률은 1시간 값 × 8 = 8시간 기준(%).
- 해외 상장 괴리율: SKHY(1 ADS = 보통주 1/10주), SMSN.IL(1 GDR = 25주), HY9H.F(1주, 유로).
  국내 종가를 그 통화로 환산한 값 대비. 비율은 발행사 공시로 확인한 값(2026-10-30) — 여기 한 곳만.
- 지수선물·매크로·미국 ETF: 직전 세션 종가 대비.
- 값이 없으면 None + 사유(지어내지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from kbj.core.rows import GlobalQuote

__all__ = [
    "FUTURES",
    "FX",
    "LISTINGS",
    "MACRO",
    "PERPS",
    "US_ETFS",
    "ProxyRow",
    "ProxyView",
    "build",
    "yahoo_symbols",
]

RefKind = Literal["stock", "index", "yahoo"]
# (Hyperliquid 이름, 화면 이름, (비교 대상 종류, 코드))
PERPS: Final[tuple[tuple[str, str, tuple[RefKind, str]], ...]] = (
    ("xyz:SMSN", "삼성전자", ("stock", "005930")),
    ("xyz:SKHX", "SK하이닉스", ("stock", "000660")),
    ("xyz:SKHY", "SK하이닉스 ADR", ("yahoo", "SKHY")),
    ("xyz:HYUNDAI", "현대차", ("stock", "005380")),
    ("xyz:KR200", "코스피200", ("index", "KOSPI200")),
)
# (야후 심볼, 화면 이름, 국내 코드, 1 증서 = 보통주 몇 주, 통화)
LISTINGS: Final[tuple[tuple[str, str, str, float, str], ...]] = (
    ("SKHY", "SK하이닉스 나스닥 ADR", "000660", 0.1, "USD"),
    ("SMSN.IL", "삼성전자 런던 GDR", "005930", 25.0, "USD"),
    ("HY9H.F", "SK하이닉스 프랑크푸르트", "000660", 1.0, "EUR"),
)
FUTURES: Final = (("NQ=F", "나스닥100 선물", "pt"), ("ES=F", "S&P500 선물", "pt"))
MACRO: Final = (("^TNX", "미국 10년물", "%"), ("CL=F", "WTI 원유", "$"), ("KRW=X", "USD/KRW", "원"))
US_ETFS: Final = (
    ("DRAM", "DRAM · 메모리", "$"),
    ("EWY", "EWY · 한국", "$"),
    ("SOXX", "SOXX · 반도체", "$"),
)
FX: Final = ("KRW=X", "EURUSD=X")


def yahoo_symbols() -> tuple[str, ...]:
    """global.proxy 가 야후에서 받는 심볼 전부(정렬)."""
    syms = {s for s, *_ in LISTINGS} | {s for s, *_ in FUTURES + MACRO + US_ETFS} | set(FX)
    syms |= {ref for _, _, (k, ref) in PERPS if k == "yahoo"}
    return tuple(sorted(syms))


@dataclass(frozen=True)
class ProxyRow:
    symbol: str
    name: str
    price: float | None = None
    unit: str = ""
    change_pct: float | None = None  # 직전 세션 종가(선물은 24시간 전) 대비
    vs_close_pct: float | None = None  # 선물: 국내 종가(환산) 대비
    vs_label: str | None = None
    premium_pct: float | None = None  # 해외 상장: 국내 환산가 대비
    fair: float | None = None  # 해외 상장: 국내 종가를 그 통화·비율로 환산한 값
    ratio: float | None = None
    funding_8h_pct: float | None = None
    vol_24h: float | None = None
    error: str | None = None


@dataclass(frozen=True)
class ProxyView:
    perps: list[ProxyRow] = field(default_factory=list[ProxyRow])
    listings: list[ProxyRow] = field(default_factory=list[ProxyRow])
    futures: list[ProxyRow] = field(default_factory=list[ProxyRow])
    macro: list[ProxyRow] = field(default_factory=list[ProxyRow])
    us_etfs: list[ProxyRow] = field(default_factory=list[ProxyRow])
    usdkrw: float | None = None


def _pct(a: float | None, b: float | None) -> float | None:
    return round((a / b - 1) * 100, 2) if a and b else None


def _quote(quotes: Mapping[str, GlobalQuote], sym: str, name: str, unit: str) -> ProxyRow:
    q = quotes.get(sym)
    if q is None:
        return ProxyRow(symbol=sym, name=name, unit=unit, error="받은 값 없음")
    return ProxyRow(
        symbol=sym, name=name, price=q.price, unit=unit, change_pct=_pct(q.price, q.prev)
    )


def build(
    quotes: Mapping[str, GlobalQuote],
    domestic: Mapping[str, float],
    index_close: Mapping[str, float],
) -> ProxyView:
    """화면 한 벌. `domestic` = {종목코드: 원화 종가}, `index_close` = {'KOSPI200': 종가}."""
    fx = quotes.get("KRW=X")
    eur = quotes.get("EURUSD=X")
    usdkrw = fx.price if fx is not None else None
    eurusd = eur.price if eur is not None else None

    perps: list[ProxyRow] = []
    for sym, name, (kind, ref) in PERPS:
        p = quotes.get(sym)
        if p is None:
            perps.append(ProxyRow(symbol=sym, name=name, error="받은 값 없음"))
            continue
        base: float | None = None
        label: str | None = None
        if kind == "stock" and domestic.get(ref) and usdkrw:
            base, label = domestic[ref] / usdkrw, "국내 종가(달러 환산)"
        elif kind == "index" and index_close.get(ref):
            base, label = index_close[ref], "코스피200 종가"
        elif kind == "yahoo" and (y := quotes.get(ref)) is not None:
            base, label = y.price, "나스닥 종가"
        perps.append(
            ProxyRow(
                symbol=sym,
                name=name,
                price=p.price,
                unit="pt" if kind == "index" else "$",
                change_pct=_pct(p.price, p.prev),
                vs_close_pct=_pct(p.price, base),
                vs_label=label,
                funding_8h_pct=None if p.funding_1h is None else round(p.funding_1h * 8 * 100, 4),
                vol_24h=p.vol_24h,
            )
        )

    listings: list[ProxyRow] = []
    for sym, name, code, shares, cur in LISTINGS:
        row = _quote(quotes, sym, name, "€" if cur == "EUR" else "$")
        if row.error is None:
            # 1 단위 통화가 몇 원인가 — 1유로 = EURUSD 달러 = EURUSD × USDKRW 원
            rate = usdkrw if cur == "USD" else (usdkrw * eurusd if usdkrw and eurusd else None)
            krw = domestic.get(code)
            fair = krw * shares / rate if krw and rate else None
            row = ProxyRow(
                symbol=row.symbol,
                name=row.name,
                price=row.price,
                unit=row.unit,
                change_pct=row.change_pct,
                fair=round(fair, 2) if fair else None,
                premium_pct=_pct(row.price, fair),
                ratio=shares,
            )
        listings.append(row)

    return ProxyView(
        perps=perps,
        listings=listings,
        futures=[_quote(quotes, s, n, u) for s, n, u in FUTURES],
        macro=[_quote(quotes, s, n, u) for s, n, u in MACRO],
        us_etfs=[_quote(quotes, s, n, u) for s, n, u in US_ETFS],
        usdkrw=usdkrw,
    )
