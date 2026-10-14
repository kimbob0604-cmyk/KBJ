"""GEX·진단 문장(순수) — 브리핑 줄(아침 ④·마감 GEX·진단)과 `/gex` 봇 요약(D-P7-16).

입력은 저장된 행 그대로다 — 레벨은 `prv_gex.levels` 한 사이클(`GexRepo.levels_latest(scope)` 결과),
진단은 `prv_diag.verdict`·`prv_diag.fear_greed` 행. 계산하지 않는다(엔진이 저장한 값을 글자로만).

규칙
- **플래그(D-P7-11·§10 Q3)**: 행의 `flag`(계산 당시 플래그)가 visible 인 값만 싣는다. shadow 는 값을
  싣지 않고 '섀도 검증 중', off 는 없는 것으로 본다.
- **품질**: invalid 행은 쓰지 않는다(절대 규칙 2). 알림은 stale 도 뺀다(`ALERT_EXCLUDE`).
- **현재가(F)**: Flip 행 detail 의 `f_ref`(엔진이 쓴 기준 F — metrics §G3.4·§G3.5, API 리더와 같다).
  없으면 ±1σ(달력) 행 detail 의 `F`, 그것도 없으면 Flip·Flip 거리에서 되돌린 값
  F = Flip ÷ (1 − 거리/100)(§G3.5 식을 거꾸로) — 이때는 `estimated`.
- **체제**: F > Flip 이면 'Flip 위'(+1), F < Flip 이면 'Flip 아래'(−1), Flip 없음·같음 0
  (metrics §D8 GEX 체제와 같은 부호).
- 단위: 레벨 pt(소수 둘째), Flip 거리 %(소수 둘째), 공포탐욕 0~100(소수 첫째).
"""

from __future__ import annotations

import html
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Protocol

from kbj.core.gex.records import LevelRecord, LevelScope
from kbj.core.p7_rows import FearGreedRow, VerdictRow
from kbj.core.quality import Quality
from kbj.core.time import to_kst
from kbj.engines.diagnosis.verdict import top_reasons

__all__ = [
    "ALERT_EXCLUDE",
    "BAND_LABELS",
    "BRIEF_EXCLUDE",
    "EVENT_LABELS",
    "SESSION_LABELS",
    "VERDICT_LABELS",
    "LevelSnapshot",
    "command_reader",
    "command_text",
    "cycle_label",
    "diag_lines",
    "fmt_pt",
    "gex_lines",
    "side_text",
    "snapshot",
]

SOURCE: Final = "KBJ:gex.engine(prv_gex.levels)"
BRIEF_EXCLUDE: Final[frozenset[str]] = frozenset({"invalid"})
ALERT_EXCLUDE: Final[frozenset[str]] = frozenset({"invalid", "stale"})
SESSION_LABELS: Final[Mapping[str, str]] = {"day": "주간", "night": "야간"}
VERDICT_LABELS: Final[Mapping[str, str]] = {
    "risk_on": "위험 선호",
    "neutral": "중립",
    "risk_off": "위험 회피",
    "hold": "보류",
}
BAND_LABELS: Final[Mapping[str, str]] = {
    "extreme_fear": "극단적 공포",
    "fear": "공포",
    "neutral": "중립",
    "greed": "탐욕",
    "extreme_greed": "극단적 탐욕",
}
EVENT_LABELS: Final[Mapping[str, str]] = {
    "expiry": "만기",
    "mpc": "금통위",
    "export_flash": "수출 속보",
    "earnings_cluster": "실적 몰림",
}
TOP_REASONS: Final = 3  # config/diag.yaml verdict.top_reasons 와 같다(metrics §D8 '기여 큰 셋')

_Q_ORDER: Final[Mapping[Quality, int]] = {
    Quality.OK: 0,
    Quality.ESTIMATED: 1,
    Quality.STALE: 2,
    Quality.INVALID: 3,
}
_LEVELS: Final = ("flip", "flip_distance", "call_wall", "put_wall", "expected_move_calendar")


def esc(s: object) -> str:
    return html.escape(str(s), quote=False)


def fmt_pt(v: float | None) -> str:
    return "—" if v is None else f"{v:,.2f}"


def _worst(qs: Sequence[Quality]) -> Quality:
    return max(qs, key=lambda q: _Q_ORDER[q]) if qs else Quality.OK


def _num(d: Mapping[str, Any], key: str) -> float | None:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, int | float):
        return None
    return float(v)


