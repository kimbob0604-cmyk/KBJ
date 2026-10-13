"""ETF 리포트(텔레그램 08:20 — docs/p5_design.md §4.4, D-P5-13 ④).

ET `etf_tracker_v9/report.py` 의 섹션 본문을 **그대로** 옮겼다(`_head`:63·`_new_holdings`:77·
`_weight_moves`:99·`_consensus`:124·`_dropped`:138·`_movers`:158·`_periods`:178·`_flows`:189·
`_misc`:207·`_pack`·`build`:222 — 문구·이모지·자르기·HTML 이스케이프·3,900자 묶기). 바뀐 것은 입력
`D` 를 만드는 쪽뿐이다: ET `dash.py:collect`(SQLite `etf_px`·`etf_aum`·`change_log`·`holding`)를
kbj 행
자료형(`EtfDay`·`EtfMeta`·`Change`·`Fund`·스냅 코드 집합)에서 만드는 순수 함수 `assemble` 로 바꿨다.

설계 요점(ET 머리말 그대로)
  · 편입 종목과 보유비중 변화가 1순위다. 앞쪽 메시지에 배치한다.
  · 비중은 %p 로 보여준다. 비중은 주가가 올라도 늘어나므로 수량 증감을 같이 표기한다.
  · 텔레그램은 메시지당 4096자 제한이 있다. 섹션 경계에서 끊어 여러 통으로 묶는다.
  · parse_mode 는 HTML 을 쓴다. ETF 이름에 * _ ( ) & 가 흔해서 Markdown 은 자주 깨진다.

kbj 에서 정한 것
  · 기준일 = 직전 완료 영업일의 KRX ETF 일별(ET `market.py` 머리말 — 장중 값을 그날 수치로 쓰지
    않는다).
  · 수익률·등락은 분할·병합 보정 종가(metrics §8.2), 자금 흐름은 P3 metrics §4(순유입 = 좌수 변화 ×
    NAV
    — `kbj.engines.etf.flows.daily_flow`, 분할 보정 포함). 금액 칸은 ET 와 같이 억원.
  · 레버리지·인버스 판정은 정본 유형(`types.etf_type` — metrics §8.1) 하나.
"""

from __future__ import annotations

import html
import statistics
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Final

from kbj.core.rows import Change, EtfDay, EtfMeta, EtfType, Fund, SplitEvent
from kbj.engines.etf.flows import daily_flow
from kbj.engines.etf.holders import fund_label
from kbj.engines.etf.map import adjusted_closes
from kbj.engines.etf.types import etf_type

__all__ = [
    "BENCH_CODE",
    "BENCH_NAME",
    "LIMIT",
    "MIN_MKTCAP",
    "MIN_TURN",
    "PERIODS",
    "ReportInput",
    "assemble",
    "build",
    "esc",
]

LIMIT: Final = 3900  # 4096 제한에서 여유를 둔다

# ET dash.py 상수(억원)
MIN_MKTCAP: Final = 50.0  # 억원. 이 미만은 순위에서 제외
MIN_TURN: Final = 1.0  # 억원/일. 거래대금 하한
PERIODS: Final[tuple[tuple[str, int], ...]] = (
    ("1주", 7),
    ("1개월", 30),
    ("3개월", 91),
    ("6개월", 182),
    ("1년", 365),
)
BENCH_NAME: Final = "KODEX 200"
BENCH_CODE: Final = "069500"
EOK: Final = 1e8


def esc(s: object) -> str:
    return html.escape(str(s if s is not None else ""))


def _amt(v: float | None) -> str:
    if v is None:
        return "–"
    if abs(v) >= 10000:
        return f"{v / 10000:,.1f}조"
    return f"{v:,.0f}억"


def _short(s: object, n: int = 26) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n - 1] + "…"


