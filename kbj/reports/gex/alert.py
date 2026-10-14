"""장중 GEX 레벨 알림 조건(순수 — D-P7-17 [제안], config/gex.yaml `alert`).

직전 상태(`AlertState` — 처리기가 Redis `gex:alert:last` 에 JSON 으로 둔다, 표 아님)와 이번 사이클
스냅샷(`lines.snapshot(rows, exclude=ALERT_EXCLUDE)` — visible·invalid/stale 뺀 값)을 비교한다.

조건(설정 `conditions` 에 든 것만)
- `flip_cross`: 현재가(F)가 Flip(범위 all)을 건넘 — Flip 위·아래(부호 체제)가 직전 사이클과 다름.
  Flip 없음·같음(0)·현재가 없음이면 직전 쪽을 그대로 둔다(건넘으로 세지 않는다).
- `wall_move`: 콜월·풋월 행사가가 바뀜(각각 따로 — 대상 `wall_move:call`·`wall_move:put`).
- `outside_1sigma`: 현재가가 **세션 기준 ±1σ 범위** 밖으로 나감. ±1σ 는 F 를 가운데 두고 매 사이클
  다시 계산되므로(§G3.8) 그 사이클의 범위와 비교하면 늘 안이다 — 그래서 그 세션에서 처음 본 ±1σ
  (달력) 범위를 기준으로 고정한다 [제안 — 확인 필요]. 안 → 밖(위·아래)으로 바뀔 때 한 번(대상
  `outside_1sigma:up`·`:down`), 안으로 돌아오면 다시 셀 수 있다.

같은 대상의 재발송은 notify 정책 쿨다운(`alert.gex_level` 600초 — 미끄러지는 창)이 막는다. 세션
(거래일·주간/야간)이 바뀌면 기준을 새로 잡고 그 사이클에는 보내지 않는다(비교할 직전 값이 없다).
"""

from __future__ import annotations

import json
from collections.abc import Collection
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from kbj.core.quality import Quality

from .lines import LevelSnapshot, cycle_label, fmt_pt, side_text

__all__ = [
    "KIND",
    "Alert",
    "AlertCondition",
    "AlertState",
    "blocked_reason",
    "evaluate",
]

KIND: Final = "alert.gex_level"
AlertCondition = Literal["flip_cross", "wall_move", "outside_1sigma"]
STATE_VERSION: Final = 1


@dataclass(frozen=True)
class Alert:
    condition: AlertCondition
    subject: str  # notify 쿨다운 대상(같은 대상은 600초 안에 다시 나가지 않는다)
    text: str  # 텔레그램 HTML


@dataclass(frozen=True)
class AlertState:
    """직전 상태 — 세션(trade_date·session)마다 새로 잡는다."""

    trade_date: date
    session: str
    ts: datetime
    side: int | None  # +1 Flip 위 · −1 아래 · None 모름
    call_wall: float | None
    put_wall: float | None
    band_lower: float | None  # 세션 기준 ±1σ(달력) — 그 세션에서 처음 본 범위
    band_upper: float | None
    outside: int = 0  # 0 안 · +1 위로 밖 · −1 아래로 밖

    def dumps(self) -> str:
        d: dict[str, Any] = asdict(self)
        d["trade_date"] = self.trade_date.isoformat()
        d["ts"] = self.ts.isoformat()
        d["v"] = STATE_VERSION
        return json.dumps(d, ensure_ascii=False, sort_keys=True)

    @classmethod
    def loads(cls, raw: str | bytes | None) -> AlertState | None:
        """저장된 JSON → 상태. 비었거나 모양이 다르면 None(기준을 새로 잡는다 — 알림 없이)."""
        if raw is None:
            return None
        try:
            d = json.loads(raw)
            if not isinstance(d, dict) or d.get("v") != STATE_VERSION:
                return None
            return cls(
                trade_date=date.fromisoformat(d["trade_date"]),
                session=str(d["session"]),
                ts=datetime.fromisoformat(d["ts"]),
                side=_opt_int(d.get("side")),
                call_wall=_opt_float(d.get("call_wall")),
                put_wall=_opt_float(d.get("put_wall")),
                band_lower=_opt_float(d.get("band_lower")),
                band_upper=_opt_float(d.get("band_upper")),
                outside=int(d.get("outside", 0)),
            )
        except (ValueError, KeyError, TypeError):
            return None


def _opt_float(v: object) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if not isinstance(v, int | float):
        raise TypeError(f"수가 아니다: {v!r}")
    return float(v)


