"""SD 개인 기록 이관 읽기·변환(P8 묶음 JNL — docs/p8_design.md §1.5·§4.1.5, ADR 0033).

원본은 사용자가 VM 에 올린 SD 파일이다(레포에 넣지 않는다 — 시험은 합성 생성기
`tests/fixtures/synthetic/sqlite/sd_p8_gen.py`). 이 모듈은 **읽기·변환만** 한다(계약 ⑦ — 저장소 층은
서비스·엔진을 모른다). 쓰기는 `kbj.services.personal.journal` 의 `import-sd` 명령이 쓰기 모듈
(일지·매매 — `PersonalRepo.add_*_personal`, 추천 — `put_picks_legacy_import`, 규칙 —
`kbj.services.personal.rules` + 변환 표 `kbj.engines.rules.legacy_sd`)로 한다.

원본 → 대상(변환):
- `dashboard.db` `analysis_journal` → `prv_journal.analysis` — 칸 그대로, `market_cap` 백만원
  → 원, `reflection_label` → 반영도, `tags` JSON 목록.
- `dashboard.db` `trade_journal` → `prv_journal.trade` — action buy·sell, KR 만(미국은 `not_kr`).
- `dashboard.db` `recommendation_history` → `prv_journal.pick` — 키 (date, source, code),
  다시 넣으면 같은 키는 덮는다.
- `cache/alert_rules.json` → `prv_alerts.rule`(규칙 쓰기 모듈) — 항목 목록(원문 dict)만 돌려준다.
- `cache/server_portfolio.json` → `prv_journal.trade` — 포지션 → 기초 매수 한 줄(buy_price·quantity·
  buy_date) [확인 필요].

- 시각: SD 의 `created_at`·`updated_at` 은 오프셋 없는 서버 지역 시각이라 쓰지 않는다(지어내지
  않는다) —
  이관 시각(`now`)을 넣는다. 날짜(analysis_date·trade_date·date)는 앞 10자 `YYYY-MM-DD` 만.
- 버린 행은 (원본, 위치, 사유)로 센다(값은 넣지 않는다). 토큰 캐시 이름의 파일은 열지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final

from kbj.core.rules_rows import AnalysisRow, PickRow, TradeRow
from kbj.store.legacy_import.sources import (
    SourceError,
    iter_sqlite,
    open_sqlite_ro,
    read_json,
    sqlite_tables,
)
from kbj.store.legacy_import.transforms import kr_code

__all__ = [
    "SD_P8_TABLES",
    "SOURCE",
    "Drop8",
    "SdP8",
    "analysis_from_sd",
    "pick_from_sd",
    "portfolio_from_sd",
    "read_alert_rules",
    "read_sd_db",
    "read_sd_portfolio",
    "trade_from_sd",
]

SOURCE: Final = "import:sd"
SD_P8_TABLES: Final = ("analysis_journal", "trade_journal", "recommendation_history")
MILLION: Final = Decimal(1_000_000)


@dataclass(frozen=True)
class Drop8:
    origin: str  # 'analysis_journal' 등
    where: int  # SQLite id 또는 파일 안 순서(0부터)
    reason: str


@dataclass
class SdP8:
    analyses: list[AnalysisRow] = field(default_factory=list[AnalysisRow])
    trades: list[TradeRow] = field(default_factory=list[TradeRow])
    picks: list[PickRow] = field(default_factory=list[PickRow])
    drops: list[Drop8] = field(default_factory=list[Drop8])
    missing_tables: list[str] = field(default_factory=list[str])


class _Bad(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _day(v: object) -> date:
    s = str(v or "").strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise _Bad("bad_date") from None


def _code(v: object, market: object = "kr") -> str:
    if str(market or "kr").strip().lower() != "kr":
        raise _Bad("not_kr")
    c = kr_code(v)
    if c is None:
        raise _Bad("bad_code")
    return c


def _dec(v: object, *, scale: Decimal | None = None) -> Decimal | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        raise _Bad("bad_number") from None
    if not d.is_finite():
        raise _Bad("bad_number")
    if scale is not None:
        d *= scale
    return d.quantize(Decimal("0.01"))


def _flt(v: object) -> float | None:
    if v is None or v == "" or isinstance(v, bool):
        return None
    try:
        x = float(str(v))
    except ValueError:
        raise _Bad("bad_number") from None
    if x != x or x in (float("inf"), float("-inf")):
        raise _Bad("bad_number")
    return x


def _text(v: object) -> str | None:
    s = str(v).strip() if v is not None else ""
    return s or None


def _tags(v: object) -> tuple[str, ...]:
    s = _text(v)
    if s is None:
        return ()
    try:
        got = json.loads(s)
    except ValueError:
        got = [t for t in s.split(",")]
    if isinstance(got, str):
        got = [got]
    if not isinstance(got, list):
        return ()
    return tuple(str(t).strip() for t in got if str(t).strip())[:20]


def analysis_from_sd(r: Mapping[str, Any], *, now: datetime) -> AnalysisRow:
    """SD `analysis_journal` 한 행 → 일지 행(id 없음). 틀리면 `_Bad`(사유)."""

    def three(kind: str, conv: Any) -> dict[str, Any]:
        return {c: conv(r.get(f"{c}_{kind}")) for c in ("bear", "base", "bull")}

    mktcap = _dec(r.get("market_cap"), scale=MILLION)
    try:
        return AnalysisRow(
            id=None,
            code=_code(r.get("stock_code")),
            analyzed_on=_day(r.get("analysis_date")),
            chain_ids=(
                _text(r.get("theme_id")),
                _text(r.get("layer_id")),
                _text(r.get("segment_id")),
            ),
            price=_dec(r.get("current_price")),
            ret_52w=_flt(r.get("return_52w")),
            fwd_per=_flt(r.get("fwd_per")),
            band_pos=_flt(r.get("fwd_per_band_pct")),
            ev_ebitda=_flt(r.get("ev_ebitda")),
            mktcap=None if mktcap is None else mktcap.quantize(Decimal(1)),
            opm=_flt(r.get("opm_estimate")),
            eps=three("eps", _dec),
            per=three("per", _flt),
            tp=three("tp", _dec),
            tp_source="SD 이관",
            conviction=_text(r.get("reflection_label")),
            memo=_text(r.get("memo")),
            tags=_tags(r.get("tags")),
            conclusion=_text(r.get("conclusion")),
            value_sources={},
            source=SOURCE,
            updated_at=now,
        )
    except _Bad:
        raise
    except (ValueError, TypeError):
        raise _Bad("bad_row") from None


def trade_from_sd(r: Mapping[str, Any], *, now: datetime) -> TradeRow:
    side = str(r.get("action") or "").strip().lower()
    if side not in ("buy", "sell"):
        raise _Bad("bad_side")
    price = _dec(r.get("price"))
    qty = r.get("qty")
    if price is None or price <= 0 or isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise _Bad("bad_number")
    try:
        return TradeRow(
            id=None,
            code=_code(r.get("code"), r.get("market")),
            name_at_trade=_text(r.get("name")),
            side=side,
            trade_date=_day(r.get("trade_date")),
            price=price,
            qty=qty,
            fee=_dec(r.get("fee")) or Decimal(0),
            tax=_dec(r.get("tax")) or Decimal(0),
            strategy=_text(r.get("strategy")),
            memo=_text(r.get("memo")),
            signal_id=None,
            source=SOURCE,
            created_at=now,
        )
    except _Bad:
        raise
    except (ValueError, TypeError):
        raise _Bad("bad_row") from None


def pick_from_sd(r: Mapping[str, Any]) -> PickRow:
    raw = _text(r.get("tags_json"))
    tags: dict[str, Any] = {}
    if raw is not None:
        try:
            got = json.loads(raw)
        except ValueError:
            got = None
        if isinstance(got, dict):
            tags = {str(k): v for k, v in got.items()}
        elif isinstance(got, list):
            tags = {"tags": got}
    rank = r.get("rank")
    try:
        return PickRow(
            date=_day(r.get("date")),
            source=_text(r.get("source")) or "",
            rank=rank
            if isinstance(rank, int) and not isinstance(rank, bool) and rank >= 1
            else None,
            code=_code(r.get("code"), r.get("market")),
            name=_text(r.get("name")),
            market=_text(r.get("market")),
            score=_flt(r.get("score")),
            price_at_rec=_dec(r.get("price_at_rec")),
            tags=tags,
        )
    except _Bad:
        raise
    except (ValueError, TypeError):
        raise _Bad("bad_row") from None


def _rows(conn: sqlite3.Connection, table: str) -> Iterator[dict[str, object]]:
    return iter_sqlite(conn, f"SELECT * FROM {table} ORDER BY id")  # noqa: S608 — 상수 표 이름


def read_sd_db(path: Path, *, now: datetime) -> SdP8:
    """SD `dashboard.db` 의 P8 표 셋을 읽어 변환한다(없는 표는 `missing_tables`)."""
    out = SdP8()
    conn = open_sqlite_ro(path)
    try:
        have = sqlite_tables(conn)
        for table in SD_P8_TABLES:
            if table not in have:
                out.missing_tables.append(table)
                continue
            for r in _rows(conn, table):
                rid = r.get("id")
                where = rid if isinstance(rid, int) else -1
                try:
                    if table == "analysis_journal":
                        out.analyses.append(analysis_from_sd(r, now=now))
                    elif table == "trade_journal":
                        out.trades.append(trade_from_sd(r, now=now))
                    else:
                        out.picks.append(pick_from_sd(r))
                except _Bad as e:
                    out.drops.append(Drop8(table, where, e.reason))
    finally:
        conn.close()
    return out


def read_alert_rules(path: Path) -> list[dict[str, Any]]:
    """SD `cache/alert_rules.json` `{"rules": [...]}` → 항목 목록(원문 dict, 형식이 틀린 항목은
    빈 dict — 변환 표가 `bad_row`·`unsupported_type` 으로 센다)."""
    doc = read_json(path)
    if not isinstance(doc, dict):
        raise SourceError("alert_rules.json 형식이 아니다(객체가 아님)")
    rules = doc.get("rules")
    if not isinstance(rules, list):
        raise SourceError("alert_rules.json 에 rules 목록이 없다")
    return [dict(x) if isinstance(x, dict) else {} for x in rules]


def portfolio_from_sd(p: Mapping[str, Any], *, now: datetime) -> TradeRow:
    """SD 포지션 `{code, name, market, buy_price, quantity, buy_date, memo}` → 기초 매수 한 줄
    [확인 필요 — SD 화면 저장 형식, buy_date 없으면 버린다]."""
    price = _dec(p.get("buy_price"))
    qty = p.get("quantity")
    if isinstance(qty, float) and qty.is_integer():
        qty = int(qty)
    if price is None or price <= 0 or isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
        raise _Bad("bad_number")
    try:
        return TradeRow(
            id=None,
            code=_code(p.get("code"), p.get("market")),
            name_at_trade=_text(p.get("name")),
            side="buy",
            trade_date=_day(p.get("buy_date")),
            price=price,
            qty=qty,
            fee=Decimal(0),
            tax=Decimal(0),
            strategy="SD 포트폴리오 기초",
            memo=_text(p.get("memo")),
            signal_id=None,
            source=SOURCE,
            created_at=now,
        )
    except _Bad:
        raise
    except (ValueError, TypeError):
        raise _Bad("bad_row") from None


def read_sd_portfolio(path: Path, *, now: datetime) -> tuple[list[TradeRow], list[Drop8]]:
    doc = read_json(path)
    if not isinstance(doc, dict) or not isinstance(doc.get("positions"), list):
        raise SourceError("server_portfolio.json 에 positions 목록이 없다")
    rows: list[TradeRow] = []
    drops: list[Drop8] = []
    for i, p in enumerate(doc["positions"]):
        try:
            if not isinstance(p, dict):
                raise _Bad("bad_row")
            rows.append(portfolio_from_sd(p, now=now))
        except _Bad as e:
            drops.append(Drop8("server_portfolio", i, e.reason))
    return rows, drops