def _pack(sections: Sequence[str]) -> list[str]:
    """섹션 리스트를 4096자 제한에 맞춰 메시지로 묶는다. 섹션은 쪼개지 않는다."""
    msgs: list[str] = []
    cur = ""
    for sec in sections:
        if not sec:
            continue
        if len(sec) > LIMIT:  # 섹션 하나가 한도를 넘으면 줄 단위로 자른다
            if cur:
                msgs.append(cur)
                cur = ""
            buf = ""
            for line in sec.split("\n"):
                if len(buf) + len(line) + 1 > LIMIT:
                    msgs.append(buf)
                    buf = ""
                buf += line + "\n"
            if buf:
                msgs.append(buf)
            continue
        if len(cur) + len(sec) + 2 > LIMIT:
            msgs.append(cur)
            cur = sec
        else:
            cur = (cur + "\n\n" + sec) if cur else sec
    if cur:
        msgs.append(cur)
    return msgs


# ─────────────────────────── 섹션 빌더(ET 본문 그대로) ───────────────────────────
D_ = Mapping[str, Any]


def _head(D: D_) -> str:
    k = {lab: val for lab, val, _ in D["kpi"]}
    ud = k.get("상승 / 하락", "")
    for t in ('<span class="up">', '<span class="dn">', "</span>"):
        ud = ud.replace(t, "")
    when = f"{D['live_date']} {D['live_ts']}" if D.get("live_ts") else D["base"]
    L = [
        f"<b>📊 ETF 리포트</b>  {esc(when)}",
        f"KODEX 200 {esc(k.get('KODEX 200', '–'))} · 상승/하락 {esc(ud)} "
        f"· 순자산 {esc(k.get('전체 순자산', '–'))}",
    ]
    if D.get("hold_note"):
        L.append(f"<i>{esc(D['hold_note'])}</i>")
    return "\n".join(L)


def _new_holdings(D: D_) -> str:
    """1순위 — 새로 편입된 종목."""
    hn, det = D.get("h_new") or [], D.get("new_detail") or []
    if not hn and not det:
        return ""
    L = ["<b>🆕 신규 편입</b>"]
    multi = [x for x in hn if x["n"] >= 2]
    if multi:
        L.append("\n<b>여러 ETF가 동시에 담은 종목</b>")
        for x in multi[:10]:
            tag = " <i>(신규상장·지수편입)</i>" if x.get("event") else ""
            L.append(f"  <b>{esc(x['name'])}</b> — {x['n']}개 ETF{tag}")
            L.append(
                f"    <i>{esc(_short(', '.join(x['funds'][:3]), 52))}"
                + (f" 외 {len(x['funds']) - 3}" if len(x["funds"]) > 3 else "")
                + "</i>"
            )
    if det:
        L.append("\n<b>비중을 크게 잡고 들어간 건</b>")
        for x in det[:12]:
            L.append(f"  <b>{x['wt']:.2f}%</b>  {esc(x['name'])}")
            L.append(f"    ↳ {esc(_short(x['fund'], 34))}")
    return "\n".join(L)


def _weight_moves(D: D_) -> str:
    """1순위 — 보유비중이 크게 바뀐 종목."""
    ups, dns = D.get("wt_up") or [], D.get("wt_down") or []
    if not ups and not dns:
        return ""
    L = ["<b>⚖️ 보유비중 변화</b>", "<i>비중(%p) · 괄호는 실제 수량 증감</i>"]

    def rows(items: Sequence[Mapping[str, Any]], arrow: str) -> list[str]:
        out: list[str] = []
        for x in items:
            out.append(
                f"  <b>{x['dpp']:+.2f}%p</b>  {esc(x['name'])}   "
                f"{x['pw']:.2f}% {arrow} {x['cw']:.2f}%"
            )
            out.append(f"    ↳ {esc(_short(x['fund'], 30))} <i>(수량 {x['qty']:+.0f}%)</i>")
        return out

    if ups:
        L.append("\n<b>📈 확대</b>")
        L += rows(ups[:10], "→")
    if dns:
        L.append("\n<b>📉 축소</b>")
        L += rows(dns[:10], "→")
    return "\n".join(L)


