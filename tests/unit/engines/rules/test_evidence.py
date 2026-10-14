"""`kbj.engines.rules.evidence` — 근거 자료·알림 본문·마감 묶음·`/시그널` 답(묶음 EVL, §4.4).

합성 P8 세계(고정 시드)의 심은 칸으로 실제 평가기를 돌려 본문을 만든다."""

from __future__ import annotations

import json
from datetime import date, datetime

from kbj.core.time import KST
from kbj.engines.rules.dsl import validate_rule
from kbj.engines.rules.evaluate import evaluate
from kbj.engines.rules.evidence import (
    DIGEST_MAX_LINES,
    DigestItem,
    SignalLine,
    close_digest,
    cond_line,
    evidence_payload,
    fmt_value,
    signal_text,
    signals_reply,
)
from kbj.engines.rules.frame import Frame, build_frame
from tests.fixtures.synthetic.p8_world import (  # pyright: ignore[reportPrivateUsage]
    P8World,
    _slot_ts,
    build_world,
)

W: P8World = build_world()
ANCHOR = date(2020, 1, 2)


def frame(code: str, k: int, *, slot: int | None = None, live: bool = True) -> Frame:
    d = W.days[k]
    if slot is None:
        return build_frame(
            code,
            W.bars[code],
            upto=d,
            anchor=ANCHOR,
            split_ratio=0.31,
            flows=W.flows[code],
            calendar=list(W.days),
        )
    ts = _slot_ts(d, slot)
    part = next(s for s in W.slots_for(code, d) if s.slot_ts == ts)
    return build_frame(
        code,
        W.bars[code],
        upto=W.days[k - 1],
        anchor=ANCHOR,
        split_ratio=0.31,
        flows=W.flows[code],
        calendar=list(W.days[:k]),
        partial=part,
        partial_flow=W.live_flow(code, ts) if live else None,
    )


def test_close_signal_text_e1_rsi() -> None:
    rule = W.examples["E1"]
    f = frame("Q80010", 300)
    v = evaluate(rule, f, f.last)
    assert v.state == "true"
    ev = evidence_payload(rule, v, f)
    json.dumps(ev)  # jsonb 에 들어간다(수·문자열·None 만)
    assert ev["partial"] is False and ev["logic"] == "all" and ev["cooldown_min"] == 1440
    text = signal_text(
        ev,
        rule_name=rule.name,
        version=3,
        code="Q80010",
        name="합성10",
        eval_ts=datetime(2026, 10, 1, 15, 30, tzinfo=KST),
        base_url="https://kbj.example",
    )
    lines = text.splitlines()
    assert lines[0] == "[규칙] RSI 과매도 · 합성10 Q80010"
    assert "마감 · 종가" in lines[1]
    assert lines[2].startswith("✔ RSI(14) ") and lines[2].endswith("< 30.0")
    assert "쿨다운 24시간 · 규칙 3판" in text
    assert text.endswith("상세 https://kbj.example/#/8/Q80010")


def test_intraday_text_marks_estimated_and_live_flow() -> None:
    rule = W.examples["E10"]
    f = frame("Q80016", 318, slot=30)
    v = evaluate(rule, f, f.last)
    assert v.state == "true" and v.live_flow
    ev = evidence_payload(rule, v, f)
    assert ev["partial"] is True and ev["flow_basis"]["live"] is True
    text = signal_text(
        ev,
        rule_name=rule.name,
        version=1,
        code="Q80016",
        name=None,
        eval_ts=_slot_ts(W.days[318], 30),
        base_url=None,
    )
    assert "14:00 장중(잠정) · 현재가" in text
    assert "(장중 잠정)" in text  # 수급 조건 — 가집계
    assert "KIS 현재가 14:00 estimated" in text
    assert "상세" not in text  # 주소가 없으면 링크를 지어내지 않는다
    assert "×0.97" in text  # ref 배수


def test_live_flow_note_names_the_round_per_investor() -> None:
    """근거 문구에 가집계 회차 시각 — 처리기가 고른 구분별 가장 최근 입력 회차(ADR 0031 §6)."""
    rule = W.examples["E10"]
    f = frame("Q80016", 318, slot=30)
    v = evaluate(rule, f, f.last)
    ev = evidence_payload(rule, v, f, extra={"flow_rounds": {"foreign": "13:20", "inst": "11:20"}})
    json.dumps(ev)
    lines = {c["ind"]: cond_line(c, ev) for c in ev["conds"] if c["flow"]}
    assert lines["foreign_streak"].endswith("(장중 잠정 13:20 회차)")
    assert lines["inst_streak"].endswith("(장중 잠정 11:20 회차)")
    # 회차를 모르면(재생·옛 기록) 회차 없이 '장중 잠정'
    plain = evidence_payload(rule, v, f)
    assert all(cond_line(c, plain).endswith("(장중 잠정)") for c in plain["conds"] if c["flow"])


