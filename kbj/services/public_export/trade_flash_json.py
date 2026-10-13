"""공개 수출 속보 `trade/flash.json`·띠 칩 `trade/ribbon.json`(관세청 10일 잠정치 —
metrics §25, docs/p6_design.md §7, 묶음 FLASH).

- 읽는 표: `pub_trade.ten_day`·`ten_day_columns`(SQL 은 `pub_` 스키마만 —
  `test_sql_schemas`, 계약 ⑧). 이 패키지는 엔진을 import 하지 않는다(계약 ⑧) —
  정의는 `kbj.engines.trade.flash` 와 같고, `test_trade_flash_json.py` 가 두 쪽 값을
  대조한다.
- `trade/flash.json` data = `{"kinds": [TradeFlash, …]}` — 종류마다 로그인 API
  (`/api/trade/flash`) 응답 data 와 같은 모양(최신 값 기준 YoY·발표 당시 값·10대
  기여도). 기간은 `PUBLIC_MONTHS` 개월 — 상한 `FILE_CAPS["trade/flash.json"]` 200 KB 에
  맞춘 값(설계서 §7 의 '10년'은 4종 × 11열 칸 수가 상한을 크게 넘는다 — 12개월도 약
  250 KB). 더 긴 기간은 로그인 API(`months` ≤ 120) [실측 #57 뒤 확정].
- `trade/ribbon.json` data = `{"chips": [export_flash]}` — 상단 띠 '수출 속보' 칩
  (로그인 `Ribbon.chips` 와 같은 칩 모양, 발표 당시 값 YoY — `credit.json` 과 같은
  방식). 매 페이지가 읽어 작게 둔다.
- 표가 비면 파일 0·이유 0(수집 전 — 빈 파일·가짜 값을 만들지 않는다).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, Row, stamp

__all__ = [
    "FLASH_NAME",
    "KINDS",
    "PUBLIC_MONTHS",
    "Q_TEN_DAY",
    "Q_TEN_DAY_COLUMNS",
    "RIBBON_NAME",
    "build_trade_flash",
]

FLASH_NAME: Final = "trade/flash.json"
RIBBON_NAME: Final = "trade/ribbon.json"
SOURCE: Final = "관세청(공공데이터포털)"
KINDS: Final[tuple[str, ...]] = ("exp_item", "exp_country", "imp_item", "imp_country")
CHIP_KIND: Final = "exp_item"
PUBLIC_MONTHS: Final = 6  # 그 앞 12개월은 YoY 분모로만 읽는다(합성 세계 4종 × 19구간 ≈ 125 KB)
COLUMNS: Final = 11
NO_PREV: Final = "전년 같은 구간 값 없음"
PREV_ZERO: Final = "전년 같은 구간 값 0"
COLUMN_CHANGED: Final = "열 이름 바뀜"
NOTE_USD: Final = "금액은 미화 달러(원화 환산 없음 — 환율 미조정)"

Q_TEN_DAY: Final = Query(
    "trade_ten_day",
    "SELECT kind, ym, seg, col_no, name, usd, first_usd, first_seen_at, revised_at, span_text, "
    "source, as_of, quality FROM pub_trade.ten_day WHERE ym >= %(ym_from)s "
    "ORDER BY kind, ym, seg, col_no",
)
Q_TEN_DAY_COLUMNS: Final = Query(
    "trade_ten_day_columns",
    "SELECT kind, col_no, valid_from, name FROM pub_trade.ten_day_columns "
    "ORDER BY kind, col_no, valid_from",
)
Q_TEN_DAY_LAST: Final = Query("trade_ten_day_last", "SELECT max(ym) AS ym FROM pub_trade.ten_day")

_Key = tuple[str, str, int, int]


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _usd(v: object) -> int | None:
    if v is None:
        return None
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    if d != d.to_integral_value():
        raise ValueError("달러 정수가 아닌 값")
    return int(d)


def _prev_ym(ym: str, months: int) -> str:
    i = int(ym[:4]) * 12 + int(ym[5:]) - 1 - months
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def _ratio(cur: int | None, prev: int | None) -> tuple[float | None, str | None]:
    if prev is None:
        return None, NO_PREV
    if cur is None:
        return None, "값 없음"
    if prev == 0:
        return None, PREV_ZERO
    return float(Decimal(cur) / Decimal(prev)) - 1.0, None


def _value(r: Row | None, first: bool) -> int | None:
    if r is None or r["quality"] == "invalid":
        return None
    return _usd(r["first_usd"] if first else r["usd"])


def _yoy(idx: Mapping[_Key, Row], r: Row, first: bool) -> tuple[float | None, str | None]:
    p = idx.get((r["kind"], _prev_ym(r["ym"], 12), r["seg"], r["col_no"]))
    if p is not None and p["name"] != r["name"]:
        return None, f"{COLUMN_CHANGED}(전년 '{p['name']}' → '{r['name']}')"
    return _ratio(_value(r, first), _value(p, False))


def _mom(idx: Mapping[_Key, Row], r: Row) -> float | None:
    """전월 같은 구간 대비(metrics §25 — 엔진 FlashCell.mom 과 같은 규칙: 열 이름이 바뀌면 None)."""
    p = idx.get((r["kind"], _prev_ym(r["ym"], 1), r["seg"], r["col_no"]))
    if p is not None and p["name"] != r["name"]:
        return None
    return _ratio(_value(r, False), _value(p, False))[0]


def _delta(idx: Mapping[_Key, Row], r: Row) -> int | None:
    p = idx.get((r["kind"], _prev_ym(r["ym"], 12), r["seg"], r["col_no"]))
    cur, prev = _value(r, False), _value(p, False)
    if cur is None or prev is None or (p is not None and p["name"] != r["name"]):
        return None
    return cur - prev


def _cells(idx: Mapping[_Key, Row], rows: Sequence[Row]) -> list[dict[str, Any]]:
    by_col = sorted(rows, key=lambda r: r["col_no"])
    total = next((r for r in by_col if r["col_no"] == 0), None)
    d0 = _delta(idx, total) if total is not None else None
    out: list[dict[str, Any]] = []
    for r in by_col:
        yoy, why = _yoy(idx, r, False)
        di = _delta(idx, r)
        out.append(
            {
                "col_no": r["col_no"],
                "name": r["name"],
                "usd": _usd(r["usd"]),
                "first_usd": _usd(r["first_usd"]),
                "yoy": yoy,
                "yoy_reason": why,
                "contribution": None if di is None or not d0 else float(Decimal(di) / Decimal(d0)),
                "mom": _mom(idx, r),
            }
        )
    return out


def _worst(qs: Iterable[str]) -> Quality:
    return worst_quality([_q(q) for q in qs])


def _columns(cols: Sequence[Row], kind: str, latest: Sequence[dict[str, Any]]) -> list[str]:
    names: dict[int, tuple[str, str]] = {}
    for c in cols:
        if c["kind"] == kind and (
            c["col_no"] not in names or c["valid_from"] > names[c["col_no"]][0]
        ):
            names[c["col_no"]] = (c["valid_from"], c["name"])
    fallback = {c["col_no"]: c["name"] for c in latest}
    out: list[str] = []
    for no in range(COLUMNS):
        n = names[no][1] if no in names else fallback.get(no)
        if n is None:
            break
        out.append(n)
    return out


def _kind(
    idx: Mapping[_Key, Row], rows: Sequence[Row], cols: Sequence[Row], kind: str
) -> tuple[dict[str, Any], datetime, Quality] | None:
    groups: dict[tuple[str, int], list[Row]] = {}
    for r in rows:
        if r["kind"] == kind:
            groups.setdefault((r["ym"], r["seg"]), []).append(r)
    if not groups:
        return None
    last = max(ym for ym, _ in groups)
    first = _prev_ym(last, PUBLIC_MONTHS - 1)
    keys = sorted((k for k in groups if k[0] >= first), reverse=True)
    segs: list[dict[str, Any]] = []
    for k in keys:
        g = groups[k]
        revised = [r["revised_at"] for r in g if r["revised_at"] is not None]
        segs.append(
            {
                "ym": k[0],
                "seg": k[1],
                "span_text": g[0]["span_text"],
                "first_seen_at": stamp(min(r["first_seen_at"] for r in g)),
                "revised_at": stamp(max(revised)) if revised else None,
                "quality": _worst(r["quality"] for r in g),
                "cells": _cells(idx, g),
            }
        )
    in_window = [r for k in keys for r in groups[k]]
    data = {
        "kind": kind,
        "months": PUBLIC_MONTHS,
        "columns": _columns(cols, kind, segs[0]["cells"]),
        "segments": segs,
    }
    as_of: datetime = max(r["as_of"] for r in in_window)
    return data, as_of, _worst(r["quality"] for r in in_window)


def _chip(
    idx: Mapping[_Key, Row], rows: Sequence[Row]
) -> tuple[dict[str, Any], datetime, Quality] | None:
    """띠 칩 — 수출 품목 전체(열 00) 최신 구간의 발표 당시 YoY(엔진 `flash_chip` 과 같은 정의)."""
    cands = [
        r for r in rows
        if r["kind"] == CHIP_KIND and r["col_no"] == 0 and r["quality"] != "invalid"
        and r["first_usd"] is not None
    ]  # fmt: skip
    if not cands:
        return None
    r = max(cands, key=lambda x: (x["ym"], x["seg"]))
    yoy, why = _yoy(idx, r, True)
    q: Quality = "estimated" if r["quality"] == "ok" else _q(r["quality"])
    a, b = str(r["span_text"]).split("~", 1)
    detail = {
        "ym": r["ym"], "seg": r["seg"], "span": r["span_text"],
        "span_label": f"{int(a)}~{int(b)}일", "usd": _usd(r["first_usd"]), "basis": "first",
    }  # fmt: skip
    at: datetime = r["first_seen_at"]
    chip: dict[str, Any] = {
        "key": "export_flash",
        "label": "수출 속보",
        "value": None,
        "tier": "public",
        "phase_pending": None,
        "tags": ["잠정"],
        "note": why,
        "detail": detail,
    }
    if yoy is not None:
        chip["value"] = {"value": yoy, "source": r["source"], "as_of": stamp(at), "quality": q}
    return chip, at, q


def build_trade_flash(read: Reader, *, now: datetime) -> BuildOut:
    """공개 속보·띠 칩 파일(표가 비면 파일 0·이유 0)."""
    del now  # 기간은 저장된 최신 기준 월에서 센다(시계와 무관 — 결정적)
    last_rows = read(Q_TEN_DAY_LAST, {})
    last = last_rows[0]["ym"] if last_rows else None
    if last is None:
        return BuildOut()  # 수집 전(작업 꺼짐 — 실측 #6·#17·#55)
    rows = read(Q_TEN_DAY, {"ym_from": _prev_ym(str(last), PUBLIC_MONTHS - 1 + 12)})
    cols = read(Q_TEN_DAY_COLUMNS, {})
    idx: dict[_Key, Row] = {}
    for r in rows:
        k = (r["kind"], r["ym"], r["seg"], r["col_no"])
        if k in idx:
            raise ValueError(f"10일 잠정치 같은 칸이 두 번 있다: {k}")
        idx[k] = r
    parts = [p for p in (_kind(idx, rows, cols, k) for k in KINDS) if p is not None]
    if not parts:
        return BuildOut()
    files = [
        PublicFile(
            name=FLASH_NAME,
            source=SOURCE,
            as_of=max(at for _, at, _ in parts),
            quality=worst_quality([q for _, _, q in parts]),
            data={"kinds": [d for d, _, _ in parts]},
            notes=(
                NOTE_USD,
                "관세청 잠정치 — 정정되면 최신 값으로 바뀐다(발표 당시 값은 first_usd)",
            ),
        )
    ]
    chip = _chip(idx, rows)
    skipped: tuple[str, ...] = ()
    if chip is not None:
        c, at, q = chip
        files.append(
            PublicFile(name=RIBBON_NAME, source=SOURCE, as_of=at, quality=q, data={"chips": [c]})
        )
    else:
        skipped = (f"{RIBBON_NAME}: 수출 품목 전체 값 없음",)
    return BuildOut(files=tuple(files), skipped=skipped)