def _consensus(D: D_) -> str:
    """여러 ETF가 같은 방향으로 움직인 종목 — 개별 액션보다 강한 신호."""
    mv = D.get("h_moves") or []
    if not mv:
        return ""
    L = ["<b>🔁 여러 ETF가 같은 방향으로 움직인 종목</b>"]
    for x in mv[:12]:
        ico = "📈" if x["kind"] == "ADD" else "📉"
        lab = "확대" if x["kind"] == "ADD" else "축소"
        L.append(
            f"  {ico} <b>{esc(x['name'])}</b> {x['n']}개 ETF {lab} "
            f"<i>(수량 평균 {x['avg']:+.1f}%)</i>"
        )
    return "\n".join(L)


def _dropped(D: D_) -> str:
    ho, ev = D.get("h_out") or [], D.get("h_events") or []
    if not ho and not ev:
        return ""
    L: list[str] = []
    if ho:
        L.append("<b>❌ 제외된 종목</b>")
        L.append("<i>제외한 ETF 수 / 그 종목을 담고 있던 ETF 수</i>")
        for x in ho[:12]:
            L.append(f"  <b>{x['n']} / {x.get('prior', x['n'])}개</b>  {esc(x['name'])}")
            L.append(f"    ↳ {esc(_short(', '.join(x['funds'][:3]), 52))}")
    if ev:
        L.append("\n<b>⚠️ 종목 사유로 빠진 건</b>")
        L.append(
            "<i>보유하던 ETF 전부에서 동시 소멸 — 거래정지·합병·분할 등. "
            "운용사 매도 판단이 아닙니다</i>"
        )
        for x in ev[:6]:
            L.append(f"  {esc(x['name'])} <code>{esc(x['code'])}</code> — 보유 {x['n']}개 전부")
    return "\n".join(L)


def _movers(D: D_) -> str:
    up = D.get("up_nl") or [x for x in (D.get("up") or []) if not x.get("lev")]
    dn = D.get("down_nl") or [x for x in (D.get("down") or []) if not x.get("lev")]
    lab = " <i>(레버리지·인버스 제외)</i>"
    if not up and not dn:  # 걸러낼 게 없으면 원본을 쓰고 라벨도 바꾼다
        up, dn, lab = D.get("up") or [], D.get("down") or [], ""
    if not up and not dn:
        return ""
    L = [f"<b>📊 당일 등락</b>{lab}"]
    if up:
        L.append("\n<b>상승</b>")
        for x in up[:7]:
            L.append(f"  <b>{x['chg']:+.2f}%</b>  {esc(_short(x['name'], 28))}")
    if dn:
        L.append("\n<b>하락</b>")
        for x in dn[:7]:
            L.append(f"  <b>{x['chg']:+.2f}%</b>  {esc(_short(x['name'], 28))}")
    return "\n".join(L)


def _periods(D: D_) -> str:
    pr = D.get("periods") or {}
    if not pr:
        return ""
    L = ["<b>📈 기간별 수익률</b> <i>(상위 3)</i>"]
    for lab, v in pr.items():
        top = " · ".join(f"{x['ret']:+.0f}% {_short(x['name'], 18)}" for x in v["up"][:3])
        L.append(f"  <b>{esc(lab)}</b>  {esc(top)}")
    return "\n".join(L)


def _flows(D: D_) -> str:
    if not D.get("flow_span"):
        return (
            "<b>💰 자금 유입·이탈</b>\n"
            "<i>순자산 스냅샷이 하루치뿐입니다. 다음 영업일부터 집계됩니다.</i>"
        )
    L = [f"<b>💰 자금 유입·이탈</b> <i>({esc(D['flow_span'][0])} → {esc(D['flow_span'][1])})</i>"]
    if D.get("flow_in"):
        L.append("\n<b>유입</b>")
        for x in D["flow_in"][:6]:
            L.append(
                f"  <b>{_amt(x['amt'])}</b>  {esc(_short(x['name'], 26))} <i>({x['pct']:+.1f}%)</i>"
            )
    if D.get("flow_out"):
        L.append("\n<b>이탈</b>")
        for x in D["flow_out"][:6]:
            L.append(
                f"  <b>{_amt(x['amt'])}</b>  {esc(_short(x['name'], 26))} <i>({x['pct']:+.1f}%)</i>"
            )
    return "\n".join(L)


