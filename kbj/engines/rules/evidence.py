"""근거 메시지 — 템플릿(P8 묶음 EVL, docs/p8_design.md §4.4·D-P8-11, metrics §30). LLM 없음(CLAUDE
§1-3).

순수 함수. 평가 결과(`Verdict`)·프레임에서 **근거 자료**(`evidence_payload` — 신호 표 `evidence`
jsonb 와 API 응답에 그대로 싣는 dict)를 만들고, 그 자료로 문장을 만든다(실시간 알림·마감 묶음·
`/시그널` 답 — 같은 자료라 화면·알림·이력이 같은 값을 보인다).

- 조건마다 ✔(참)·✖(거짓)·?(판단 불가 — 장중 가집계 없음), 지표 이름·파라미터·현재값·기준값, 크로스·
  전환이면 전 칸 값. 값이 없으면 사유(워밍업 부족·결측·정지).
- 수급 조건은 기준을 적는다: 장중은 '장중 잠정 13:20 회차'(가집계 — 사용자 결정 2026-10-13, 회차는
  근거 자료 `flow_rounds` — 처리기가 고른 가장 최근 입력 회차), 마감은 그날 원장 행의 품질('확정'·
  '잠정').
- 시세 줄은 출처·시각·quality(장중 = estimated). 일봉 줄은 전 거래일(장중) 또는
  그날(마감) 원장 출처.
- 상세 링크 = 로그인 주소 + `#/8/<코드>`(주소가 없으면 링크 줄을 뺀다 — 지어내지 않는다).
- 4,096자 초과는 notifier 가 나눈다. 마감 묶음은 종목 줄 `DIGEST_MAX_LINES`(30)를 넘으면 '외 N종목'.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from kbj.core.time import to_kst
from kbj.engines.rules.dsl import INDICATORS, AllGroup, Cond, Ref, Rule
from kbj.engines.rules.evaluate import CondResult, Verdict
from kbj.engines.rules.frame import Frame

__all__ = [
    "DIGEST_MAX_LINES",
    "DigestItem",
    "SignalLine",
    "close_digest",
    "cond_label",
    "cond_line",
    "evidence_payload",
    "signal_text",
    "signals_reply",
]

DIGEST_MAX_LINES: Final = 30
REPLY_TOP: Final = 10
_MARK: Final = {True: "✔", False: "✖", None: "?"}
_OPS: Final = {"<": "<", "<=": "≤", ">": ">", ">=": "≥", "==": "="}
_NEG_OPS: Final = {"<": "≥", "<=": ">", ">": "≤", ">=": "<", "==": "≠"}
_REASONS: Final = {
    "insufficient": "값 없음(워밍업 부족)",
    "missing": "값 없음(결측)",
    "halted": "값 없음(거래정지)",
    "no_live_flow": "판단 불가(장중 가집계 없음)",
}
_FIELDS: Final = {
    "line": "선", "signal": "시그널", "hist": "히스토그램",
    "upper": "상단", "mid": "중심", "lower": "하단", "pctb": "%B", "width": "폭",
}  # fmt: skip
_AXES: Final = {"d120": "120일", "52w": "52주", "ath": "역사적"}


# ── 이름·값 ─────────────────────────────────────────────────────────────────────────────────


def _pv(p: Mapping[str, Any], k: str) -> str:
    v = p.get(k)
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v)


def cond_label(x: Cond | Ref, *, partial: bool = False) -> str:
    """지표 표시 이름(파라미터 포함). 장중 부분 봉의 종가는 '현재가'."""
    p = x.p
    ind = x.ind
    fld = x.field
    if ind == "close":
        return "현재가" if partial else "종가"
    if ind in ("open", "high", "low"):
        return {"open": "시가", "high": "고가", "low": "저가"}[ind]
    if ind == "change_pct":
        return "등락률"
    if ind in ("sma", "ema"):
        return f"{ind.upper()}{_pv(p, 'n')}"
    if ind == "rsi":
        return f"RSI({_pv(p, 'n')})"
    if ind == "macd":
        return (
            f"MACD({_pv(p, 'fast')},{_pv(p, 'slow')},{_pv(p, 'signal')}) {_FIELDS[fld or 'line']}"
        )
    if ind == "bb":
        return f"볼린저({_pv(p, 'n')},{_pv(p, 'k')}) {_FIELDS.get(fld or '', fld or '')}"
    if ind == "bb_width_rank":
        return f"밴드폭 백분위({_pv(p, 'n')},{_pv(p, 'k')},{_pv(p, 'lookback')}일)"
    if ind == "adx":
        name = {"adx": "ADX", "pdi": "+DI", "mdi": "−DI"}[fld or "adx"]
        return f"{name}({_pv(p, 'n')})"
    if ind == "atr":
        return f"ATR({_pv(p, 'n')})"
    if ind == "vol_ratio":
        return f"거래량 배수({_pv(p, 'n')}일)"
    if ind in ("high_n", "low_n"):
        return f"{_pv(p, 'n')}일 {'최고' if ind == 'high_n' else '최저'}"
    if ind == "new_high":
        return f"{_AXES.get(_pv(p, 'axis'), _pv(p, 'axis'))} 신고가"
    if ind in ("foreign_streak", "inst_streak"):
        who = "외국인" if ind.startswith("foreign") else "기관"
        side = "순매도" if _pv(p, "side") == "sell" else "순매수"
        return f"{who} 연속 {side}"
    if ind in ("foreign_sum", "inst_sum"):
        who = "외국인" if ind.startswith("foreign") else "기관"
        return f"{who} {_pv(p, 'n')}일 순매수 합"
    if ind == "trail_from_peak":
        return "보유 고점 대비"
    return ind  # pragma: no cover — 닫힌 목록


def _unit(x: Cond | Ref) -> str:
    spec = INDICATORS[x.ind]
    if spec.fields is not None:
        return spec.fields.get(x.field or spec.default_field or "", spec.unit)
    return spec.unit


def fmt_value(v: float | None, unit: str) -> str:
    """단위별 표시 — 원 단위 금액은 억원, 비율은 배, %는 부호."""
    if v is None:
        return "—"
    if unit in ("price", "price_diff"):
        if abs(v) >= 100:
            return f"{v:,.0f}".replace("-", "−")
        return f"{v:,.2f}".replace("-", "−")
    if unit == "pct":
        return f"{v:+.2f}%".replace("-", "−")
    if unit == "osc":
        return f"{v:.1f}"
    if unit == "ratio":
        return f"{v:.2f}"
    if unit == "days":
        return f"{v:.0f}일"
    if unit == "won":
        return f"{v / 1e8:+,.1f}억".replace("-", "−")
    if unit == "flag":
        return "예" if v >= 0.5 else "아니오"
    return f"{v:g}"  # pragma: no cover


# ── 근거 자료(jsonb) ────────────────────────────────────────────────────────────────────────


def _flow_basis(frame: Frame, i: int) -> dict[str, Any] | None:
    r = frame.flow_rows[i]
    if r is None:
        return None
    live = frame.partial and i == frame.last
    return {"date": r.date.isoformat(), "quality": str(r.quality), "live": live}


def _cond_dict(r: CondResult, *, partial: bool) -> dict[str, Any]:
    c = r.cond
    return {
        "path": r.path,
        "ind": c.ind,
        "p": dict(c.p),
        "field": c.field,
        "op": c.op,
        "value_const": list(c.value) if isinstance(c.value, tuple) else c.value,
        "ref": None
        if c.ref is None
        else {"ind": c.ref.ind, "p": dict(c.ref.p), "field": c.ref.field, "scale": c.ref.scale},
        "label": cond_label(c, partial=partial),
        "ref_label": None if c.ref is None else cond_label(c.ref, partial=partial),
        "unit": _unit(c),
        "flow": INDICATORS[c.ind].flow,
        "ok": r.ok,
        "value": r.value,
        "ref_value": r.ref_value,
        "prev_value": r.prev_value,
        "prev_ref": r.prev_ref,
        "reason": r.reason,
    }


def evidence_payload(
    rule: Rule,
    verdict: Verdict,
    frame: Frame,
    *,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """신호 표 `evidence`(jsonb)에 싣는 근거 자료. 값은 JSON 수·문자열·None 만."""
    i = verdict.index
    partial = frame.partial and i == frame.last
    prev_i = i - 1 if i >= 1 else None
    out: dict[str, Any] = {
        "v": 1,
        "state": verdict.state,
        "skipped_reason": verdict.skipped_reason,
        "logic": "all" if isinstance(rule.when, AllGroup) else "any",
        "cooldown_min": rule.alert.cooldown_min,
        "partial": partial,
        "live_flow": verdict.live_flow,
        "trade_date": verdict.date.isoformat(),
        "price": frame.close[i],
        "change_pct": None,
        "price_source": frame.sources[i] or None,
        "price_quality": str(frame.quality[i]),
        "bar_date": None if prev_i is None or not partial else frame.dates[prev_i].isoformat(),
        "bar_source": (frame.sources[prev_i] or None)
        if partial and prev_i is not None
        else (frame.sources[i] or None),
        "flags": list(frame.flags) if partial else [],
        "flow_basis": _flow_basis(frame, i),
        "quality": str(verdict.quality),
        "input_fp": verdict.input_fp,
        "conds": [_cond_dict(r, partial=partial) for r in verdict.conds],
    }
    chg = frame.series("change_pct")[i]
    out["change_pct"] = chg
    if extra:
        out.update(extra)
    return out


# ── 문장 ────────────────────────────────────────────────────────────────────────────────────


def _flow_note(ev: Mapping[str, Any], ind: str = "") -> str:
    fb = ev.get("flow_basis")
    if not isinstance(fb, Mapping):
        return ""
    if fb.get("live"):
        rounds = ev.get("flow_rounds")
        who = "foreign" if ind.startswith("foreign") else "inst"
        rnd = rounds.get(who) if isinstance(rounds, Mapping) else None
        return f" (장중 잠정 {rnd} 회차)" if rnd else " (장중 잠정)"
    d = str(fb.get("date") or "")[5:]
    return f" ({d} {'확정' if fb.get('quality') == 'ok' else '잠정'})"


def cond_line(c: Mapping[str, Any], ev: Mapping[str, Any] | None = None) -> str:
    """조건 한 줄(✔/✖/?)."""
    mark = _MARK.get(c.get("ok"))
    label = str(c.get("label") or c.get("ind"))
    unit = str(c.get("unit") or "")
    op = str(c.get("op"))
    val = c.get("value")
    flow = _flow_note(ev or {}, str(c.get("ind") or "")) if c.get("flow") else ""
    reason = c.get("reason")
    if val is None or reason is not None:
        why = _REASONS.get(str(reason), "값 없음")
        return f"{mark} {label} — {why}{flow}"
    a = fmt_value(val, unit)
    if op == "between":
        lo, hi = c.get("value_const") or (None, None)
        return f"{mark} {label} {a} ∈ [{fmt_value(lo, unit)}, {fmt_value(hi, unit)}]{flow}"
    if op in ("turns_positive", "turns_negative"):
        word = "양전환" if op == "turns_positive" else "음전환"
        return f"{mark} {label} {word} — {a} (전일 {fmt_value(c.get('prev_value'), unit)}){flow}"
    ref_label = c.get("ref_label")
    ref = c.get("ref") or {}
    scale = ref.get("scale", 1.0) if isinstance(ref, Mapping) else 1.0
    if ref_label is not None and scale != 1.0:
        ref_label = f"{ref_label}×{scale:g}"
    b = fmt_value(c.get("ref_value"), unit)
    if op in ("cross_above", "cross_below"):
        word = "상향 돌파" if op == "cross_above" else "하향 돌파"
        other = ref_label if ref_label is not None else b
        pa, pb = fmt_value(c.get("prev_value"), unit), fmt_value(c.get("prev_ref"), unit)
        now_rel, prev_rel = (">", "≤") if op == "cross_above" else ("<", "≥")
        return (
            f"{mark} {label} {word} {other} — {a} {now_rel} {b} (전일 {pa} {prev_rel} {pb}){flow}"
        )
    sym = _OPS.get(op, op) if c.get("ok") else _NEG_OPS.get(op, op)
    if ref_label is not None:
        return f"{mark} {label} {a} {sym} {ref_label} {b}{flow}"
    return f"{mark} {label} {a} {sym} {b}{flow}"


def _cooldown_text(minutes: int | None) -> str:
    if minutes is None:
        return "—"
    if minutes % 1440 == 0:
        return f"{minutes // 1440 * 24}시간"
    if minutes % 60 == 0:
        return f"{minutes // 60}시간"
    return f"{minutes}분"


def _link(base_url: str | None, code: str) -> str | None:
    if not base_url:
        return None
    return f"{base_url.rstrip('/')}/#/8/{code}"


def signal_text(
    ev: Mapping[str, Any],
    *,
    rule_name: str,
    version: int,
    code: str,
    name: str | None,
    eval_ts: datetime,
    base_url: str | None,
) -> str:
    """신호 하나의 알림 본문(평문 — parse_mode 없음)."""
    k = to_kst(eval_ts)
    partial = bool(ev.get("partial"))
    head = f"[규칙] {rule_name} · {name + ' ' if name else ''}{code}"
    price = fmt_value(ev.get("price"), "price")
    chg = ev.get("change_pct")
    chg_t = "" if chg is None else f" ({fmt_value(chg, 'pct')})"
    if partial:
        when = f"{k:%H:%M} 장중(잠정) · 현재가 {price}{chg_t}"
    else:
        when = f"{k:%m-%d} 마감 · 종가 {price}{chg_t}"
    flags = ev.get("flags") or []
    if flags:
        when += " · " + ",".join(str(f) for f in flags)
    lines = [head, when]
    lines.extend(cond_line(c, ev) for c in ev.get("conds") or [])
    logic = "모두(AND)" if ev.get("logic") == "all" else "하나 이상(OR)"
    lines.append(f"{logic} · 쿨다운 {_cooldown_text(ev.get('cooldown_min'))} · 규칙 {version}판")
    src: list[str] = []
    if partial:
        src.append(f"KIS 현재가 {k:%H:%M} {ev.get('price_quality')}")
        if ev.get("bar_date"):
            src.append(f"일봉 {ev.get('bar_source') or '원장'} {str(ev.get('bar_date'))[5:]}")
    else:
        src.append(
            f"일봉 {ev.get('bar_source') or '원장'} {str(ev.get('trade_date'))[5:]} "
            f"{ev.get('price_quality')}"
        )
    link = _link(base_url, code)
    if link is not None:
        src.append(f"상세 {link}")
    lines.append(" · ".join(src))
    return "\n".join(lines)


@dataclass(frozen=True)
class DigestItem:
    """마감 묶음의 종목 한 줄 재료."""

    rule_id: str
    rule_name: str
    code: str
    name: str | None
    evidence: Mapping[str, Any]


def _key_value(ev: Mapping[str, Any]) -> str:
    for c in ev.get("conds") or []:
        if c.get("ok") and c.get("value") is not None and c.get("ind") not in ("close",):
            return f"{c.get('label')} {fmt_value(c.get('value'), str(c.get('unit') or ''))}"
    return ""


def close_digest(items: Sequence[DigestItem], *, day: date, base_url: str | None) -> str | None:
    """16:00 마감 묶음 본문(평문). 0건이면 None(보내지 않는다 — ADR 0018)."""
    if not items:
        return None
    lines = [f"[규칙 마감] {day:%m-%d} — 16:00 기준 KIS 마감 값(잠정일 수 있음)"]
    by_rule: dict[str, list[DigestItem]] = {}
    names: dict[str, str] = {}
    for it in items:
        by_rule.setdefault(it.rule_id, []).append(it)
        names[it.rule_id] = it.rule_name
    shown = 0
    hidden = 0
    for rid, rows in by_rule.items():
        lines.append(f"■ {names[rid]} ({len(rows)}종목)")
        for it in rows:
            if shown >= DIGEST_MAX_LINES:
                hidden += 1
                continue
            ev = it.evidence
            kv = _key_value(ev)
            nm = f"{it.name} " if it.name else ""
            price = fmt_value(ev.get("price"), "price")
            chg = ev.get("change_pct")
            chg_t = "" if chg is None else f" ({fmt_value(chg, 'pct')})"
            lines.append(f"  {nm}{it.code} {price}{chg_t}{' · ' + kv if kv else ''}")
            shown += 1
    if hidden:
        more = f"외 {hidden}종목"
        lines.append(
            f"{more} — 페이지 11" if not base_url else f"{more} — {base_url.rstrip('/')}/#/11"
        )
    srcs = sorted(
        {str(it.evidence.get("bar_source")) for it in items if it.evidence.get("bar_source")}
    )
    lines.append(
        f"출처: {', '.join(srcs) if srcs else '원장'} · 수급은 원장 행 품질대로(확정·잠정)"
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class SignalLine:
    """`/시그널` 답의 한 줄 재료."""

    eval_ts: datetime
    mode: str
    rule_name: str
    code: str
    name: str | None
    status: str


def signals_reply(rows: Sequence[SignalLine], *, day: date) -> str:
    """`/시그널` — 오늘 신호(보냄·발송 꺼짐·문지기 막음) 최신 순 상위 10. HTML(답은 HTML)."""
    picked = sorted(
        (r for r in rows if r.status in ("sent", "dry", "suppressed")),
        key=lambda r: (r.eval_ts, r.rule_name, r.code),
        reverse=True,
    )
    if not picked:
        return f"📡 <b>규칙 신호</b> {day:%m-%d}\n오늘 신호 없음"
    lines = [f"📡 <b>규칙 신호</b> {day:%m-%d} — 최신 {min(len(picked), REPLY_TOP)}/{len(picked)}"]
    for r in picked[:REPLY_TOP]:
        k = to_kst(r.eval_ts)
        mode = "장중" if r.mode == "intraday" else "마감"
        nm = f"{html.escape(r.name)} " if r.name else ""
        lines.append(f"{k:%H:%M} {mode} · {html.escape(r.rule_name)} · {nm}{html.escape(r.code)}")
    return "\n".join(lines)