@dataclass(frozen=True)
class LevelSnapshot:
    """한 사이클·한 범위의 레벨(visible·쓸 수 있는 품질만). 값이 없으면 None.

    flip_none: Flip 행이 visible·쓸 수 있는데 값이 없다(범위 안 교차 없음 — §G3.4 none).
    shadow: 섀도라 싣지 않은 레벨 이름. quality: 실은 행 중 가장 나쁜 것(현재가를 되돌렸으면
    estimated 이상).
    """

    ts: datetime
    trade_date: date
    session: str
    scope: str
    price: float | None
    flip: float | None
    flip_none: bool
    multi_cross: bool
    flip_distance: float | None
    call_wall: float | None
    put_wall: float | None
    sigma: float | None
    lower: float | None
    upper: float | None
    shadow: tuple[str, ...]
    quality: Quality
    reasons: tuple[str, ...] = ()

    @property
    def has_values(self) -> bool:
        vals = (self.price, self.flip, self.call_wall, self.put_wall, self.sigma)
        return self.flip_none or any(v is not None for v in vals)

    @property
    def side(self) -> int | None:
        """+1 Flip 위 · −1 Flip 아래 · 0 Flip 없음·같음 · None 현재가 없음."""
        if self.price is None:
            return None
        if self.flip is None:
            return 0 if self.flip_none else None
        if self.price > self.flip:
            return 1
        if self.price < self.flip:
            return -1
        return 0


def snapshot(
    rows: Sequence[LevelRecord], *, exclude: Collection[str] = BRIEF_EXCLUDE
) -> LevelSnapshot | None:
    """`levels_latest` 결과 → 스냅샷. 행이 없으면 None. 여러 사이클이 섞여 오면 가장 늦은 것만."""
    if not rows:
        return None
    ts = max(r.ts for r in rows)
    cyc = [r for r in rows if r.ts == ts]
    by: dict[str, LevelRecord] = {}
    shadow: list[str] = []
    for r in cyc:
        if r.name not in _LEVELS:
            continue
        if r.flag == "shadow":
            shadow.append(r.name)
            continue
        if r.flag != "visible" or r.quality in exclude:
            continue
        by[r.name] = r
    used: list[Quality] = [Quality(r.quality) for r in by.values()]
    reasons: list[str] = []

    def val(name: str) -> float | None:
        r = by.get(name)
        return r.value if r is not None else None

    flip_row = by.get("flip")
    move = by.get("expected_move_calendar")
    price = _num(flip_row.detail, "f_ref") if flip_row is not None else None
    if price is None and move is not None:
        price = _num(move.detail, "F")
    dist = val("flip_distance")
    if price is None and flip_row is not None and flip_row.value is not None and dist is not None:
        if dist != 100:
            price = flip_row.value / (1 - dist / 100)
            used.append(Quality.ESTIMATED)
            reasons.append("현재가: Flip·Flip 거리에서 되돌린 값(f_ref 없음)")
    sigma = val("expected_move_calendar")
    lower = _num(move.detail, "lower") if move is not None else None
    upper = _num(move.detail, "upper") if move is not None else None
    if sigma is not None and price is not None and (lower is None or upper is None):
        lower, upper = price - sigma, price + sigma
    if sigma is None:
        lower = upper = None
    first = cyc[0]
    return LevelSnapshot(
        ts=ts,
        trade_date=first.trade_date,
        session=first.session,
        scope=first.scope,
        price=price,
        flip=val("flip"),
        flip_none=flip_row is not None and flip_row.value is None,
        multi_cross=bool(flip_row.detail.get("multi_cross")) if flip_row is not None else False,
        flip_distance=dist,
        call_wall=val("call_wall"),
        put_wall=val("put_wall"),
        sigma=sigma,
        lower=lower,
        upper=upper,
        shadow=tuple(sorted(set(shadow))),
        quality=_worst(used),
        reasons=tuple(reasons),
    )


def side_text(side: int | None) -> str:
    return {1: "Flip 위", -1: "Flip 아래", 0: "Flip 근처"}.get(side if side is not None else 9, "")


