"""SD 알림 규칙 → DSL v1 변환 표(docs/p8_design.md §4.1.5 — 이관, 묶음 JNL). 순수.

SD `cache/alert_rules.json` 의 `{"rules": [{code, market, type, value, enabled, name, triggered_at
…}]}`
한 항목을 DSL 규칙(dict — 검사는 쓰기 모듈 `kbj.services.personal.rules` 가 `validate_rule` 로)으로
바꾼다. 못 바꾸면 `Unconverted`(사유 코드만 — 값은 넣지 않는다).

| SD `type` | DSL |
|---|---|
| price_above·price_below | close >= v · close <= v |
| change_up·change_down | change_pct >= v · change_pct <= v |
| rsi_oversold·rsi_overbought | rsi(14) <= v(기본 30) · >= v(70) |
| macd_golden·macd_dead | macd.line cross_above/below macd.signal |
| foreign_strong_buy | foreign_streak >= v(3) |
| foreign_cum_buy | foreign_sum(5) >= v × 1e8 (SD 는 억원) |
| 미국 종목(market us) | 변환하지 않음(`not_kr` — 장중 미국 평가 없음) |

그 밖의 SD 종류(bb_upper·bb_lower·volume_spike — SD 평가기도 판정하지 않던 종류)는
`unsupported_type`.
mode 는 both(SD 는 장중 30분마다 봤다), 우니버스는 그 종목 하나, SD '하루 1번(triggered_at)' 은
`cooldown_min: 1440`. 꺼진 SD 규칙은 꺼진 채로(`enabled` 를 따로 돌려준다).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal

__all__ = [
    "COOLDOWN_MIN",
    "SD_TYPES",
    "Converted",
    "Unconverted",
    "UnconvertedReason",
    "convert",
]

COOLDOWN_MIN: Final = 1440
EOK: Final = 100_000_000
_CODE = re.compile(r"[0-9A-Z]{6}")
UnconvertedReason = Literal["not_kr", "bad_code", "bad_value", "unsupported_type", "bad_row"]


@dataclass(frozen=True)
class Converted:
    rule: dict[str, Any]
    enabled: bool
    sd_type: str


@dataclass(frozen=True)
class Unconverted:
    index: int
    sd_type: str
    reason: UnconvertedReason


def _num(v: object, default: float | None) -> float | None:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return None
    try:
        x = float(str(v).replace(",", ""))
    except ValueError:
        return None
    return x if math.isfinite(x) else None


def _plain(x: float) -> int | float:
    return int(x) if x.is_integer() else x


def _cmp(
    ind: str, op: str, p: Mapping[str, Any] | None = None
) -> Callable[[float], dict[str, Any]]:
    def leaf(v: float) -> dict[str, Any]:
        c: dict[str, Any] = {"ind": ind, "op": op, "value": _plain(v)}
        if p:
            c["p"] = dict(p)
        return c

    return leaf


def _macd_cross_cond(op: str) -> Callable[[float], dict[str, Any]]:
    def leaf(_: float) -> dict[str, Any]:
        return {"ind": "macd", "field": "line", "op": op, "ref": {"ind": "macd", "field": "signal"}}

    return leaf


# 종류 → (기본값(None 이면 값 필수), 값 변환, 조건 만들기)
_TABLE: Final[
    dict[str, tuple[float | None, Callable[[float], float], Callable[[float], dict[str, Any]]]]
] = {
    "price_above": (None, lambda v: v, _cmp("close", ">=")),
    "price_below": (None, lambda v: v, _cmp("close", "<=")),
    "change_up": (None, lambda v: v, _cmp("change_pct", ">=")),
    "change_down": (None, lambda v: v, _cmp("change_pct", "<=")),
    "rsi_oversold": (30.0, lambda v: v, _cmp("rsi", "<=", {"n": 14})),
    "rsi_overbought": (70.0, lambda v: v, _cmp("rsi", ">=", {"n": 14})),
    "macd_golden": (0.0, lambda v: v, _macd_cross_cond("cross_above")),
    "macd_dead": (0.0, lambda v: v, _macd_cross_cond("cross_below")),
    "foreign_strong_buy": (3.0, lambda v: float(math.floor(v)), _cmp("foreign_streak", ">=")),
    "foreign_cum_buy": (100.0, lambda v: v * EOK, _cmp("foreign_sum", ">=", {"n": 5})),
}
SD_TYPES: Final = tuple(_TABLE)

_LABEL: Final = {
    "price_above": "가격 이상",
    "price_below": "가격 이하",
    "change_up": "등락률 이상",
    "change_down": "등락률 이하",
    "rsi_oversold": "RSI 과매도",
    "rsi_overbought": "RSI 과매수",
    "macd_golden": "MACD 골든크로스",
    "macd_dead": "MACD 데드크로스",
    "foreign_strong_buy": "외국인 연속 순매수",
    "foreign_cum_buy": "외국인 5일 누적 순매수",
}


def _code(v: object) -> str | None:
    if isinstance(v, bool):
        return None
    s = str(v if v is not None else "").strip().upper()
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)
    return s if _CODE.fullmatch(s) else None


def convert(item: Mapping[str, Any], index: int) -> Converted | Unconverted:
    """SD 규칙 한 항목(파일 안 순서 index — 0부터) → DSL 규칙 또는 못 바꾼 사유."""
    sd_type = str(item.get("type") or "")[:40]
    market = str(item.get("market") or "kr").strip().lower()
    if market != "kr":
        return Unconverted(index, sd_type, "not_kr")
    spec = _TABLE.get(sd_type)
    if spec is None:
        return Unconverted(index, sd_type, "unsupported_type")
    code = _code(item.get("code"))
    if code is None:
        return Unconverted(index, sd_type, "bad_code")
    default, scale, leaf = spec
    v = _num(item.get("value"), default)
    if v is None or (sd_type.startswith("price_") and v <= 0):
        return Unconverted(index, sd_type, "bad_value")
    name_raw = item.get("name")
    base = name_raw.strip() if isinstance(name_raw, str) and name_raw.strip() else code
    name = f"[SD] {base} · {_LABEL[sd_type]}"[:80]
    rule: dict[str, Any] = {
        "v": 1,
        "name": name,
        "universe": {"codes": [code]},
        "mode": "both",
        "when": {"all": [leaf(scale(v))]},
        "alert": {"cooldown_min": COOLDOWN_MIN},
    }
    enabled = item.get("enabled", True) is not False
    return Converted(rule, enabled, sd_type)