def _opt_int(v: object) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise TypeError(f"정수가 아니다: {v!r}")
    return v


def blocked_reason(s: LevelSnapshot | None, now: datetime, stale_after: timedelta) -> str | None:
    """보내면 안 되는 사유(없으면 None) — 산출 없음·품질 invalid/stale·사이클 지연."""
    if s is None:
        return "레벨 산출 없음"
    if s.quality in (Quality.INVALID, Quality.STALE):
        return f"품질 {s.quality.value} — 보내지 않는다"
    age = now - s.ts
    if age > stale_after:
        return f"마지막 사이클이 {int(age.total_seconds())}초 전 — 지연 산출로는 보내지 않는다"
    if not s.has_values:
        return "visible 레벨 없음(섀도·꺼짐)"
    return None


def _baseline(s: LevelSnapshot) -> AlertState:
    side = s.side if s.side in (1, -1) else None
    return AlertState(
        trade_date=s.trade_date,
        session=s.session,
        ts=s.ts,
        side=side,
        call_wall=s.call_wall,
        put_wall=s.put_wall,
        band_lower=s.lower,
        band_upper=s.upper,
        outside=_outside(s.price, s.lower, s.upper),
    )


def _outside(price: float | None, lo: float | None, hi: float | None) -> int:
    if price is None or lo is None or hi is None:
        return 0
    if price > hi:
        return 1
    if price < lo:
        return -1
    return 0


def _tail(s: LevelSnapshot) -> str:
    return f" · {cycle_label(s)}"


def evaluate(
    prev: AlertState | None,
    s: LevelSnapshot,
    conditions: Collection[str],
) -> tuple[list[Alert], AlertState]:
    """보낼 알림과 새 상태.

    부르는 쪽이 `blocked_reason` 으로 먼저 거른다(막히면 상태를 바꾸지 않는다)."""
    if prev is None or (prev.trade_date, prev.session) != (s.trade_date, s.session):
        return [], _baseline(s)
    if s.ts <= prev.ts:
        return [], prev
    out: list[Alert] = []
    state = replace(prev, ts=s.ts)

    side = s.side if s.side in (1, -1) else None
    if side is not None:
        if "flip_cross" in conditions and prev.side is not None and side != prev.side:
            out.append(Alert(
                "flip_cross",
                "flip_cross",
                f"⚡ <b>GEX Flip 건넘</b> — F {fmt_pt(s.price)} 이 Flip {fmt_pt(s.flip)} "
                f"{'위로' if side > 0 else '아래로'}(직전 {side_text(prev.side)}){_tail(s)}",
            ))  # fmt: skip
        state = replace(state, side=side)

    for which, label, new, old in (
        ("call", "콜월", s.call_wall, prev.call_wall),
        ("put", "풋월", s.put_wall, prev.put_wall),
    ):
        if new is None:
            continue
        if "wall_move" in conditions and old is not None and new != old:
            out.append(Alert(
                "wall_move",
                f"wall_move:{which}",
                f"⚡ <b>GEX {label} 이동</b> {fmt_pt(old)} → {fmt_pt(new)} · F {fmt_pt(s.price)}"
                f"{_tail(s)}",
            ))  # fmt: skip
        state = replace(state, **{f"{which}_wall": new})

    lo, hi = prev.band_lower, prev.band_upper
    if lo is None or hi is None:
        # 세션 첫 사이클에 ±1σ 가 없었다 — 처음 본 범위를 기준으로(이 사이클은 세지 않는다)
        state = replace(state, band_lower=s.lower, band_upper=s.upper)
        state = replace(state, outside=_outside(s.price, s.lower, s.upper))
    elif s.price is not None:
        now_out = _outside(s.price, lo, hi)
        if "outside_1sigma" in conditions and now_out != 0 and now_out != prev.outside:
            up = now_out > 0
            edge = f"상단 {fmt_pt(hi)}" if up else f"하단 {fmt_pt(lo)}"
            out.append(Alert(
                "outside_1sigma",
                f"outside_1sigma:{'up' if up else 'down'}",
                f"⚡ <b>GEX ±1σ 밖</b> — F {fmt_pt(s.price)} {'>' if up else '<'} {edge}"
                f"(세션 기준 {fmt_pt(lo)}~{fmt_pt(hi)}){_tail(s)}",
            ))  # fmt: skip
        state = replace(state, outside=now_out)
    return out, state