def test_no_live_flow_is_unknown_with_reason() -> None:
    rule = W.examples["E10"]
    f = frame("Q80016", 318, slot=30, live=False)
    v = evaluate(rule, f, f.last)
    assert v.state == "unknown"
    ev = evidence_payload(rule, v, f)
    lines = [cond_line(c, ev) for c in ev["conds"] if c["flow"]]
    assert lines and all(x.startswith("? ") and "판단 불가(장중 가집계 없음)" in x for x in lines)


def test_cross_line_shows_previous_values() -> None:
    rule = W.examples["E4"]
    f = frame("Q80017", 305)
    v = evaluate(rule, f, f.last)
    assert v.state == "true"
    line = cond_line(evidence_payload(rule, v, f)["conds"][0])
    assert "상향 돌파 SMA20" in line and "(전일 " in line and " ≤ " in line


def test_false_cond_uses_negated_symbol_and_warmup_reason() -> None:
    rule = validate_rule(
        {
            "v": 1,
            "name": "x",
            "universe": {"codes": ["Q80004"]},
            "mode": "close",
            "when": {
                "any": [
                    {"ind": "rsi", "p": {"n": 14}, "op": "<", "value": 0},
                    {"ind": "sma", "p": {"n": 200}, "op": ">", "value": 0},
                ]
            },
        }
    )
    f = frame("Q80004", 270)  # 신규상장 21칸째 — sma200 워밍업 부족
    v = evaluate(rule, f, f.last)
    ev = evidence_payload(rule, v, f)
    a, b = (cond_line(c) for c in ev["conds"])
    assert a.startswith("✖ RSI(14)") and " ≥ " in a
    assert b == "✖ SMA200 — 값 없음(워밍업 부족)"


def test_fmt_value_units() -> None:
    assert fmt_value(71200.0, "price") == "71,200"
    assert fmt_value(-2.345, "pct") == "−2.35%"
    assert fmt_value(1.5e9, "won") == "+15.0억"
    assert fmt_value(3.0, "days") == "3일"
    assert fmt_value(None, "price") == "—"


def _item(i: int, rule: str = "r_aaaaaaaaaaaa") -> DigestItem:
    ev = {
        "price": 1000.0 + i,
        "change_pct": 1.0,
        "bar_source": "kis",
        "conds": [{"ok": True, "value": 25.0, "ind": "rsi", "label": "RSI(14)", "unit": "osc"}],
    }
    return DigestItem(rule, "규칙A", f"Q8{i:04d}", None, ev)


def test_close_digest_none_when_empty_and_caps_lines() -> None:
    assert close_digest([], day=date(2026, 10, 30), base_url=None) is None
    text = close_digest(
        [_item(i) for i in range(DIGEST_MAX_LINES + 5)],
        day=date(2026, 10, 30),
        base_url="https://kbj.example",
    )
    assert text is not None
    assert text.startswith("[규칙 마감] 10-30 — 16:00 기준 KIS 마감 값(잠정일 수 있음)")
    assert "■ 규칙A (35종목)" in text
    assert "외 5종목 — https://kbj.example/#/11" in text
    assert sum(1 for x in text.splitlines() if x.startswith("  Q8")) == DIGEST_MAX_LINES
    assert "RSI(14) 25.0" in text


def test_signals_reply_top10_latest_first_and_escaped() -> None:
    base = datetime(2026, 10, 30, 9, 0, tzinfo=KST)
    rows = [
        SignalLine(base.replace(minute=i), "intraday", "<규칙>", f"Q8{i:04d}", None, "sent")
        for i in range(12)
    ]
    rows.append(SignalLine(base.replace(hour=15), "close", "막힘", "Q80099", None, "cooldown"))
    text = signals_reply(rows, day=date(2026, 10, 30))
    lines = text.splitlines()
    assert "최신 10/12" in lines[0]
    assert len(lines) == 11
    assert lines[1].startswith("09:11 장중 · &lt;규칙&gt;")
    assert "막힘" not in text
    assert "오늘 신호 없음" in signals_reply([], day=date(2026, 10, 30))
