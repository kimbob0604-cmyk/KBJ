"""원본 행 → 대상 행 순수 함수(docs/p2_design.md §8.4·§8.6 — 네트워크·DB 없음, 단위 시험).

각 함수는 원본 한 행(dict)을 받아 **후보 목록**을 돌려준다: 대상 행(dict, 대상 표 열 이름 → 값) 또는
`Drop(사유)`. 한 원본 행이 여러 대상 행이 되는 매핑(SD flow_cache 의 날짜 배열)이 있어 목록이다.
`loaded_by`(이관 원본 표시)는 실행기가 채운다.

버림 규칙(사유 이름은 보고서·`ops.legacy_import.drops` 에 그대로 나간다)
- `naver`: 출처가 네이버(U4, conflict_map §1.5) — 이관하지 않고 KRX·KIS 로 다시 받는다
- `no_source`: 출처 열이 비었다 — 네이버 행인지 가를 수 없어 버린다(지어내지 않는다)
- `not_kis`: 수급인데 KIS 가 아니다(§8.4 "KIS 행만")
- `bad_code`·`bad_date`·`bad_value`·`volume_not_integer`·`bad_json`·`array_length`·`unknown_unit`·
  `no_update_id`·`bad_kind`·`no_asof`: 형식 오류 — 행을 고쳐 넣지 않는다
- `secret_like_key`: 키 이름이 토큰·비밀처럼 보인다(절대 규칙 5 — 값은 열어 보지 않고 버린다)

값 규칙
- 날짜 TEXT(`YYYY-MM-DD`·`YYYYMMDD`) → date. 종목코드는 text 6자리 — 숫자로 저장돼 앞자리 0 이
  빠진 코드는 0 을 채운다(KR 만). 미국 티커는 대문자.
- 실수(REAL)는 `Decimal(str(x))`(짧은 표기 그대로 — float 끝자리 잡음 없이). NaN·무한대는 None.
- ET 금액(억원 float — board/ingest/krx.py `_norm`)은 원 정수로 되돌린다(× 1e8, 반올림 —
  metrics §0).
- 시각은 오프셋이 있는 ISO 만 받는다. naive 시각은 어느 시간대인지 모르니 NULL(원문은 detail 에).
  예외: 쓰는 코드로 시간대를 확인한 원본만 그 시간대로 읽는다(SD `ops_state` = KST —
  `parse_naive_in`).
- 자유 문장(run_log note, ops_state 값)은 `kbj.core.masking.mask_text` 를 거친다(절대 규칙 5).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

from kbj.core.masking import mask_text

TargetRow = dict[str, object]


@dataclass(frozen=True)
class Drop:
    """버린 후보 하나와 사유."""

    reason: str


Candidate = TargetRow | Drop
Transform = Callable[[dict[str, object]], list[Candidate]]

EOK: Final = Decimal(100_000_000)  # 억원 → 원
_KR_CODE = re.compile(r"[0-9A-Z]{6}")
_DATE_DASH = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATE_PLAIN = re.compile(r"(\d{4})(\d{2})(\d{2})")
_SECRET_KEY = re.compile(
    r"(?i)token|secret|passw|app[_-]?key|api[_-]?key|credential|cookie|session[_-]?id"
)
_ET_INVESTORS: Final = {"기관": "institution", "외국인": "foreign", "개인": "individual"}


# ── 값 헬퍼 ──────────────────────────────────────────────────────────────────────────────


def parse_date(v: object) -> date | None:
    """`YYYY-MM-DD`·`YYYYMMDD`(앞뒤 공백 허용, 뒤에 시각이 붙어도 날짜만). 틀리면 None."""
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v or "").strip()
    m = _DATE_DASH.match(s) or _DATE_PLAIN.fullmatch(s)
    if m is None:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


def parse_aware(v: object) -> datetime | None:
    """오프셋이 있는 ISO 시각 → UTC. naive·형식 오류는 None(어느 시간대인지 지어내지 않는다)."""
    s = str(v or "").strip()
    if not s:
        return None
    try:
        t = datetime.fromisoformat(s)
    except ValueError:
        return None
    if t.tzinfo is None or t.utcoffset() is None:
        return None
    return t.astimezone(UTC)


def parse_naive_in(v: object, tz: tzinfo) -> datetime | None:
    """`YYYY-MM-DD HH:MM:SS`(오프셋 없음) → UTC. **쓴 쪽 코드로 시간대를 확인한** 원본에만 쓴다.
    오프셋이 붙어 있으면 그것을 따른다. 형식이 다르면 None.

    시간대는 표의 DDL 기본값이 아니라 실제로 쓰는 코드로 정한다: SD `ops_state` 의 DDL 기본값은
    `CURRENT_TIMESTAMP`(UTC)지만 유일한 쓰기 지점 `server.py:_ops_set`(도입 커밋부터)이
    `now_kst().strftime("%Y-%m-%d %H:%M:%S")` 로 KST 를 적는다 — UTC 로 읽으면 9시간 어긋난다.
    """
    s = str(v or "").strip()
    aware = parse_aware(s)
    if aware is not None:
        return aware
    try:
        t = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")  # noqa: DTZ007 — 바로 아래에서 tz 를 붙인다
    except ValueError:
        return None
    return t.replace(tzinfo=tz).astimezone(UTC)


def kr_code(v: object) -> str | None:
    """KR 종목코드 text 6자리. 숫자로 저장돼 앞자리 0 이 빠졌으면 채운다. 틀리면 None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        v = str(v)
    s = str(v or "").strip().upper()
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    return s if _KR_CODE.fullmatch(s) else None