def gex_lines(s: LevelSnapshot) -> list[str]:
    """브리핑 GEX 줄(텔레그램 HTML). 값이 하나도 없으면 섀도·없음 사유 한 줄."""
    if not s.has_values:
        if s.shadow:
            return ["GEX 레벨 섀도 검증 중 — 싣지 않는다"]
        return []
    out: list[str] = []
    head = f"F {fmt_pt(s.price)}"
    if s.flip is not None:
        d = f" {s.flip_distance:+.2f}%" if s.flip_distance is not None else ""
        cross = " · 교차 여럿" if s.multi_cross else ""
        out.append(f"{head} · Flip {fmt_pt(s.flip)}({side_text(s.side)}{d}{cross})")
    elif s.flip_none:
        out.append(f"{head} · Flip 없음(범위 안 부호 같음)")
    else:
        out.append(head)
    if s.call_wall is not None or s.put_wall is not None:
        out.append(f"콜월 {fmt_pt(s.call_wall)} · 풋월 {fmt_pt(s.put_wall)}")
    if s.sigma is not None:
        out.append(f"±1σ {fmt_pt(s.lower)}~{fmt_pt(s.upper)}(±{s.sigma:,.2f}pt, 달력 기준)")
    if s.quality is not Quality.OK:
        why = f" — {esc('; '.join(s.reasons))}" if s.reasons else ""
        out.append(f"<i>품질 {s.quality.value}{why}</i>")
    return out


def diag_lines(
    verdict: VerdictRow | None,
    fear_greed: FearGreedRow | None,
    fear_greed_prev: FearGreedRow | None = None,
) -> list[str]:
    """진단 줄 — 종합 판정(근거 셋·이벤트 주의)·공포탐욕(구간·전일 대비).

    없는 쪽은 줄을 내지 않는다."""
    out: list[str] = []
    if verdict is not None:
        label = VERDICT_LABELS.get(verdict.label, verdict.label)
        mark = " <i>(잠정)</i>" if verdict.quality is Quality.ESTIMATED else ""
        if verdict.label == "hold" or verdict.score is None:
            out.append(f"종합 판정: {esc(label)}(성분 부족){mark}")
        else:
            why = top_reasons(verdict.parts, TOP_REASONS)
            tail = f" — 근거 {esc(' · '.join(why))}" if why else ""
            out.append(f"종합 판정: {esc(label)}(점수 {verdict.score:+d}){tail}{mark}")
        if verdict.event_flags:
            names = "·".join(EVENT_LABELS.get(f, f) for f in verdict.event_flags)
            out.append(f"이벤트 주의: {esc(names)}")
    if fear_greed is not None:
        if fear_greed.score is None or fear_greed.band is None:
            out.append("공포탐욕: 값 없음(성분 부족)")
        else:
            line = f"공포탐욕 {fear_greed.score:.1f} {esc(BAND_LABELS[fear_greed.band])}"
            prev = fear_greed_prev
            if (
                prev is not None
                and prev.score is not None
                and prev.trade_date < fear_greed.trade_date
            ):
                ch = fear_greed.score - prev.score
                pb = BAND_LABELS.get(prev.band or "", "")
                moved = f", {esc(pb)}에서" if prev.band != fear_greed.band and pb else ""
                line += f"(전일 {prev.score:.1f}, {ch:+.1f}{moved})"
            if fear_greed.quality is Quality.ESTIMATED:
                line += " <i>(잠정)</i>"
            out.append(line)
    return out


def cycle_label(s: LevelSnapshot) -> str:
    k = to_kst(s.ts)
    return f"{k.month:02d}/{k.day:02d} {k:%H:%M} KST {SESSION_LABELS.get(s.session, s.session)}"


def command_text(
    s: LevelSnapshot | None, now: datetime, *, stale_after: timedelta = timedelta(minutes=3)
) -> str:
    """`/gex` 답 한 덩어리(로그인 등급 채팅만 — 명령 허용 대화). 산출이 없으면 그렇다고만 적는다."""
    if s is None:
        return "📐 <b>GEX</b>\n산출 없음 — gex.engine 이 아직 레벨을 저장하지 않았다"
    head = f"📐 <b>GEX({esc(s.scope)})</b> · {cycle_label(s)} 사이클"
    lines = gex_lines(s) or ["값 없음"]
    age = now - s.ts
    if age > stale_after:
        lines.append(f"<i>마지막 사이클 {int(age.total_seconds() // 60)}분 전</i>")
    return "\n".join([head, *lines])


class _Levels(Protocol):
    def levels_latest(
        self, scope: LevelScope, upto: datetime | None = None
    ) -> list[LevelRecord]: ...


def command_reader(
    repo: _Levels, now: Callable[[], datetime], scope: LevelScope = "all"
) -> Callable[[], str]:
    """`/gex` 읽기 함수 — 부를 때마다 저장소(`GexRepo.levels_latest`)를 읽어 요약 한 덩어리."""

    def read() -> str:
        t = now()
        return command_text(snapshot(repo.levels_latest(scope, upto=t)), t)

    return read
