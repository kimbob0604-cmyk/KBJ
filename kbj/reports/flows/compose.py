"""수급 리포트 본문(텔레그램 Markdown) — 고른 이유·수익률·기준일·문장·숫자표.

승격 원본: ET `monitor/flow/telegram.py` 의 `FOOTER`·`won`·`pick_lines`·`RET_LABEL`·`return_line`·
`asof_gap`·`compose`(본문 그대로). 발송 함수(`send_photos`·`send_report`·`send_text`)는 notifier
대기열(`notify`·`notify_media`)로 바뀌어 `run.py` 로 갔다 — 텔레그램을 직접 부르지 않는다.

KBJ P5 에서 더한 것: `flow_basis_line` — 수급 원장의 실제 기준일·원천·quality 한 줄(ADR 0018 §2-5:
16:00 의 투자자 수급은 KIS 마감 수집 값이라 잠정일 수 있고, 당일 행이 없는 종목은 전 거래일 값이다.
오늘 값인 척 내보내지 않는다). 리포트에 `flow_basis` 가 있을 때만 붙는다.
"""

from __future__ import annotations

from typing import Any

from .analyze import LIMITS, UNVERIFIED
from .narrative import period_label

__all__ = [
    "FOOTER",
    "RET_LABEL",
    "asof_gap",
    "compose",
    "flow_basis_line",
    "pick_lines",
    "return_line",
    "won",
]

# 본문 하단에 출처·한계 문구를 붙일 것인가.
#
# 사용자가 받아 보고 **빼 달라고 했다** — 본인 대화방이고 매번 같은 네 줄이
# 붙으면 읽는 데 방해가 된다. 그래서 끈다.
#
# 사실이 사라지는 것은 아니다. 어느 경로로 만들었는지(`rep['source']`)와
# 기간합계 대조를 했는지(`rep['verified']`)는 리포트 데이터에 그대로 있고
# 실행 기록(detail)이 매번 남긴다. 한계 문구 원문은 analyze.LIMITS 에 있다.
FOOTER = False

QUALITY_KO = {"ok": "확정", "estimated": "잠정", "stale": "지연", "invalid": "무효"}


def won(eok: float | None) -> str | None:
    """억원 값을 사람이 읽는 단위로. 1조부터는 조로 적는다."""
    if eok is None:
        return None
    if abs(eok) >= 10_000:
        return f"{eok / 10_000:,.2f}조"
    return f"{eok:,.0f}억"


def pick_lines(pick: dict[str, Any]) -> list[str]:
    """왜 이 종목이 뽑혔는지, 그리고 그 거래가 덩치에 비해 얼마나 큰지.

    **시총 대비 거래대금**을 같이 적는 이유는, 같은 1,000억이라도 시총 3천억
    종목에서는 손이 통째로 바뀐 것이고 10조 종목에서는 평상시이기 때문이다.
    거래대금이 종가x거래량 추정치면 그렇게 적는다 — 실측과 섞지 않는다.
    """
    if not pick.get("label"):
        return []
    # 어느 기준으로 선 신고가인지 반드시 적는다.
    head = f"{pick['label']} 신고가"
    if pick.get("basis"):
        head += f"({pick['basis']} 기준)"
    if pick.get("vol_mult") is not None:
        head += f" · 거래량 20일 평균의 {pick['vol_mult']}배"
    out = [head]

    cap, turn = pick.get("mktcap"), pick.get("turnover")
    bits: list[str] = []
    if cap:
        bits.append(f"시총 {won(cap)}")
    if turn is not None:
        t = f"거래대금 {won(turn)}"
        if pick.get("turnover_est"):
            t += "(추정)"
        if cap:
            t += f" = 시총의 {turn / cap * 100:,.1f}%"
        bits.append(t)
    if bits:
        out.append(" · ".join(bits))
    return out


RET_LABEL = (("daily", "일간"), ("high", "장중 고가"), ("mom", "1개월"), ("ytd", "연초 대비"))