def _misc(D: D_) -> str:
    L: list[str] = []
    if D.get("spikes"):
        L.append("<b>🔥 거래 급증</b> <i>(20일 중앙값 대비)</i>")
        for x in D["spikes"][:6]:
            L.append(
                f"  <b>×{x['mult']:,.1f}</b>  {esc(_short(x['name'], 26))} "
                f"<i>{_amt(x['turnover'])}</i>"
            )
    if D.get("new_etf"):
        L.append("\n<b>🆕 신규 상장 ETF</b> <i>(최근 60일)</i>")
        for x in D["new_etf"][:6]:
            L.append(
                f"  {esc(x['first'])}  {esc(_short(x['name'], 28))} <i>{_amt(x['mktcap'])}</i>"
            )
    return "\n".join(L)


def build(D: D_, url: str | None = None) -> list[str]:
    """전체 리포트를 텔레그램 메시지 리스트로 만든다(섹션 순서는 ET 그대로)."""
    tail = ""
    if url:
        tail = f'\n<a href="{esc(url)}">웹 대시보드에서 보기</a>'
    secs = [
        _head(D),
        _new_holdings(D),
        _weight_moves(D),
        _consensus(D),
        _dropped(D),
        _movers(D),
        _periods(D),
        _flows(D),
        _misc(D),
    ]
    msgs = _pack([s for s in secs if s])
    if msgs and tail:
        msgs[-1] += "\n" + tail
    return msgs


# ─────────────────────────── 입력 D 조립(ET dash.collect 의 kbj 판) ───────────────────────────


@dataclass(frozen=True)
class ReportInput:
    """리포트 입력 — 저장소에서 읽은 행만(순수 계산의 전제).

    - `base`: 기준일(직전 완료 영업일 이하 마지막 ETF 일별 날짜).
    - `days`: 코드 → ETF 일별(오름차순, `base` 이하 — 1년 기간 수익률·20일 거래량 창을 덮을 만큼).
    - `run_date`·`changes`: 그날 구성종목 변동(`prv_etf.change_log`). 없으면 빈 목록(변동 없는 날).
    - `snaps`: (fund_id, 기준일) → 그 스냅의 종목 코드 집합(변동 펀드의 prev_asof·asof 만 — ET
      `_prior_holders`·`_cur_holders`).
    - `pair_count`: 비교 가능한 펀드 수(ET `_pair_count` — `holdings.fund_pairs` 길이).
    - `notes`: 머리 기울임 줄에 덧붙일 문구(기준일 지연 등).
    """

    base: date
    days: Mapping[str, Sequence[EtfDay]]
    meta: Mapping[str, EtfMeta]
    run_date: date
    changes: Sequence[Change]
    funds: Mapping[str, Fund]
    snaps: Mapping[tuple[str, date], Collection[str]]
    pair_count: int
    splits: Mapping[str, Sequence[SplitEvent]] = field(
        default_factory=dict[str, Sequence[SplitEvent]]
    )
    notes: Sequence[str] = ()


def _name(inp: ReportInput, code: str, row: EtfDay | None) -> str:
    m = inp.meta.get(code)
    if m is not None and m.name:
        return m.name
    if row is not None and row.name:
        return row.name
    return code


def _is_lev(inp: ReportInput, code: str, name: str) -> bool:
    m = inp.meta.get(code)
    t = m.etf_type if m is not None and m.etf_type is not None else None
    if t is None:
        t = etf_type(name, None if m is None else m.base_index)
    return EtfType(t) is EtfType.LEVERAGED_INVERSE


def _dates(inp: ReportInput) -> list[date]:
    return sorted({r.date for rows in inp.days.values() for r in rows if r.date <= inp.base})