def us_ticker(v: object) -> str | None:
    s = str(v or "").strip().upper()
    return s if s and not any(c.isspace() for c in s) and len(s) <= 16 else None


def dec(v: object) -> Decimal | None:
    """숫자 → Decimal(짧은 표기). None·빈 문자열·NaN·무한대는 None. 숫자가 아니면 ValueError."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    if isinstance(v, bool):
        raise ValueError("불 값은 숫자가 아니다")
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        return Decimal(repr(v))
    if isinstance(v, int | Decimal):
        return Decimal(v)
    try:
        d = Decimal(str(v).replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("숫자가 아니다") from None
    return d if d.is_finite() else None


def whole(v: object) -> int | None:
    """정수여야 하는 수(거래량·주식수·수량). 소수부가 있으면 ValueError."""
    d = dec(v)
    if d is None:
        return None
    if d != d.to_integral_value():
        raise ValueError("정수가 아니다")
    return int(d)


def eok_to_won(v: object) -> int | None:
    """억원(float) → 원 정수(반올림)."""
    d = dec(v)
    if d is None:
        return None
    return int((d * EOK).to_integral_value(rounding=ROUND_HALF_EVEN))


def source_of(v: object) -> str:
    return str(v or "").strip()


def is_naver(source: str) -> bool:
    return source.lower().startswith("naver")


def _source_drop(source: str) -> Drop | None:
    if not source:
        return Drop("no_source")
    if is_naver(source):
        return Drop("naver")
    return None


def _json_str(v: object) -> str:
    """ops.kv 값: 원문 문자열을 JSON 문자열로(숫자처럼 보여도 바꾸지 않는다 — 손실 없음)."""
    return mask_text("" if v is None else str(v))


# ── ops.kv ───────────────────────────────────────────────────────────────────────────────


def kv_row(namespace: str, *, naive_tz: tzinfo | None = None) -> Transform:
    """SD `ops_state(key, value, updated_at)`·ET `meta(k, v)` → `ops.kv`.

    `naive_tz` = 원본이 오프셋 없이 적은 `updated_at` 의 시간대(쓰는 코드로 확인한 것 — SD 는 KST,
    `parse_naive_in`). None 이면 오프셋 없는 시각은 NULL(시간대를 지어내지 않는다).
    """

    def updated(v: object) -> datetime | None:
        return parse_aware(v) if naive_tz is None else parse_naive_in(v, naive_tz)

    def run(row: dict[str, object]) -> list[Candidate]:
        key = str(row.get("key", row.get("k")) or "").strip()
        if not key:
            return [Drop("bad_value")]
        if _SECRET_KEY.search(key):
            return [Drop("secret_like_key")]
        value = row.get("value", row.get("v"))
        return [
            {
                "namespace": namespace,
                "key": key,
                "value": _json_str(value),
                "updated_at": updated(row.get("updated_at")),
            }
        ]

    return run


def sd_fetch_progress(row: dict[str, object]) -> list[Candidate]:
    """SD `fetch_progress` 한 행 → `ops.kv(namespace='sd.fetch_progress', key=종목코드)`, 값은
    나머지 열 전체(JSON 객체). `updated_at` 은 SQLite `datetime('now','localtime')` — 시간대를
    모르니 값에 원문으로만 둔다."""
    code = kr_code(row.get("stock_code"))
    if code is None:
        return [Drop("bad_code")]
    rest = {k: v for k, v in row.items() if k != "stock_code"}
    for k, v in rest.items():
        if isinstance(v, str):
            rest[k] = mask_text(v)
        elif isinstance(v, bytes):
            return [Drop("bad_value")]
    return [{"namespace": "sd.fetch_progress", "key": code, "value": rest, "updated_at": None}]


# ── ops.job_run ──────────────────────────────────────────────────────────────────────────


def run_log_row(job: str) -> Transform:
    """ET board/us `run_log(asof, step, ok, note, ts)` → `ops.job_run`.

    run_id = `<job>:<asof>:<ts>:<step>:<sha256(ok|note) 8자>` — 같은 초·같은 단계의 다른 기록이
    겹치지 않게 하고, 다시 돌려도 같은 값(멱등). 완전히 같은 줄은 실행기가 `dup_key` 로 센다.
    """

    def run(row: dict[str, object]) -> list[Candidate]:
        as_of = str(row.get("asof") or "").strip()
        if not as_of:
            return [Drop("no_asof")]
        step = str(row.get("step") or "").strip()
        ts_raw = str(row.get("ts") or "").strip()
        ok = row.get("ok")
        note = mask_text(str(row.get("note") or ""))
        h = hashlib.sha256(f"{ok}|{note}".encode()).hexdigest()[:8]
        finished = parse_aware(ts_raw)
        detail: dict[str, object] = {"step": step, "note": note}
        if finished is None and ts_raw:
            detail["ts_raw"] = ts_raw  # 오프셋 없는 옛 시각 — 시간대를 지어내지 않는다
        return [
            {
                "run_id": f"{job}:{as_of}:{ts_raw}:{step}:{h}",
                "job": job,
                "as_of": as_of,
                "attempt": 1,
                "status": "ok" if ok in (1, "1", True) else "failed",
                "started_at": None,
                "finished_at": finished,
                "detail": detail,
                "source": "legacy_import",
            }
        ]

    return run


# ── prv_market.universe ──────────────────────────────────────────────────────────────────


def sd_index_universe(row: dict[str, object]) -> list[Candidate]:
    """SD `index_universe(stock_code, source, rank, market_cap, added_date, removed_date,
    is_active)` → `prv_market.universe(source='sd.<원본 source 소문자>')`.
    as_of = 편입일(added_date)."""
    code = kr_code(row.get("stock_code"))
    if code is None:
        return [Drop("bad_code")]
    as_of = parse_date(row.get("added_date"))
    if as_of is None:
        return [Drop("bad_date")]
    src = source_of(row.get("source")).lower()
    if not src:
        return [Drop("no_source")]
    try:
        rank = whole(row.get("rank"))
        cap = dec(row.get("market_cap"))
    except ValueError:
        return [Drop("bad_value")]
    removed = parse_date(row.get("removed_date"))
    flags: dict[str, object] = {
        "rank": rank,
        "market_cap": str(cap) if cap is not None else None,
        "removed_date": removed.isoformat() if removed else None,
        "is_active": bool(row.get("is_active")) if row.get("is_active") is not None else None,
    }
    return [
        {
            "market": "KR",
            "code": code,
            "as_of": as_of,
            "source": f"sd.{src}",
            "name": None,
            "kind": None,
            "listed_on": None,
            "flags": flags,
            "quality": "ok",
            "received_at": None,
        }
    ]


# ── prv_market.stock_snapshot ────────────────────────────────────────────────────────────


def board_snap(row: dict[str, object]) -> list[Candidate]:
    """ET board.db `snap` → `prv_market.stock_snapshot(market='KR')`. 거래대금·시총 억원 → 원."""
    source = source_of(row.get("source"))
    if (drop := _source_drop(source)) is not None:
        return [drop]
    code = kr_code(row.get("code"))
    if code is None:
        return [Drop("bad_code")]
    day = parse_date(row.get("asof"))
    if day is None:
        return [Drop("bad_date")]
    try:
        close = dec(row.get("close"))
        out: TargetRow = {
            "market": "KR",
            "code": code,
            "trade_date": day,
            "source": source,
            "venue": "KRX" if source.lower() == "krx" else "",
            "name": row.get("name"),
            "segment": row.get("market"),
            "sector": None,
            "industry": None,
            "close": close,
            "chg_pct": dec(row.get("chg_pct")),
            "volume": whole(row.get("volume")),
            "turnover": eok_to_won(row.get("turnover")),
            "turnover_is_estimate": bool(row.get("turnover_is_estimate")),
            "mktcap": eok_to_won(row.get("mktcap")),
            "shares": None,
            "extra": {},
            "quality": "ok" if close is not None else "invalid",
            "received_at": None,
        }
    except ValueError as e:
        return [Drop("volume_not_integer" if "정수" in str(e) else "bad_value")]
    return [out]


def us_snap(row: dict[str, object]) -> list[Candidate]:
    """ET us_board.db `snap` → `prv_market.stock_snapshot(market='US')`. 금액은 달러 그대로.

    ET 미국 거래대금은 종가 × 거래량으로 만든 값이라(board/us/sources.py) `turnover_is_estimate`.
    """
    source = source_of(row.get("source"))
    if (drop := _source_drop(source)) is not None:
        return [drop]
    code = us_ticker(row.get("ticker"))
    if code is None:
        return [Drop("bad_code")]
    day = parse_date(row.get("asof"))
    if day is None:
        return [Drop("bad_date")]
    try:
        close = dec(row.get("close"))
        turnover = dec(row.get("turnover"))
        extra = {
            k: row.get(k) for k in ("sector_raw", "industry_raw") if row.get(k) not in (None, "")
        }
        out: TargetRow = {
            "market": "US",
            "code": code,
            "trade_date": day,
            "source": source,
            "venue": "",
            "name": row.get("name"),
            "segment": row.get("exchange"),
            "sector": row.get("sector"),
            "industry": row.get("industry"),
            "close": close,
            "chg_pct": dec(row.get("chg_pct")),
            "volume": whole(row.get("volume")),
            "turnover": turnover,
            "turnover_is_estimate": True,
            "mktcap": dec(row.get("mktcap")),
            "shares": None,
            "extra": extra,
            "quality": "ok" if close is not None else "invalid",
            "received_at": None,
        }
    except ValueError as e:
        return [Drop("volume_not_integer" if "정수" in str(e) else "bad_value")]
    return [out]


# ── prv_market.daily_bar ─────────────────────────────────────────────────────────────────

# 출처별 수정주가 여부. KRX OpenAPI·금융위 시세는 비수정 가격(conflict_map §1.13 ⚠). 그 밖은
# 모른다 → NULL(지어내지 않는다) [확인 필요]
_UNADJUSTED: Final = frozenset({"krx", "datago"})


def daily_bar(market: str) -> Transform:
    """ET `px(code|ticker, asof, open, high, low, close, volume, source)` →
    `prv_market.daily_bar`."""

    def run(row: dict[str, object]) -> list[Candidate]:
        source = source_of(row.get("source"))
        if (drop := _source_drop(source)) is not None:
            return [drop]
        code = kr_code(row.get("code")) if market == "KR" else us_ticker(row.get("ticker"))
        if code is None:
            return [Drop("bad_code")]
        day = parse_date(row.get("asof"))
        if day is None:
            return [Drop("bad_date")]
        try:
            close = dec(row.get("close"))
            out: TargetRow = {
                "market": market,
                "asset": "stock",
                "code": code,
                "trade_date": day,
                "source": source,
                "venue": "KRX" if market == "KR" and source.lower() == "krx" else "",
                "open": dec(row.get("open")),
                "high": dec(row.get("high")),
                "low": dec(row.get("low")),
                "close": close,
                "volume": whole(row.get("volume")),
                "turnover": None,
                "adjusted": False if source.lower() in _UNADJUSTED else None,
                "quality": "ok" if close is not None else "invalid",
                "received_at": None,
            }
        except ValueError as e:
            return [Drop("volume_not_integer" if "정수" in str(e) else "bad_value")]
        return [out]

    return run


# ── prv_flows.stock_investor_daily ───────────────────────────────────────────────────────


def _flow(
    code: str, day: date, investor: str, *, qty: int | None, value: int | None, quality: str
) -> TargetRow:
    return {
        "code": code,
        "trade_date": day,
        "investor": investor,
        "source": "kis",
        "venue": "",
        "net_qty": qty,
        "net_value": value,
        "unit": "krw",
        "quality": quality,
        "received_at": None,
    }


def _json_list(v: object) -> list[object] | None:
    if v is None:
        return []
    try:
        out = json.loads(str(v))
    except ValueError:
        return None
    return out if isinstance(out, list) else None


def sd_flow_cache(row: dict[str, object]) -> list[Candidate]:
    """SD `flow_cache`(종목별 20일 블록 — 날짜·수량·금액 JSON 배열) → 날짜 × (외국인·기관) 행.

    KIS 행만(§8.4). SD 의 금액은 '순매매량 × 종가' 추정이라 quality=estimated
    (server.py `_save_flow`).
    """
    source = source_of(row.get("source"))
    if not source:
        return [Drop("no_source")]
    if is_naver(source):
        return [Drop("naver")]
    if not source.lower().startswith("kis"):
        return [Drop("not_kis")]
    code = kr_code(row.get("code"))
    if code is None:
        return [Drop("bad_code")]
    arrays = {
        k: _json_list(row.get(k))
        for k in (
            "dates_json",
            "foreign_shares_json",
            "inst_shares_json",
            "foreign_value_json",
            "inst_value_json",
        )
    }
    if any(v is None for v in arrays.values()):
        return [Drop("bad_json")]
    dates = arrays["dates_json"] or []
    series = {k: v or [] for k, v in arrays.items() if k != "dates_json"}
    if any(len(v) not in (0, len(dates)) for v in series.values()):
        return [Drop("array_length")]
    out: list[Candidate] = []
    for i, raw_day in enumerate(dates):
        day = parse_date(raw_day)
        for investor, qk, vk in (
            ("foreign", "foreign_shares_json", "foreign_value_json"),
            ("institution", "inst_shares_json", "inst_value_json"),
        ):
            if day is None:
                out.append(Drop("bad_date"))
                continue
            try:
                qty = whole(series[qk][i]) if series[qk] else None
                vd = dec(series[vk][i]) if series[vk] else None
            except ValueError:
                out.append(Drop("bad_value"))
                continue
            value = int(vd.to_integral_value(rounding=ROUND_HALF_EVEN)) if vd is not None else None
            if qty is None and value is None:
                out.append(Drop("bad_value"))
                continue
            out.append(_flow(code, day, investor, qty=qty, value=value, quality="estimated"))
    return out


def et_stockflows(row: dict[str, object]) -> list[Candidate]:
    """ET `state/<날짜>/stockflows.json` 항목(by_code 하나) → 투자자(기관·외국인·개인)별 행.

    unit 억원 → net_value(원), 주 → net_qty. KIS 항목만 — 네이버 폴백 항목(source='naver', 금액은
    종가 추정 `amt_est`)은 버린다(§8.4).
    """
    source = source_of(row.get("source"))
    if (drop := _source_drop(source)) is not None:
        return [drop]
    if not source.lower().startswith("kis"):
        return [Drop("not_kis")]
    code = kr_code(row.get("code"))
    if code is None:
        return [Drop("bad_code")]
    day = parse_date(row.get("as_of"))
    if day is None:
        return [Drop("bad_date")]
    unit = str(row.get("unit") or "")
    if unit not in ("억원", "주"):
        return [Drop("unknown_unit")]
    out: list[Candidate] = []
    for ko, investor in _ET_INVESTORS.items():
        if row.get(ko) is None:
            continue
        try:
            if unit == "억원":
                rec = _flow(code, day, investor, qty=None, value=eok_to_won(row[ko]), quality="ok")
            else:
                rec = _flow(code, day, investor, qty=whole(row[ko]), value=None, quality="ok")
        except ValueError:
            out.append(Drop("bad_value"))
            continue
        out.append(rec)
    return out or [Drop("bad_value")]


def kr_flows(row: dict[str, object]) -> list[Candidate]:
    """ET monitor/kr `cache/flows.json` 한 (종목, 날짜) → f(외국인)·o(기관)·p(개인) 행(억원 → 원).

    원본이 빈 값을 0.0 으로 채웠다(monitor/kr/flows.py `_amounts` 의 `or 0.0`) — 0 은 '받은 0' 과
    '못 받음' 을 가를 수 없어 quality=estimated 로 둔다.
    """
    source = source_of(row.get("source"))
    if (drop := _source_drop(source)) is not None:
        return [drop]
    if not source.lower().startswith("kis"):
        return [Drop("not_kis")]
    if str(row.get("unit") or "") != "억원":
        return [Drop("unknown_unit")]
    code = kr_code(row.get("code"))
    if code is None:
        return [Drop("bad_code")]
    day = parse_date(row.get("date"))
    if day is None:
        return [Drop("bad_date")]
    out: list[Candidate] = []
    for k, investor in (("f", "foreign"), ("o", "institution"), ("p", "individual")):
        if k not in row:
            continue
        try:
            value = eok_to_won(row[k])
        except ValueError:
            out.append(Drop("bad_value"))
            continue
        if value is None:
            out.append(Drop("bad_value"))
            continue
        quality = "estimated" if value == 0 else "ok"
        out.append(_flow(code, day, investor, qty=None, value=value, quality=quality))
    return out or [Drop("bad_value")]


# ── prv_alerts.tg_inbox ──────────────────────────────────────────────────────────────────


def _str_list(v: object) -> list[str] | None:
    if v is None:
        return []
    if not isinstance(v, list):
        return None
    return [str(x) for x in v]


def et_inbox(row: dict[str, object]) -> list[Candidate]:
    """ET `state/inbox.json` 항목 → `prv_alerts.tg_inbox`(§5.8 — 항목 모양 그대로)."""
    if row.get("_bad"):
        return [Drop("bad_value")]
    uid = row.get("update_id")
    if isinstance(uid, bool) or not isinstance(uid, int):
        return [Drop("no_update_id")]
    kind = row.get("kind")
    if kind not in ("x", "other"):
        return [Drop("bad_kind")]
    chat = row.get("chat_id")
    urls = _str_list(row.get("urls"))
    x_ids = _str_list(row.get("x_ids"))
    if urls is None or x_ids is None:
        return [Drop("bad_value")]
    via = row.get("text_via")
    return [
        {
            "update_id": uid,
            "chat_id": chat if isinstance(chat, int) and not isinstance(chat, bool) else None,
            "date": parse_aware(row.get("date")),
            "text": str(row.get("text") or ""),
            "urls": urls,
            "x_ids": x_ids,
            "author": str(row["author"]) if row.get("author") is not None else None,
            "kind": kind,
            "text_via": via if via in ("message", "oembed") else None,
            "received_at": None,
        }
    ]
