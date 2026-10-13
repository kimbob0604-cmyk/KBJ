"""공개 신용 칩 파일 — `credit.json`(상단 띠: 신용스프레드 AA-·신용잔고·예탁금, docs/p5_design.md
§7·D-P5-20, MAC 본문).

- `pub_market_stats.credit_balance`·`market_capital`, `pub_macro.series`(817Y002 AA-·국고 3년)만
  읽는다
  (SQL 은 `pub_` 스키마, 계약 ⑧). 로그인 칩(한국 대 글로벌)은 없다.
- 파일 data = `{"chips": [...]}` — 로그인 API `Ribbon.chips` 와 같은 칩 모양(key·label·value{value·
  source·as_of·quality}·tier·phase_pending·tags·note·detail). 화면 띠가 같은 코드로 그린다.
- 정의는 엔진 `kbj.engines.macro.ribbon` 과 같다(이 패키지는 엔진을 import 하지 않는다 — 계약 ⑧):
  신용스프레드 = 두 시리즈 모두 값이 있는 마지막 날의 AA- − 국고 3년(%p), 한쪽이 더 뒤 날짜 값을
  가졌으면 stale. 신용잔고 = 신용거래융자 전체(`loan_total`), 없으면 유가+코스닥이 둘 다 있을 때 합.
  예탁금 = `investor_deposit`. 각 칩 detail 에 전 값 대비 변화.
- 값이 하나도 없으면(수집 전) 파일 0·이유 0 — 빈 파일·가짜 값을 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Final

from kbj.core.calendar import TradingCalendar
from kbj.core.time import KST
from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row, day, num, stamp

__all__ = ["NAME", "build_credit"]

NAME: Final = "credit.json"
LOOKBACK_DAYS: Final = 21
KOFIA_OPEN: Final = (13, 30)
CREDIT_AA: Final = "ECOS:817Y002:corp_aa3y"
CREDIT_KTB: Final = "ECOS:817Y002:ktb3y"

Q_CREDIT_SERIES: Final = Query(
    "credit_series",
    "SELECT series_id, period, value, quality, as_of, released_at FROM pub_macro.series "
    "WHERE series_id IN (%(a)s, %(b)s) AND period >= %(start)s AND value IS NOT NULL "
    "ORDER BY period",
)
Q_BALANCE: Final = Query(
    "credit_balance",
    "SELECT stat_date, item, value, unit, quality, as_of FROM pub_market_stats.credit_balance "
    "WHERE stat_date >= %(start)s AND value IS NOT NULL ORDER BY stat_date, item",
)
Q_CAPITAL: Final = Query(
    "credit_capital",
    "SELECT stat_date, item, value, unit, quality, as_of FROM pub_market_stats.market_capital "
    "WHERE stat_date >= %(start)s AND item = 'investor_deposit' AND value IS NOT NULL "
    "ORDER BY stat_date",
)


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _dec(v: object) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    return d if d.is_finite() else None


def expected_day(now: datetime, cal: TradingCalendar) -> date:
    """신용잔고가 최신이려면 있어야 할 날(D+1 13:30 개방·수집 — 엔진 띠 칩과 같은 규칙)."""
    k = now.astimezone(KST)
    prev = cal.prev_trading_day(k.date())
    return prev if (k.hour, k.minute) >= KOFIA_OPEN else cal.prev_trading_day(prev)


def _chip(
    key: str,
    label: str,
    value: float,
    source: str,
    as_of: datetime,
    quality: Quality,
    tags: Sequence[str],
    note: str | None,
    detail: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": {"value": value, "source": source, "as_of": stamp(as_of), "quality": quality},
        "tier": "public",
        "phase_pending": None,
        "tags": list(tags),
        "note": note,
        "detail": dict(detail),
    }


def _spread(rows: Sequence[Row]) -> tuple[dict[str, Any], datetime, Quality] | None:
    a = {r["period"]: r for r in rows if r["series_id"] == CREDIT_AA}
    k = {r["period"]: r for r in rows if r["series_id"] == CREDIT_KTB}
    common = sorted(set(a) & set(k))
    if not common:
        return None
    p = common[-1]
    da, dk = _dec(a[p]["value"]), _dec(k[p]["value"])
    if da is None or dk is None:
        return None
    va, vk, v = float(da), float(dk), float(da - dk)  # 차는 Decimal 로 정확히(엔진과 같다)
    q = worst_quality([_q(a[p]["quality"]), _q(k[p]["quality"])])
    lag = max([*a, *k]) > p
    if lag:
        q = worst_quality([q, "stale"])
    at = max(
        x
        for x in (a[p].get("released_at"), k[p].get("released_at"), a[p]["as_of"])
        if x is not None
    )
    detail: dict[str, Any] = {"period": p, "aa": va, "ktb3y": vk}
    if len(common) > 1:
        pp = common[-2]
        pa, pk = _dec(a[pp]["value"]), _dec(k[pp]["value"])
        if pa is not None and pk is not None:
            detail["change_bp"] = (v - float(pa - pk)) * 100.0
            detail["prev_period"] = pp
    note = "공표 지연 — 한쪽 시리즈가 늦다(전 공통일 값)" if lag else None
    chip = _chip(
        "credit_spread", "신용스프레드 AA-", v, "ECOS:817Y002", at, q, ["%p"], note, detail
    )
    return chip, at, q


def _balance(
    rows: Sequence[Row], expected: date
) -> tuple[dict[str, Any], datetime, Quality] | None:
    days = sorted({r["stat_date"] for r in rows}, reverse=True)
    got: list[tuple[Any, float, Quality, datetime, str]] = []
    for d in days:
        by = {r["item"]: r for r in rows if r["stat_date"] == d}
        if "loan_total" in by:
            r = by["loan_total"]
            v = num(r["value"])
            if v is not None:
                got.append((d, v, _q(r["quality"]), r["as_of"], str(r["unit"])))
        elif "loan_kospi" in by and "loan_kosdaq" in by:
            parts = [by["loan_kospi"], by["loan_kosdaq"]]
            vals = [num(x["value"]) for x in parts]
            if all(x is not None for x in vals):
                got.append(
                    (
                        d,
                        sum(x for x in vals if x is not None),
                        worst_quality([_q(x["quality"]) for x in parts]),
                        max(x["as_of"] for x in parts),
                        str(parts[0]["unit"]),
                    )
                )
        if len(got) == 2:
            break
    if not got:
        return None
    d, v, q, at, unit = got[0]
    note = None
    if d < expected:
        q = worst_quality([q, "stale"])
        note = f"공표 지연 — {day(d)} 값(D+1 13시 개방)"
    detail: dict[str, Any] = {"date": day(d), "unit": unit}
    if len(got) == 2:
        detail["change"] = v - got[1][1]
        detail["prev_date"] = day(got[1][0])
    chip = _chip(
        "credit_balance", "신용잔고", v, "DATAGO:15094809/credit", at, q, [unit], note, detail
    )
    return chip, at, q


def _deposit(rows: Sequence[Row]) -> tuple[dict[str, Any], datetime, Quality] | None:
    have = [r for r in rows if num(r["value"]) is not None]
    if not have:
        return None
    last = have[-1]
    v = num(last["value"]) or 0.0
    detail: dict[str, Any] = {"date": day(last["stat_date"]), "unit": str(last["unit"])}
    if len(have) > 1:
        detail["change"] = v - (num(have[-2]["value"]) or 0.0)
        detail["prev_date"] = day(have[-2]["stat_date"])
    q = _q(last["quality"])
    chip = _chip(
        "investor_deposit",
        "투자자예탁금",
        v,
        "DATAGO:15094809/capital",
        last["as_of"],
        q,
        [str(last["unit"])],
        None,
        detail,
    )
    return chip, last["as_of"], q


def build_credit(read: Reader, *, now: datetime, cal: TradingCalendar | None = None) -> BuildOut:
    """공개 신용 칩 파일(값이 없으면 파일 0 + 이유)."""
    start = now.date() - timedelta(days=LOOKBACK_DAYS)
    expected = expected_day(now, cal or TradingCalendar.default())
    parts = [
        _spread(
            read(Q_CREDIT_SERIES, {"a": CREDIT_AA, "b": CREDIT_KTB, "start": start.isoformat()})
        ),
        _balance(read(Q_BALANCE, {"start": start}), expected),
        _deposit(read(Q_CAPITAL, {"start": start})),
    ]
    got = [p for p in parts if p is not None]
    if not got:
        return BuildOut()  # 수집 전(작업 꺼짐 — 실측 #43·#48) — 파일도 이유도 없다
    sources = sorted({c["value"]["source"].split(":", 1)[0] for c, _, _ in got})
    f = PublicFile(
        name=NAME,
        source="+".join(sources),
        as_of=max(at for _, at, _ in got),
        quality=worst_quality([q for _, _, q in got]),
        data={"chips": [c for c, _, _ in got]},
        notes=tuple(
            f"{k}: 값 없음"
            for k, p in zip(
                ("credit_spread", "credit_balance", "investor_deposit"), parts, strict=True
            )
            if p is None
        ),
    )
    return BuildOut(files=(f,))
