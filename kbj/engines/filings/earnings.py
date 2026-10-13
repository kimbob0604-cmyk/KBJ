"""잠정실적 분류·서프라이즈·신뢰도·알림 본문·알림 후 성과.

docs/p4_design.md §4.2, docs/metrics.md §14.

승격 원본
- SD `earnings_signal_classifier.py`: `THRESHOLDS`:166(정본은 이제 `config/fin.yaml
  earnings.thresholds`), `classify_signal`:172(→ `classify_surprise` — 분기 순서·경계 그대로),
  `sanity_check_consensus`:34(→ `sanity` — 3관문 그대로, DB 조회 대신 인자), scope 가드(:296 —
  `scope_guard`).
- SD `_process_yoy_only`:411 은 **바뀌었다**(설계 §4.2·metrics §14 가 정본): 흑자전환·적자전환을
  우선순위 1, 적자 축소 ≥ 50% 를 2, YoY ±20%(BIG)·±10% 를 1·2 로 나눈다(SD 는 TURNAROUND·SHOCK 2,
  +50%·−30% 를 3 으로 봤다). 분모 = |전년동기|, 전년동기 0 이면 비율 없음(전환 판정만).
- SD `earnings_alert_writer.build_fallback`:299 → `alert_text`(템플릿 — **LLM 을 쓰지 않는다**,
  Ollama 폐기. 본문 숫자는 모두 입력 행에서 — 절대 규칙 3).
- SD `earnings_telegram_sender.backfill_alert_performance`:217 → `alert_base_day`·`perf_days`·
  `perf_pct`(SD 는 달력일 뒤 첫 종가였다 — 이제 **거래일**, 기준은 알림 시각 직전 마감 종가).

단위: 금액은 원(SD 는 백만원·억원이 섞였다 — 비율만 쓰므로 분류는 같다). 비율은 % 실수, 반올림하지
않는다(SD `_build_signal` 은 소수 둘째 자리로 잘랐다 — 분류에는 쓰지 않던 값이다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Final, Literal

from kbj.config.fin import EarningsThresholds
from kbj.core.calendar import TradingCalendar
from kbj.core.fin_rows import FiscalPeriod, PrelimRow, PrelimScope
from kbj.core.time import to_kst

__all__ = [
    "NO_DATA",
    "SIGNAL_LABEL",
    "EarningsSignal",
    "Figures",
    "Sanity",
    "alert_base_day",
    "alert_subject",
    "alert_text",
    "apply_sanity",
    "classify_prelim",
    "classify_surprise",
    "classify_yoy",
    "figures_of",
    "pct_change",
    "perf_days",
    "perf_pct",
    "period_label",
    "sanity",
    "scope_guard",
]

# 전년동기도 컨센서스도 없음(SD _process_yoy_only 의 NO_DATA — 우선순위 5)
NO_DATA: Final = "NO_DATA"

# 알림 본문의 신호 이름(한국어). 값은 분류 이름 그대로 저장하고 화면·알림에서만 바꾼다
SIGNAL_LABEL: Final[Mapping[str, str]] = {
    # YoY(공개 규칙)
    "TURNAROUND": "흑자전환(영업이익, 전년동기 대비)",
    "SHOCK": "적자전환(영업이익, 전년동기 대비)",
    "TURNAROUND_PARTIAL": "적자 축소(전년동기 대비 절반 이상)",
    "YOY_BIG_UP": "전년동기 대비 큰 폭 증가",
    "YOY_UP": "전년동기 대비 증가",
    "YOY_BIG_DOWN": "전년동기 대비 큰 폭 감소",
    "YOY_DOWN": "전년동기 대비 감소",
    "YOY_INLINE": "전년동기 수준",
    NO_DATA: "비교 불가(전년동기 값 없음)",
    # 서프라이즈(로그인 — 분기 컨센서스가 있을 때만)
    "BEAT_BIG": "컨센서스 크게 상회",
    "BEAT": "컨센서스 상회",
    "MISS_BIG": "컨센서스 크게 하회",
    "MISS": "컨센서스 하회",
    "TURNAROUND_FULL": "컨센서스 적자 → 실제 흑자",
    "SHOCK_FULL": "컨센서스 흑자 → 실제 적자",
    "REVENUE_MISS_OP_BEAT": "매출 하회·영업이익 상회",
    "REVENUE_BEAT_OP_INLINE": "매출 상회·영업이익 부합",
    "REVENUE_BEAT": "매출만 상회",
    "REVENUE_MISS": "매출만 하회",
    "INLINE": "컨센서스 부합",
}


@dataclass(frozen=True)
class Figures:
    """비교에 쓰는 두 값(원). 없으면 None — 0 으로 채우지 않는다."""

    revenue: int | float | None = None
    op: int | float | None = None


@dataclass(frozen=True)
class EarningsSignal:
    """분류 결과. `method`: yoy(전년동기 — 공개 규칙)·surprise(분기 컨센서스 — 로그인).

    `rev_pct`·`op_pct` = method 의 비교 비율(YoY 면 전년동기 대비, surprise 면 컨센 대비 %).
    `rev_qoq`·`op_qoq` = 직전 분기 대비(표시용 — 분류에 쓰지 않는다). `basis` = 분류에 쓴 지표.
    """

    signal: str
    priority: int
    method: Literal["yoy", "surprise"]
    basis: Literal["op", "revenue"] | None
    rev_pct: float | None = None
    op_pct: float | None = None
    rev_qoq: float | None = None
    op_qoq: float | None = None
    triggers: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Sanity:
    """컨센서스 신뢰도 3관문(SD `sanity_check_consensus` 의 반환 dict 그대로)."""

    is_valid: bool
    confidence: float
    gates: Mapping[str, str] = field(default_factory=dict[str, str])  # A·B·C → pass·warn·fail·skip
    reasons: tuple[str, ...] = ()
    recommendation: Literal["use", "use_with_caution", "skip"] = "use"


# ── 공통 ───────────────────────────────────────────────────────────────────────────────


def pct_change(cur: int | float | None, base: int | float | None) -> float | None:
    """(당기 − 기준) ÷ |기준| × 100. 하나라도 없거나 기준이 0 이면 None(0 으로 채우지 않는다)."""
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base) * 100.0


def figures_of(row: PrelimRow) -> tuple[Figures, Figures, Figures]:
    """잠정실적 행 → (당기, 전년동기, 직전분기)."""
    return (
        Figures(row.revenue_cur, row.op_cur),
        Figures(row.revenue_prev_y, row.op_prev_y),
        Figures(row.revenue_prev_q, row.op_prev_q),
    )


def _signal(
    name: str,
    priority: int,
    method: Literal["yoy", "surprise"],
    basis: Literal["op", "revenue"] | None,
    rev: float | None,
    op: float | None,
    triggers: Sequence[str] = (),
    notes: Sequence[str] = (),
    *,
    rev_qoq: float | None = None,
    op_qoq: float | None = None,
) -> EarningsSignal:
    return EarningsSignal(
        name, priority, method, basis, rev, op, rev_qoq, op_qoq, tuple(triggers), tuple(notes)
    )


# ── YoY 분류(공개 — metrics §14) ─────────────────────────────────────────────────────────

_REPORTED_LABEL: Final = {"revenue": "매출액", "op": "영업이익"}
_EPS_PP: Final = 1e-9


def _reported_notes(
    computed: Mapping[str, float | None], reported: Mapping[str, float | None], tol_pp: float
) -> list[str]:
    """본문 '전년동기대비%' 와 우리 계산이 tol_pp(%p) 넘게 다르면 사유 한 줄(값은 계산값)."""
    out: list[str] = []
    for key, label in _REPORTED_LABEL.items():
        rep = reported.get(key)
        calc = computed.get(key)
        if rep is None or calc is None:
            continue
        if (
            abs(rep - calc) > tol_pp + _EPS_PP
        ):  # 이진 소수 끝자리(20.1 − 20.0)로 경계가 흔들리지 않게
            out.append(
                f"본문 {label} 전년동기대비 {rep:+.1f}% 와 계산 {calc:+.1f}% 가 다르다"
                "(본문은 반올림·정정 반영일 수 있다 — 계산값으로 분류)"
            )
    return out


def classify_yoy(
    cur: Figures,
    prev_y: Figures,
    prev_q: Figures | None = None,
    reported_yoy: Mapping[str, float | None] | None = None,
    *,
    th: EarningsThresholds,
    tol_pp: float = 0.1,
) -> EarningsSignal:
    """전년동기 대비 분류 — 영업이익 기준(영업이익 칸이 없으면 매출).

    흑자전환(전년동기 < 0 < 당기) TURNAROUND(1) → 적자전환 SHOCK(1) → 적자 축소 ≥ 50%
    TURNAROUND_PARTIAL(2) → YoY ≥ +BIG YOY_BIG_UP(1) · ≥ +STANDARD YOY_UP(2) · ≤ −BIG
    YOY_BIG_DOWN(1) · ≤ −STANDARD YOY_DOWN(2) → 그 밖 YOY_INLINE(5). 경계는 포함. 전년동기가 0 이면
    비율이 없어 전환만 판정하고 그 밖은 YOY_INLINE. 전년동기 값이 하나도 없으면 NO_DATA(5) —
    2열 폼(당기·전기만)이 이렇다.
    """
    rev_yoy = pct_change(cur.revenue, prev_y.revenue)
    op_yoy = pct_change(cur.op, prev_y.op)
    pq = prev_q or Figures()
    rev_qoq = pct_change(cur.revenue, pq.revenue)
    op_qoq = pct_change(cur.op, pq.op)
    notes = _reported_notes({"revenue": rev_yoy, "op": op_yoy}, reported_yoy or {}, tol_pp)

    def done(
        name: str,
        prio: int,
        basis: Literal["op", "revenue"] | None,
        triggers: Sequence[str] = (),
        extra: Sequence[str] = (),
    ) -> EarningsSignal:
        return _signal(name, prio, "yoy", basis, rev_yoy, op_yoy, triggers, [*notes, *extra],
                       rev_qoq=rev_qoq, op_qoq=op_qoq)  # fmt: skip

    basis: Literal["op", "revenue"]
    if cur.op is not None and prev_y.op is not None:
        basis, c, p, pct = "op", cur.op, prev_y.op, op_yoy
    elif cur.revenue is not None and prev_y.revenue is not None:
        basis, c, p, pct = "revenue", cur.revenue, prev_y.revenue, rev_yoy
        notes.append("영업이익 전년동기 값이 없다 — 매출 기준으로 분류")
    else:
        return done(NO_DATA, 5, None, extra=["전년동기 값 없음 — 분류 불가(2열 폼 등)"])

    tag = "OP" if basis == "op" else "REV"
    if basis == "op":
        if p < 0 < c:
            return done("TURNAROUND", 1, basis, ["OP_TURNAROUND"])
        if p > 0 > c:
            return done("SHOCK", 1, basis, ["OP_SHOCK"])
        if p < 0 and c < 0 and abs(c) / abs(p) <= 1 - th.turnaround_partial_ratio:
            return done(
                "TURNAROUND_PARTIAL",
                2,
                basis,
                ["OP_LOSS_NARROWING"],
                [f"적자폭 {(1 - abs(c) / abs(p)) * 100:.0f}% 축소"],
            )
    if pct is None:
        return done("YOY_INLINE", 5, basis, extra=["전년동기 0 — 비율 없음(전환만 판정)"])
    if pct >= th.big_pct:
        return done("YOY_BIG_UP", 1, basis, [f"{tag}_YOY_BIG_UP"])
    if pct >= th.standard_pct:
        return done("YOY_UP", 2, basis, [f"{tag}_YOY_UP"])
    if pct <= -th.big_pct:
        return done("YOY_BIG_DOWN", 1, basis, [f"{tag}_YOY_BIG_DOWN"])
    if pct <= -th.standard_pct:
        return done("YOY_DOWN", 2, basis, [f"{tag}_YOY_DOWN"])
    return done("YOY_INLINE", 5, basis)


def classify_prelim(
    row: PrelimRow, th: EarningsThresholds, *, tol_pp: float = 0.1
) -> EarningsSignal:
    """잠정실적 행 하나의 YoY 분류(본문 '전년동기대비%' 대조 포함)."""
    cur, prev_y, prev_q = figures_of(row)
    return classify_yoy(cur, prev_y, prev_q, row.yoy_reported, th=th, tol_pp=tol_pp)


# ── 서프라이즈 분류(로그인 — 분기 컨센서스가 있을 때만, SD classify_signal:172) ─────────────


def classify_surprise(
    actual: Figures, consensus: Figures, th: EarningsThresholds
) -> EarningsSignal:
    """실적 vs 분기 컨센서스 — SD `classify_signal` 의 분기 순서·경계 그대로(12 신호)."""
    rev_c, op_c = consensus.revenue, consensus.op
    rev_a, op_a = actual.revenue, actual.op
    rev_pct = pct_change(rev_a, rev_c)
    op_pct = pct_change(op_a, op_c)
    big, std, inline = th.big_pct, th.standard_pct, th.inline_pct

    def sig(name: str, prio: int, rev: float | None, op: float | None, triggers: Sequence[str],
            note: str) -> EarningsSignal:  # fmt: skip
        basis: Literal["op", "revenue"] | None = (
            "op" if op is not None else ("revenue" if rev is not None else None)
        )
        return _signal(name, prio, "surprise", basis, rev, op, triggers, [note])

    # 흑자·적자 전환 우선
    if op_c is not None and op_a is not None:
        if op_c < 0 < op_a:
            return sig("TURNAROUND_FULL", 1, rev_pct, op_pct, ["OP_TURNAROUND"],
                       f"컨센 적자({op_c:,.0f}) → 실제 흑자({op_a:,.0f})")  # fmt: skip
        if op_c > 0 > op_a:
            return sig("SHOCK_FULL", 1, rev_pct, op_pct, ["OP_SHOCK"],
                       f"컨센 흑자({op_c:,.0f}) → 실제 적자({op_a:,.0f})")  # fmt: skip
        if op_c < 0 and op_a < 0:
            ratio = abs(op_a) / abs(op_c)
            if ratio <= 1 - th.turnaround_partial_ratio:
                return sig(
                    "TURNAROUND_PARTIAL", 2, rev_pct, op_pct, ["OP_LOSS_NARROWING"],
                    f"적자폭 {(1 - ratio) * 100:.0f}% 축소 ({op_c:,.0f}→{op_a:,.0f})",
                )  # fmt: skip

    # 매출·영업이익 둘 다
    if rev_pct is not None and op_pct is not None:
        if op_pct >= big:
            return sig("BEAT_BIG", 1, rev_pct, op_pct,
                       ["OP_BEAT_BIG", *(["REV_BEAT"] if rev_pct >= std else [])],
                       f"영업익 {op_pct:+.1f}% (BIG)")  # fmt: skip
        if op_pct <= -big:
            return sig("MISS_BIG", 1, rev_pct, op_pct,
                       ["OP_MISS_BIG", *(["REV_MISS"] if rev_pct <= -std else [])],
                       f"영업익 {op_pct:+.1f}% (BIG)")  # fmt: skip
        if rev_pct <= -std and op_pct >= std:
            return sig("REVENUE_MISS_OP_BEAT", 2, rev_pct, op_pct,
                       ["REV_MISS", "OP_BEAT", "PRICING_POWER"],
                       f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}% — 가격 결정력")  # fmt: skip
        if rev_pct >= std and abs(op_pct) <= inline:
            return sig("REVENUE_BEAT_OP_INLINE", 3, rev_pct, op_pct,
                       ["REV_BEAT", "OP_INLINE", "MARGIN_PRESSURE"],
                       f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}% — 마진 압박")  # fmt: skip
        if std <= op_pct < big:
            return sig("BEAT", 2, rev_pct, op_pct, ["OP_BEAT"], f"영업익 {op_pct:+.1f}%")
        if -big < op_pct <= -std:
            return sig("MISS", 2, rev_pct, op_pct, ["OP_MISS"], f"영업익 {op_pct:+.1f}%")
        if rev_pct >= std and abs(op_pct) < std:
            return sig("REVENUE_BEAT", 3, rev_pct, op_pct, ["REV_BEAT"], f"매출만 {rev_pct:+.1f}%")
        if rev_pct <= -std and abs(op_pct) < std:
            return sig("REVENUE_MISS", 3, rev_pct, op_pct, ["REV_MISS"], f"매출만 {rev_pct:+.1f}%")
        return sig("INLINE", 5, rev_pct, op_pct, [],
                   f"매출 {rev_pct:+.1f}%, 영업익 {op_pct:+.1f}%")  # fmt: skip

    # 영업이익만
    if op_pct is not None:
        if op_pct >= big:
            return sig("BEAT_BIG", 1, None, op_pct, ["OP_BEAT_BIG"], f"영업익 {op_pct:+.1f}%")
        if op_pct <= -big:
            return sig("MISS_BIG", 1, None, op_pct, ["OP_MISS_BIG"], f"영업익 {op_pct:+.1f}%")
        if op_pct >= std:
            return sig("BEAT", 2, None, op_pct, ["OP_BEAT"], f"영업익 {op_pct:+.1f}%")
        if op_pct <= -std:
            return sig("MISS", 2, None, op_pct, ["OP_MISS"], f"영업익 {op_pct:+.1f}%")

    # 매출만
    if rev_pct is not None:
        if rev_pct >= std:
            return sig("REVENUE_BEAT", 3, rev_pct, None, ["REV_BEAT"], f"매출 {rev_pct:+.1f}%")
        if rev_pct <= -std:
            return sig("REVENUE_MISS", 3, rev_pct, None, ["REV_MISS"], f"매출 {rev_pct:+.1f}%")

    return sig("INLINE", 5, rev_pct, op_pct, [], "데이터 부족 또는 인라인")


def sanity(consensus: Figures, history: Sequence[Figures], mktcap: int | None) -> Sanity:
    """컨센서스 자체 신뢰도 3관문(SD `sanity_check_consensus`:34 — DB 조회를 인자로 바꿨다).

    `history` = 그 회사의 분기 실적(원, **최근 것부터**, PIT — 호출자가 공시일 ≤ 발표일만 넘긴다).
    `mktcap` = 전 거래일 회사 시총(원). 단위가 같으면 비율은 SD(백만원끼리)와 같다.

    - A: 매출 컨센 ÷ 직전 4분기(매출 > 0) 평균 — 0.5~2.0 pass, 0.3~0.5·2.0~3.0 warn(×0.7), 밖 fail
      (×0.3), 3분기 미만 skip(×0.8).
    - B: 컨센 영업이익률 vs 이력 평균 영업이익률(4분기 이상) — 차이 ≤ 15%p pass, ≤ 25%p warn(×0.8),
      밖 fail(×0.4).
    - C: 매출 컨센 ÷ 시총 — 0.5%~100% pass, 0.1%~0.5%·100%~500% warn(×0.8), 밖 fail(×0.3).
    - fail ≥ 2 → skip(무효), fail 1 → use_with_caution(무효), warn ≥ 2 → use_with_caution,
      그 밖 use.
    """
    rev_c, op_c = consensus.revenue, consensus.op
    if rev_c is None or rev_c <= 0:
        return Sanity(False, 0.0, {"A": "fail", "B": "skip", "C": "skip"},
                      ("매출 컨센서스 누락 또는 0",), "skip")  # fmt: skip
    gates: dict[str, str] = {}
    reasons: list[str] = []
    confidence = 1.0

    last_4q = [h.revenue for h in history if h.revenue is not None and h.revenue > 0][:4]
    if len(last_4q) >= 3:
        avg = sum(last_4q) / len(last_4q)
        ratio_a = rev_c / avg if avg > 0 else 0.0
        if 0.5 <= ratio_a <= 2.0:
            gates["A"] = "pass"
        elif 0.3 <= ratio_a < 0.5 or 2.0 < ratio_a <= 3.0:
            gates["A"] = "warn"
            reasons.append(f"매출 컨센이 직전 4분기 평균의 {ratio_a:.2f}배")
            confidence *= 0.7
        else:
            gates["A"] = "fail"
            reasons.append(f"매출 컨센이 직전 4분기 평균의 {ratio_a:.2f}배 — 이상치")
            confidence *= 0.3
    else:
        gates["A"] = "skip"
        reasons.append("직전 4분기 데이터 부족")
        confidence *= 0.8

    if op_c is not None:
        consensus_opm = op_c / rev_c * 100
        opms = [h.op / h.revenue * 100 for h in history
                if h.op is not None and h.revenue is not None and h.revenue > 0]  # fmt: skip
        if len(opms) >= 4:
            hist_opm = sum(opms) / len(opms)
            diff = abs(consensus_opm - hist_opm)
            if diff <= 15:
                gates["B"] = "pass"
            elif diff <= 25:
                gates["B"] = "warn"
                reasons.append(f"OPM 컨센 {consensus_opm:.1f}% vs 평균 {hist_opm:.1f}%")
                confidence *= 0.8
            else:
                gates["B"] = "fail"
                reasons.append(f"OPM 컨센 {consensus_opm:.1f}% vs 평균 {hist_opm:.1f}% — 이상")
                confidence *= 0.4
        else:
            gates["B"] = "skip"
    else:
        gates["B"] = "skip"

    if mktcap is not None:
        ratio_c = rev_c / mktcap if mktcap > 0 else 0.0
        if 0.005 <= ratio_c <= 1.0:
            gates["C"] = "pass"
        elif 0.001 <= ratio_c < 0.005 or 1.0 < ratio_c <= 5.0:
            gates["C"] = "warn"
            reasons.append(f"매출/시총 {ratio_c * 100:.2f}%")
            confidence *= 0.8
        else:
            gates["C"] = "fail"
            reasons.append(f"매출/시총 {ratio_c * 100:.4f}% — 이상")
            confidence *= 0.3
    else:
        gates["C"] = "skip"

    fails = sum(1 for v in gates.values() if v == "fail")
    warns = sum(1 for v in gates.values() if v == "warn")
    rec: Literal["use", "use_with_caution", "skip"]
    if fails >= 2:
        rec, valid = "skip", False
    elif fails == 1:
        rec, valid = "use_with_caution", False
    elif warns >= 2:
        rec, valid = "use_with_caution", True
    else:
        rec, valid = "use", True
    return Sanity(valid, round(confidence, 2), gates, tuple(reasons), rec)


def apply_sanity(sig: EarningsSignal, s: Sanity) -> EarningsSignal:
    """주의(use_with_caution)면 우선순위 1 → 2 + 신뢰도 한 줄.

    SD `process_earnings_announcement`:338 그대로.
    """
    if s.recommendation == "use_with_caution" and sig.priority == 1:
        return replace(sig, priority=2, notes=(*sig.notes, f"컨센 신뢰도 {s.confidence:.0%}"))
    return sig


def scope_guard(
    scope: PrelimScope,
    rev_actual: int | float | None,
    rev_consensus: int | float | None,
    body_rev_yoy: float | None,
) -> str | None:
    """실적·컨센 범위(연결·별도) 불일치 의심이면 사유(→ YoY 로 되돌린다). SD :296 그대로.

    1) 별도 실적 + 컨센 대비 갭 > 10% 2) 연결 실적인데 본문 매출 YoY 가 |·| < 10% 인데 갭 > 20%.
    """
    if rev_actual is None or rev_consensus is None or rev_consensus <= 0:
        return None
    gap = abs(rev_actual - rev_consensus) / rev_consensus
    if scope is PrelimScope.STANDALONE and gap > 0.10:
        return "별도 실적 vs 연결 컨센서스"
    if (
        scope is PrelimScope.CONSOLIDATED
        and body_rev_yoy is not None
        and abs(body_rev_yoy) < 10
        and gap > 0.20
    ):
        return f"본문 매출 YoY {body_rev_yoy:+.1f}% vs 컨센 갭 {gap * 100:+.1f}% → 범위 의심"
    return None


# ── 알림(관심종목 — 로그인, D-P4-14) ─────────────────────────────────────────────────────


def alert_subject(row: PrelimRow) -> str:
    """`<corp_code>:<회계기간>:<연결/별도>` — 접수번호가 아니다(정정공시가 다시 울리지 않게)."""
    return f"{row.corp_code}:{row.period}:{row.scope.value}"


def _month_start_back(end: date, months: int) -> date:
    y, m = divmod(end.month - 1 - months, 12)
    return date(end.year + y, m + 1, 1)


def period_label(row: PrelimRow) -> str:
    """`2026 3Q`(12월 결산) · `FY26 2Q (26.07~26.09)`(비12월) · 연간 잠정은 `2026 연간`."""
    if row.is_annual:
        return f"{row.fiscal_year} 연간"
    start = _month_start_back(row.period_end, 2)
    try:
        return FiscalPeriod(row.fiscal_year, row.fiscal_q, start, row.period_end).label
    except ValueError:  # 분기말이 월말이 아닌 이상한 행 — 라벨만 단순하게
        return f"{row.fiscal_year} {row.fiscal_q}Q"


_SCOPE_LABEL: Final = {
    PrelimScope.CONSOLIDATED: "연결",
    PrelimScope.STANDALONE: "별도",
    PrelimScope.SUBSIDIARY: "자회사",
}


def _eok(v: int | float | None) -> str:
    """원 → 억 표기(화면 반올림). 없으면 '—'."""
    if v is None:
        return "—"
    x = v / 100_000_000
    return f"{x:,.0f}억" if abs(x) >= 100 else f"{x:,.1f}억"


def _pct(v: float | None) -> str:
    return "—" if v is None else f"{v:+.1f}%"


def alert_text(
    row: PrelimRow,
    sig: EarningsSignal,
    *,
    name: str,
    code: str | None,
    link: str,
    correction: bool = False,
) -> str:
    """관심종목 잠정실적 알림 본문(템플릿 — SD `build_fallback`:299 문형, LLM 없음).

    숫자는 모두 입력 행(`row` — 원 단위 실적)과 분류 결과(`sig` — 그 행으로 계산한 비율)에서 온다.
    """
    who = f"{name}({code})" if code else name
    head = f"[잠정실적] {who} {period_label(row)} {_SCOPE_LABEL[row.scope]}"
    if correction:
        head += " — 정정공시"
    basis = "전년동기" if sig.method == "yoy" else "컨센서스"
    lines = [
        head,
        f"{SIGNAL_LABEL.get(sig.signal, sig.signal)} ({sig.signal}, 우선순위 {sig.priority})",
        f"매출 {_eok(row.revenue_cur)} ({basis} {_pct(sig.rev_pct)}) / "
        f"영업이익 {_eok(row.op_cur)} ({basis} {_pct(sig.op_pct)})",
    ]
    if row.ni_cur is not None:
        lines.append(f"당기순이익 {_eok(row.ni_cur)}")
    lines.extend(sig.notes[:2])
    if row.period_inferred:
        lines.append("회계기간은 결산월로 추정(본문에 기간 표기 없음)")
    lines.append(f"원문 {link}")
    return "\n".join(lines)


# ── 알림 후 성과(거래일 — 원장 종가) ─────────────────────────────────────────────────────


def alert_base_day(alerted_at: datetime, cal: TradingCalendar) -> date:
    """알림 시각 직전에 마감한 거래일(그날 정규장 마감 ≤ 알림 시각이면 그날, 아니면 앞 거래일)."""
    kst = to_kst(alerted_at)
    d = kst.date()
    if cal.is_trading_day(d):
        _, close = cal.equity_bounds(d)
        if kst >= close:
            return d
    return cal.prev_trading_day(d)


def perf_days(base: date, days: Sequence[int], cal: TradingCalendar) -> dict[int, date]:
    """기준 거래일 뒤 k 번째 거래일(k ∈ days)."""
    if any(k < 1 for k in days):
        raise ValueError(f"성과 거래일은 1 이상: {tuple(days)}")
    out: dict[int, date] = {}
    d = base
    for k in range(1, max(days, default=0) + 1):
        d = cal.next_trading_day(d)
        if k in days:
            out[k] = d
    return out


def perf_pct(base_price: float | None, close: float | None) -> float | None:
    """(종가 ÷ 기준 종가 − 1) × 100. 하나라도 없거나 기준 ≤ 0 이면 None(지어내지 않는다)."""
    if base_price is None or close is None or base_price <= 0:
        return None
    return (close / base_price - 1.0) * 100.0