def return_line(ret: dict[str, Any]) -> str:
    """수익률 한 줄. 못 잰 항목은 적지 않는다.

    기준일을 괄호로 함께 적는다 — '1개월 +18.4%' 만 적으면 어느 날과 견줬는지
    확인할 방법이 없다. 연초 대비의 기준은 작년 마지막 거래일이다.
    """
    bits: list[str] = []
    for key, label in RET_LABEL:
        r = ret.get(key) or {}
        if r.get("pct") is None:
            continue
        # 연초 대비의 기준은 작년이다. 연도를 빼고 '12.30' 만 적으면 올해
        # 12월로 읽힌다 — 이 항목만 두 자리 연도를 붙인다.
        raw = r.get("base") or ""
        base = (raw[2:] if key == "ytd" else raw[5:]).replace("-", ".")
        bits.append(f"{label} {r['pct']:+,.1f}%" + (f"({base})" if base else ""))
    return "수익률 " + " · ".join(bits) if bits else ""


def asof_gap(rep: dict[str, Any]) -> str:
    """신고가 판정일과 이 리포트의 마지막 거래일이 다르면 말한다.

    신고가는 보드가 판정하고, 수급은 원장에 아직 오늘치가 없을 수 있다. 그러면 '52주 신고가' 와
    '일간 수익률' 이 서로 다른 날의 사실이 되는데, 아무 말이 없으면 같은 날로 읽힌다.
    """
    board = str((rep.get("pick") or {}).get("asof") or "").replace("-", "")[:8]
    last = str((rep.get("dates") or [""])[-1])
    if not board or board == "?" or not last or board == last:
        return ""

    def md(x: str) -> str:
        return f"{x[4:6]}.{x[6:8]}"

    return f"기준일이 다르다 — 신고가 {md(board)} · 수급·가격 {md(last)}"


def flow_basis_line(rep: dict[str, Any]) -> str:
    """수급 원장의 실제 기준일·원천·quality(ADR 0018 §2-5). 정보가 없으면 빈 문자열."""
    fb = rep.get("flow_basis") or {}
    day = str(fb.get("date") or "")
    if len(day) != 8:
        return ""
    q = str(fb.get("quality") or "")
    src = str(fb.get("source") or "?")
    return f"수급 기준일 {day[4:6]}.{day[6:8]} · 원천 {src} · {QUALITY_KO.get(q, q or '?')}"


def compose(rep: dict[str, Any], lines: list[str], numbers: str) -> str:
    """본문. 텔레그램은 Markdown 이라 표를 코드블록으로 감싼다."""
    out: list[str] = []
    # '한 달' 을 박아 두면 60거래일 리포트도 한 달이라고 적힌다. 실제로 받은
    # 거래일 수에서 만든다 (period_label).
    out.append(f"*{rep['name']}* `{rep['code']}` — 최근 {period_label(rep)} 수급")
    out.append(
        f"{rep['dates'][0][4:6]}.{rep['dates'][0][6:8]}"
        f"~{rep['dates'][-1][4:6]}.{rep['dates'][-1][6:8]} "
        f"{len(rep['dates'])}거래일 · 가격 기준 "
        f"{rep['baseDate'][4:6]}.{rep['baseDate'][6:8]} 종가=100"
    )
    for line in pick_lines(rep.get("pick") or {}):
        out.append(f"_{line}_")
    ret = return_line(rep.get("returns") or {})
    if ret:
        out.append(f"_{ret}_")
    gap = asof_gap(rep)
    if gap:
        out.append(f"_{gap}_")
    basis = flow_basis_line(rep)
    if basis:
        out.append(f"_{basis}_")
    out.append("")
    out.extend(f"· {line}" for line in lines)
    out.append("")
    out.append("```")
    out.append(numbers)
    out.append("```")
    if FOOTER:
        out.append("")
        out.append(f"_출처: {rep.get('source', '?')}_")
        if not rep.get("verified", True):
            out.append(f"_{UNVERIFIED}_")
        out.extend(f"_{line}_" for line in LIMITS)
    return "\n".join(out)