def _universe(inp: ReportInput) -> dict[str, dict[str, Any]]:
    """ET `_universe` — 기준일 행이 있는 ETF 의 이름·종가·거래량·시총(억)·거래대금(억)·레버리지."""
    out: dict[str, dict[str, Any]] = {}
    for code in sorted(inp.days):
        row = next((r for r in inp.days[code] if r.date == inp.base), None)
        if row is None:
            continue
        name = _name(inp, code, row)
        cap = row.mktcap if row.mktcap is not None else row.net_asset
        turn = (
            row.turnover / EOK
            if row.turnover is not None
            else (row.close or 0) * (row.volume or 0) / EOK
        )
        out[code] = dict(
            code=code,
            name=name,
            close=row.close,
            volume=row.volume or 0,
            mktcap=(cap or 0) / EOK,
            turnover=turn,
            lev=_is_lev(inp, code, name),
        )
    return out


def _liquid(u: Mapping[str, Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [
        x
        for x in u.values()
        if x["mktcap"] >= MIN_MKTCAP and x["turnover"] >= MIN_TURN and x["close"]
    ]


def _adj(inp: ReportInput) -> dict[str, dict[date, float]]:
    return {c: adjusted_closes(rows, inp.splits.get(c, ())) for c, rows in inp.days.items()}


def _mover_rows(
    inp: ReportInput, u: Mapping[str, Mapping[str, Any]], adj: Mapping[str, Mapping[date, float]]
) -> list[dict[str, Any]]:
    """ET `movers` — 당일 등락률(분할 보정, 내림차순 전체)."""
    ds = _dates(inp)
    if len(ds) < 2:
        return []
    prev_d = ds[-2]
    out: list[dict[str, Any]] = []
    for x in _liquid(u):
        a = adj.get(x["code"], {})
        p, c = a.get(prev_d), a.get(inp.base)
        if not p or c is None:
            continue
        out.append({**x, "chg": (c / p - 1) * 100})
    out.sort(key=lambda z: (-z["chg"], z["code"]))
    return out


def _ref_date(ds: Sequence[date], d: date, days: int) -> date | None:
    target = d - timedelta(days=days)
    got = [x for x in ds if x <= target]
    return max(got) if got else None


def _period_returns(
    inp: ReportInput,
    u: Mapping[str, Mapping[str, Any]],
    adj: Mapping[str, Mapping[date, float]],
    n: int = 5,
) -> dict[str, dict[str, Any]]:
    """ET `period_returns` — 기간별 수익률 상·하위. 기준일 이전 자료가 없는 신규 상장은 빠진다."""
    ds = _dates(inp)
    liq = _liquid(u)
    res: dict[str, dict[str, Any]] = {}
    ytd = (inp.base - date(inp.base.year, 1, 1)).days
    for label, days in (*PERIODS, ("YTD", ytd)):
        ref = _ref_date(ds, inp.base, days)
        if ref is None:
            continue
        rows: list[dict[str, Any]] = []
        for x in liq:
            a = adj.get(x["code"], {})
            b, c = a.get(ref), a.get(inp.base)
            if not b or c is None:
                continue
            rows.append({**x, "ret": (c / b - 1) * 100})
        if not rows:
            continue
        rows.sort(key=lambda z: (-z["ret"], z["code"]))
        res[label] = {"ref": ref.isoformat(), "up": rows[:n], "down": list(reversed(rows[-n:]))}
    return res


def _flow_rows(
    inp: ReportInput, u: Mapping[str, Mapping[str, Any]], n: int = 10
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[str, str] | None]:
    """ET `flows` — 설정·환매(P3 metrics §4: 좌수 변화 × NAV, 분할 보정). 억원."""
    ds = _dates(inp)
    if len(ds) < 2:
        return [], [], None
    prv, cur = ds[-2], ds[-1]
    out: list[dict[str, Any]] = []
    for code in sorted(inp.days):
        by = {r.date: r for r in inp.days[code]}
        a, b = by.get(cur), by.get(prv)
        if a is None or b is None:
            continue
        ev = next((e for e in inp.splits.get(code, ()) if e.effective_date == cur), None)
        f = daily_flow(b, a, ev)
        if not f.counted or f.net_inflow is None:
            continue
        cap = (u.get(code) or {}).get("mktcap", 0.0)
        if cap < MIN_MKTCAP or not b.list_shrs or not a.list_shrs:
            continue
        amt = f.net_inflow / EOK
        if abs(amt) < 1.0:
            continue
        pct = (a.list_shrs / (b.list_shrs * f.ratio) - 1) * 100
        out.append(dict(code=code, name=_name(inp, code, a), amt=amt, mktcap=cap, pct=pct))
    out.sort(key=lambda z: (-z["amt"], z["code"]))
    fin = [x for x in out if x["amt"] > 0][:n]
    fout = list(reversed([x for x in out if x["amt"] < 0][-n:]))
    return fin, fout, (prv.isoformat(), cur.isoformat())


def _volume_spikes(
    inp: ReportInput, u: Mapping[str, Mapping[str, Any]], n: int = 10, look: int = 20
) -> list[dict[str, Any]]:
    """ET `volume_spikes` — 당일 거래량 ÷ 직전 20거래일 중앙값(2배 이상)."""
    ds = [d for d in _dates(inp) if d < inp.base][-look:]
    if len(ds) < 5:
        return []
    want = set(ds)
    out: list[dict[str, Any]] = []
    for x in _liquid(u):
        h = [
            float(r.volume)
            for r in inp.days.get(x["code"], ())
            if r.date in want and r.volume is not None and r.volume > 0
        ]
        if len(h) < 5:
            continue
        m = statistics.median(h)
        # 평소 거래가 사실상 없던 종목은 배수가 무한대로 튄다 — 중앙값 거래대금에도 하한(ET 그대로)
        if m <= 0 or m * x["close"] / EOK < MIN_TURN:
            continue
        out.append({**x, "mult": x["volume"] / m, "med": m})
    out = [z for z in out if z["mult"] >= 2.0]
    out.sort(key=lambda z: (-z["mult"], z["code"]))
    return out[:n]


def _new_listings(
    inp: ReportInput, u: Mapping[str, Mapping[str, Any]], days: int = 60, n: int = 12
) -> list[dict[str, Any]]:
    """ET `new_listings` — 상장(메타 상장일, 없으면 첫 시세)이 최근 60일 안."""
    cut = inp.base - timedelta(days=days)
    ds = _dates(inp)
    start = ds[0] if ds else inp.base
    rows: list[tuple[date, str]] = []
    for code, rs in inp.days.items():
        m = inp.meta.get(code)
        first = m.listed_on if m is not None and m.listed_on is not None else None
        if first is None:
            got = [r.date for r in rs]
            # 창 첫날부터 있던 것은 그 전부터 있었을 수 있다 — 상장일을 모르면 신규로 세지 않는다
            if not got or min(got) <= start:
                continue
            first = min(got)
        if cut <= first <= inp.base:
            rows.append((first, code))
    rows.sort(key=lambda t: (t[0], t[1]), reverse=True)
    return [
        dict(
            code=c,
            name=_name(inp, c, None),
            first=f.isoformat(),
            mktcap=(u.get(c) or {}).get("mktcap", 0),
        )
        for f, c in rows[:n]
    ]


def _holders(inp: ReportInput, attr: str) -> dict[str, int]:
    """ET `_prior_holders`(attr=prev_asof)·`_cur_holders`(asof) — 변동 펀드들의 그 스냅 보유 펀드
    수."""
    pairs = {(c.fund_id, getattr(c, attr)) for c in inp.changes}
    out: dict[str, int] = {}
    for key in pairs:
        for code in inp.snaps.get(key, ()):
            out[code] = out.get(code, 0) + 1
    return out


def _agg(inp: ReportInput, kinds: Collection[str], n: int) -> list[dict[str, Any]]:
    """ET `_agg` — 종목별 '몇 개 ETF 가' (펀드 수 내림차순, 같으면 코드)."""
    by: dict[str, dict[str, Any]] = {}
    for c in inp.changes:
        if c.kind not in kinds:
            continue
        x = by.setdefault(c.code, {"code": c.code, "name": c.name, "fids": set(), "wt": None})
        x["name"] = x["name"] or c.name
        x["fids"].add(c.fund_id)
        if c.cur_wt is not None and (x["wt"] is None or c.cur_wt > x["wt"]):
            x["wt"] = c.cur_wt
    rows = [
        dict(
            code=x["code"],
            name=x["name"] or x["code"],
            n=len(x["fids"]),
            funds=sorted({fund_label(f, inp.funds.get(f)) for f in x["fids"]}),
            wt=x["wt"],
        )
        for x in by.values()
    ]
    rows.sort(key=lambda z: (-z["n"], z["code"]))
    return rows[: n * 3]


def _holdings_new(inp: ReportInput, n: int = 12) -> list[dict[str, Any]]:
    """ET `holdings_new` — 새로 편입(NEW·IN10). 아무도 안 들고 있던 것을 5곳 이상이 담으면
    이벤트."""
    rows = _agg(inp, ("NEW", "IN10"), n)
    prior = _holders(inp, "prev_asof")
    for x in rows:
        x["event"] = prior.get(x["code"], 0) == 0 and x["n"] >= 5
    return rows[:n]


def _holdings_out(
    inp: ReportInput, n: int = 12
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ET `holdings_out` — 제외. 보유처 전부에서 동시 소멸(3곳↑)은 종목 사유(이벤트)로 따로."""
    rows = _agg(inp, ("DROP", "OUT10"), n)
    prior = _holders(inp, "prev_asof")
    cur = _holders(inp, "asof")
    for x in rows:
        p = prior.get(x["code"], 0)
        x["prior"] = p
        x["event"] = p >= 3 and cur.get(x["code"], 0) == 0
    real = [x for x in rows if not x["event"]][:n]
    events = [x for x in rows if x["event"]][:6]
    return real, events


def _theme_moves(inp: ReportInput, n: int = 10) -> list[dict[str, Any]]:
    """ET `theme_moves` — 비중 확대·축소(ADD·CUT)가 2개 이상 ETF 에서 겹친 종목."""
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for c in inp.changes:
        if c.kind not in ("ADD", "CUT"):
            continue
        x = by.setdefault((c.code, c.kind), {"name": c.name, "fids": set(), "adj": []})
        x["name"] = x["name"] or c.name
        x["fids"].add(c.fund_id)
        if c.qty_pct_adj is not None:
            x["adj"].append(c.qty_pct_adj)
    rows: list[dict[str, Any]] = []
    for (code, kind), x in by.items():
        if len(x["fids"]) < 2:
            continue
        avg = sum(x["adj"]) / len(x["adj"]) if x["adj"] else 0.0
        rows.append(
            dict(
                code=code,
                name=x["name"] or code,
                kind=kind,
                n=len(x["fids"]),
                avg=avg,
                funds=sorted({fund_label(f, inp.funds.get(f)) for f in x["fids"]}),
            )
        )
    rows.sort(key=lambda z: (-z["n"], -abs(z["avg"]), z["code"], z["kind"]))
    return rows[:n]


def _new_detail(inp: ReportInput, n: int = 15) -> list[dict[str, Any]]:
    """ET `new_detail` — 개별 편입(어떤 ETF 가 몇 % 비중으로)."""
    rows: list[dict[str, Any]] = [
        dict(
            name=c.name or c.code,
            code=c.code,
            fund=fund_label(c.fund_id, inp.funds.get(c.fund_id)),
            wt=c.cur_wt,
        )
        for c in inp.changes
        if c.kind in ("NEW", "IN10") and c.cur_wt is not None
    ]
    rows.sort(key=lambda z: (-z["wt"], z["code"], z["fund"]))
    return rows[:n]


def _weight_moves_rows(
    inp: ReportInput, n: int = 12, floor: float = 0.5
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ET `weight_moves` — 비중 %p 변화(0.5%p 이상), 수량 증감(qty_pct_adj)을 같이."""
    out: list[dict[str, Any]] = []
    for c in inp.changes:
        if c.kind not in ("ADD", "CUT") or c.prev_wt is None or c.cur_wt is None:
            continue
        d = c.cur_wt - c.prev_wt
        if abs(d) < floor:
            continue
        out.append(
            dict(
                name=c.name or c.code,
                code=c.code,
                fund=fund_label(c.fund_id, inp.funds.get(c.fund_id)),
                pw=c.prev_wt,
                cw=c.cur_wt,
                qty=c.qty_pct_adj or 0,
                dpp=d,
            )
        )
    out.sort(key=lambda z: (-abs(z["dpp"]), z["code"], z["fund"]))
    return [x for x in out if x["dpp"] > 0][:n], [x for x in out if x["dpp"] < 0][:n]


def _hold_note(inp: ReportInput) -> str:
    """ET `_hold_note` — 변동 없는 날도 한 줄(조용히 끝내지 않는다)."""
    n = inp.pair_count
    if n == 0:
        return "아직 비교할 이전 스냅샷이 없습니다. 다음 영업일부터 집계됩니다."
    if not inp.changes:
        return f"{n}개 ETF 비교 — 감지된 변동 없음"
    lo = min(c.prev_asof for c in inp.changes).isoformat()
    hi = max(c.asof for c in inp.changes).isoformat()
    return f"{n}개 ETF 비교 · {lo} → {hi} · 운용사 공시 기준일이 달라 ETF마다 비교 구간이 다릅니다"


def _amt_plain(v: float) -> str:
    return f"{v / 10000:,.1f}조" if abs(v) >= 10000 else f"{v:,.0f}억"


def assemble(inp: ReportInput) -> dict[str, Any]:
    """ET `dash.collect` — 리포트 입력 D(섹션 빌더가 읽는 키만)."""
    u = _universe(inp)
    adj = _adj(inp)
    ds = _dates(inp)
    prev = ds[-2] if len(ds) >= 2 else None
    liq = _liquid(u)

    def chg(code: str) -> float | None:
        a = adj.get(code, {})
        p, c = (a.get(prev) if prev else None), a.get(inp.base)
        return None if not p or c is None else (c / p - 1) * 100

    ups = sum(1 for x in liq if (chg(x["code"]) or 0) > 0)
    dns = sum(1 for x in liq if (chg(x["code"]) or 0) < 0)
    aum = sum(x["mktcap"] for x in u.values())
    bench = next((x for x in u.values() if x["name"] == BENCH_NAME), u.get(BENCH_CODE))
    bench_c = None if bench is None else chg(bench["code"])
    every = _mover_rows(inp, u, adj)
    nl = [x for x in every if not x["lev"]]
    real, events = _holdings_out(inp)
    fin, fout, span = _flow_rows(inp, u)
    wt_up, wt_down = _weight_moves_rows(inp)
    note = _hold_note(inp)
    if inp.notes:
        note = " · ".join([note, *inp.notes])

    def _k(v: float) -> str:
        return ("up" if v > 0 else "dn") if v else ""

    kpi = [
        ("KODEX 200", f"{bench_c:+.2f}%" if bench_c is not None else "–", _k(bench_c or 0)),
        ("상승 / 하락", f'<span class="up">{ups}</span> / <span class="dn">{dns}</span>', ""),
        ("전체 순자산", _amt_plain(aum), ""),
        ("구성종목 변동 ETF", f"{len({c.fund_id for c in inp.changes})}개", ""),
    ]
    return dict(
        base=inp.base.isoformat(),
        prev=None if prev is None else prev.isoformat(),
        n_etf=len(u),
        kpi=kpi,
        up=every[:10],
        down=list(reversed(every[-10:])),
        up_nl=nl[:10],
        down_nl=list(reversed(nl[-10:])),
        periods=_period_returns(inp, u, adj),
        flow_in=fin,
        flow_out=fout,
        flow_span=span,
        spikes=_volume_spikes(inp, u),
        new_etf=_new_listings(inp, u),
        h_new=_holdings_new(inp),
        h_out=real,
        h_events=events,
        new_detail=_new_detail(inp),
        wt_up=wt_up,
        wt_down=wt_down,
        h_moves=_theme_moves(inp),
        hold_note=note,
    )
