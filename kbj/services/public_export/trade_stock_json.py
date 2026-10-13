"""공개 매핑 종목 파일(docs/p6_design.md §7, metrics §28 — 묶음 MAP) — `trade/stocks.json`·
`trade/stock/<코드>.json`. **`origin = 'public'` 매핑만**(D-P6-14 — 관심종목 매핑 0, SQL 의 WHERE 와
읽은 행 검사 두 번), 시세 칸 없음(`manifest.trade_problems` 가 막는다).

읽는 표: pub_trade.stock_map·stock_export(SQL 은 `pub_` 스키마만 — 계약 ⑧). 이 패키지는 엔진을
import 하지 않는다(⑧) — 랭킹(최근 3개월 E 합 / 전년 같은 달 합 − 1, None 은 뒤, 동률 코드 순)은
`kbj.engines.trade.overlay.rank_yoy`·`rank` 와 같은 뜻을 여기서 다시 적는다(시험이 로그인 API 와
대조). 같은 종목에 local 줄이 있어도 공개 파일은 공개 줄로 만든다(local 우선은 로그인 API 만).

파일
- `trade/stocks.json`: 매핑 종목 랭킹(지수가 있는 종목만).
- `trade/stock/<코드>.json`: 매핑 줄·근거·월 지수(최근 `MONTHS` 개월)·분기 지수·매출 YoY·시차
상관·상태. 표가 비면 파일 0·이유 0(빈 파일·가짜 값을 만들지 않는다), 지수가 아직 없으면 이유 한 줄.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row

__all__ = [
    "MONTHS",
    "Q_EXPORT",
    "Q_MAP",
    "RANK_MONTHS",
    "SOURCE",
    "STOCKS_NAME",
    "build_trade_stock",
]

SOURCE: Final = "관세청(공공데이터포털)+DART"
STOCKS_NAME: Final = "trade/stocks.json"
MONTHS: Final = 60  # 월 지수 — 파일 상한 100 KB 안(합성 세계 약 12 KB)
RANK_MONTHS: Final = 3  # config/trade.yaml overlay.rank_months 와 같다(시험)
FX_NOTE: Final = "환율 미조정(달러)"
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"
NOTE_DRAFT: Final = (
    "매핑 가중치는 자체 큐레이션 초안 — '검토 전' 종목은 사람이 아직 확인하지 않았다"
)

Q_MAP: Final = Query(
    "trade_s_map",
    "SELECT code, hs, flow, region_id, name, weight, origin, reviewed, map_version, countries, "
    "evidence, as_of, quality FROM pub_trade.stock_map WHERE origin = 'public' "
    "ORDER BY code, hs, flow, region_id",
)
Q_EXPORT: Final = Query(
    "trade_s_export",
    "SELECT e.code, e.period, e.export_usd, e.yoy, e.revenue_yoy, e.status, e.map_version, "
    "e.lag_corr, e.as_of, e.quality FROM pub_trade.stock_export e "
    "WHERE e.code IN (SELECT DISTINCT code FROM pub_trade.stock_map WHERE origin = 'public') "
    "ORDER BY e.code, e.period",
)


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _usd(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int | Decimal | float):
        raise TypeError("export_usd 는 수")
    return int(v)


def _ratio(v: object) -> float | None:
    if v is None:
        return None
    f = float(v)  # pyright: ignore[reportArgumentType]
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _prev_ym(ym: str, n: int) -> str:
    idx = int(ym[:4]) * 12 + int(ym[5:7]) - 1 - n
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _rank_yoy(monthly: Mapping[str, int | None], ym: str, n: int) -> float | None:
    """최근 n 개월(ym 포함) 합 / 전년 같은 달 합 − 1(하나라도 None·분모 0 → None)."""

    def window(end: str) -> int | None:
        total = 0
        for k in range(n):
            v = monthly.get(_prev_ym(end, k))
            if v is None:
                return None
            total += v
        return total

    cur, prev = window(ym), window(_prev_ym(ym, 12))
    if cur is None or prev is None or prev == 0:
        return None
    return cur / prev - 1.0


def _is_quarter(period: str) -> bool:
    return "Q" in period


def _evidence(lines: Sequence[Row]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str | None]] = set()
    for ln in lines:
        for e in ln.get("evidence") or []:
            rc = e.get("rcept_no")
            key = (str(e.get("kind", "")), str(e.get("note", "")), None if rc is None else str(rc))
            if key not in seen:
                seen.add(key)
                out.append({"kind": key[0], "note": key[1], "rcept_no": key[2]})
    return out


def _line(r: Row) -> dict[str, Any]:
    return {
        "hs": str(r["hs"]),
        "flow": str(r["flow"]),
        "region_id": str(r["region_id"]) or None,
        "weight": float(r["weight"]),
        "countries": [str(c) for c in r.get("countries") or []],
    }


def _point(r: Row, *, quarter: bool) -> dict[str, Any]:
    return {
        "period": str(r["period"]),
        "export_usd": _usd(r["export_usd"]),
        "yoy": _ratio(r["yoy"]),
        "revenue_yoy": _ratio(r["revenue_yoy"]) if quarter else None,
        "quality": _q(r["quality"]),
    }


def build_trade_stock(read: Reader, *, now: datetime) -> BuildOut:
    del now
    maps = read(Q_MAP, {})
    if not maps:
        return BuildOut()
    lines: dict[str, list[Row]] = {}
    for m in maps:
        if m["origin"] != "public":  # SQL 이 이미 거른다 — 한 번 더(D-P6-14)
            raise ValueError("공개 내보내기에 origin local 매핑 행")
        lines.setdefault(str(m["code"]), []).append(m)
    ex: dict[str, list[Row]] = {}
    for r in read(Q_EXPORT, {}):
        if str(r["code"]) in lines:
            ex.setdefault(str(r["code"]), []).append(r)
    have = sorted(c for c in lines if ex.get(c))
    if not have:
        return BuildOut(skipped=(f"{STOCKS_NAME}: 지수 없음 — trade.stock_index 전",))
    monthly = {c: sorted((r for r in ex[c] if not _is_quarter(str(r["period"]))),
                         key=lambda r: str(r["period"])) for c in have}  # fmt: skip
    known = [str(r["period"]) for c in have for r in monthly[c] if r["export_usd"] is not None]
    last = max(known) if known else None
    ranks: list[tuple[str, float | None]] = []
    for c in have:
        m = {str(r["period"]): _usd(r["export_usd"]) for r in monthly[c]}
        ranks.append((c, None if last is None else _rank_yoy(m, last, RANK_MONTHS)))
    ranks.sort(key=lambda t: (t[1] is None, -(t[1] or 0.0), t[0]))
    files: list[PublicFile] = []
    rank_rows: list[dict[str, Any]] = []
    qs: list[Quality] = []
    ats: list[datetime] = []
    versions: set[str] = set()
    for code, v in ranks:
        ls = lines[code]
        rows = ex[code]
        mon = monthly[code]
        qtr = sorted(
            (r for r in rows if _is_quarter(str(r["period"]))), key=lambda r: str(r["period"])
        )
        tail_m = mon[-1] if mon else rows[-1]
        tail = (qtr or mon or rows)[-1]
        reviewed = all(bool(x["reviewed"]) for x in ls)
        name = str(ls[0]["name"])
        rank_rows.append(
            {"code": code, "name": name, "yoy_3m": v, "status": str(tail_m["status"]),
             "reviewed": reviewed, "quality": _q(tail_m["quality"])}
        )  # fmt: skip
        qs.append(_q(tail_m["quality"]))
        at = max(r["as_of"] for r in rows)
        ats.append(at)
        versions.add(str(ls[0]["map_version"]))
        known_q = [_q(r["quality"]) for r in mon if r["export_usd"] is not None]
        lc: Mapping[str, Any] = (tail["lag_corr"] or {}) if qtr else {}
        data = {
            "code": code,
            "name": name,
            "reviewed": reviewed,
            "map_version": str(ls[0]["map_version"]),
            "status": str(tail["status"]),
            "fx_note": FX_NOTE,
            "lines": [_line(x) for x in ls],
            "evidence": _evidence(ls),
            "monthly": [_point(r, quarter=False) for r in mon[-MONTHS:]],
            "quarterly": [_point(r, quarter=True) for r in qtr],
            "lag_corr": {k: _ratio(lc.get(k)) for k in ("0", "1", "2")} if qtr else {},
        }
        files.append(
            PublicFile(
                name=f"trade/stock/{code}.json", source=SOURCE, as_of=at,
                quality=worst_quality(known_q) if known_q else "estimated",
                data=data, notes=(NOTE_USD, *(() if reviewed else (NOTE_DRAFT,))),
            )
        )  # fmt: skip
    notes = [NOTE_USD, NOTE_DRAFT]
    if last is not None:
        notes.append(f"랭킹 = {last} 까지 최근 {RANK_MONTHS}개월 수출 지수 YoY")
    files.insert(
        0,
        PublicFile(
            name=STOCKS_NAME, source=SOURCE, as_of=max(ats), quality=worst_quality(qs),
            data={"map_version": "+".join(sorted(versions)), "rows": rank_rows},
            notes=tuple(notes),
        ),
    )  # fmt: skip
    return BuildOut(files=tuple(files))
