"""수급 리포트 — 신고가 + 거래량 종목의 1개월(60거래일) 투자자별 수급을 차트 4장·본문으로 보낸다.

작업 `flows.report`(16:00 거래일 — ADR 0018, 등록부 owner `kbj.reports.flows:daily`, notify
`flows.report`). 승격 원본: ET `monitor/flow/run.py`(`DAYS`·`trading_window`·`build_one`·`run`·
`main` — 본문 그대로) + `telegram.py`(`send_photos`·`send_report`·`send_text` → notifier 대기열).

KBJ P5 에서 바뀐 것(docs/p5_design.md §1.5·§4.5, ADR 0022 초안)
- **원천**: KRX 정보데이터시스템 스크랩(`krx.py`)·KIS 직접 호출(`kissrc.py`)·네이버 일봉을 모두
  버리고 kbj 원장만 읽는다 — 투자자별 일별 순매수 `prv_flows.stock_investor_daily`
  (`FlowsRepo.window`), 일봉 `prv_market.daily_bar`(`MarketRepo.series`), 대상
  `prv_board.artifact`(newhigh — `select.py`).
  발송 시각에 외부 호출이 0 이다(계약 ⑯). 다른 묶음의 코드를 import 하지 않는다.
- **검산**: 원장에는 KRX 기간합계가 없다 → `verified=False`(하지 않은 대조를 했다고 적지 않는다).
  대신 ET KIS 경로의 '금액 자릿수 대조(수량x종가)'를 원장 값에 그대로 건다(`unit_check`) — 셀 수
  있을 만큼 값이 있을 때만(값이 모자라면 대조를 걸지 않는다 — 없는 대조를 실패로 적지 않는다).
- **가격**: 원장 일봉은 **비수정(KRX)** 이다(ET 는 네이버 수정주가). 차트 아래 표기를 그렇게 바꾸고,
  구간 안에 액면분할·권리락이 끼면 수익률이 틀릴 수 있다 [확인 필요 — 보드 분할 가드와 묶기는 P6].
- **기준일**(ADR 0018 §2-5): 16:00 의 투자자 수급은 KIS 마감 수집 값이라 잠정일 수 있고, 당일 행이
  없는 종목은 전 거래일 값이다. 본문에 실제 기준일·원천·quality 한 줄(`compose.flow_basis_line`)과,
  보드 기준일과 다르면 그 줄(`asof_gap`)을 싣는다.
- **발송**: 차트 4장 → `notify_media`, 본문 → `notify(kind='flows.report', parse_mode='Markdown')`.
  dedup daily — subject 는 종목(`<코드>`·`<코드>:charts`)이라 재실행해도 같은 날 두 번 가지 않는다.
  그림이 실패하면 본문도 보내지 않는다(ET). 대상이 없는 날·모두 건너뛴 날은 한 줄을 보낸다.
- 신고가 '달성 0' 이나 보드 산출 없음은 ET 처럼 멈춘다 — 다만 `SystemExit` 대신 `FlowReportError`
  (실행기가 작업 실패로 기록한다).

시각은 `ctx.now`·`ctx.as_of` 만 쓴다(시계 주입). LLM 없음 — 문장은 `narrative.py` 의 규칙.
"""

from __future__ import annotations

import argparse
import inspect
import sys
import tempfile
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final, cast

from kbj.core.quality import Quality
from kbj.core.rows import INST7, Investor, InvestorDay
from kbj.core.time import to_kst
from kbj.services.scheduler.handlers import JobContext, JobResult
from kbj.store.repos import pg_repos

from . import analyze as A
from . import chart as C
from . import compose as T
from . import narrative as N
from . import returns as RET
from .select import (
    KIND,
    KINDS,
    LABEL_KO,
    TOP_N,
    VOL_MULT_MIN,
    basis_of,
    hit,
    latest_newhigh,
    meta_of,
    pick_targets,
)

__all__ = [
    "DAYS",
    "JOB",
    "KIND",
    "KINDS",
    "NOTIFY_KIND",
    "TOP_N",
    "VOL_MULT_MIN",
    "FlowReportError",
    "basis_of",
    "build_one",
    "daily",
    "hit",
    "latest_newhigh",
    "ledger",
    "meta_of",
    "pick_targets",
    "run",
    "send_photos",
    "send_report",
    "send_text",
    "trading_window",
    "unit_check",
]

JOB: Final = "flows.report"
NOTIFY_KIND: Final = "flows.report"

# 조회할 거래일 수. 사용자 요청으로 20 → 60 거래일 (2026-09-17).
# 원장이 그만큼 없으면 받은 만큼만 쓰고, 제목도 받은 일수로 적는다
# (narrative.period_label) — 요청한 일수를 제목에 적으면 거짓이 된다.
DAYS = 60

# 일봉을 넉넉히 — 연초 대비는 작년 12월 1일부터 필요하다(최대 약 13개월 ≈ 280 거래일).
OHLCV_DAYS: Final = 300

# 원장 투자자 → ET 화면 이름(analyze 의 MAIN_ROWS·INSTITUTIONS 와 같은 글자).
INVESTOR_KO: Final[dict[Investor, str]] = {
    Investor.INDIVIDUAL: "개인",
    Investor.FOREIGN: "외국인",
    Investor.FOREIGN_OTHER: "기타외국인",
    Investor.INSTITUTION: "기관합계",
    Investor.OTHER_CORP: "기타법인",
    Investor.FIN_INVEST: "금융투자",
    Investor.INSURANCE: "보험",
    Investor.TRUST: "투신",
    Investor.PRIVATE_FUND: "사모",
    Investor.BANK: "은행",
    Investor.OTHER_FIN: "기타금융",
    Investor.PENSION: "연기금 등",
}

# 금액 자릿수 대조(ET kissrc.unit_check). 수량 x 종가 대비 허용 배수와 최소 규모(원).
RATIO_LO, RATIO_HI = 0.2, 5.0
RATIO_FLOOR = 100_000_000
UNIT_WHO: Final = ("개인", "외국인", "기관합계")

_Q_ORDER: Final = {Quality.OK: 0, Quality.ESTIMATED: 1, Quality.STALE: 2, Quality.INVALID: 3}

Log = Callable[..., Any]


class FlowReportError(RuntimeError):
    """보드 산출이 없거나 신고가 목록이 빈 날 — 작업 실패(ET 의 SystemExit 자리)."""


def trading_window(days: int = DAYS, *, end: date) -> tuple[str, str]:
    """조회 구간. 넉넉히 잡고 실제 거래일은 원장에서 추린다.

    주말·공휴일을 모르므로 달력일로 1.9배 잡는다(ET 그대로 — 원장 창은 `FlowsRepo.window` 가
    거래일 수로 자른다. 이 함수는 미리보기·기록용 구간 표기에 쓴다).
    """
    start = end - timedelta(days=int(days * 1.9) + 10)
    return start.isoformat(), end.isoformat()


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def ledger(
    rows: Sequence[InvestorDay],
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], dict[str, dict[str, Any]]]:
    """원장 행 → ET 모양 `({'YYYYMMDD': {'개인': 원}}, {...: 주}, {날짜: {source, quality}})`.

    없는 구분은 키를 만들지 않는다(0 으로 채우지 않는다 — analyze.present 가 표에서 뺀다).
    기관합계가 없고 기관 세부가 있으면 세부의 합으로 만든다(ET run.build_one 과 같다).
    """
    amt: dict[str, dict[str, float]] = {}
    qty: dict[str, dict[str, float]] = {}
    meta: dict[str, dict[str, Any]] = {}
    for r in rows:
        who = INVESTOR_KO.get(Investor(r.investor))
        if who is None:
            continue
        d = _ymd(r.date)
        m = meta.setdefault(d, {"sources": set(), "quality": Quality.OK})
        m["sources"].add(r.source)
        if _Q_ORDER.get(r.quality, 3) > _Q_ORDER.get(m["quality"], 3):
            m["quality"] = r.quality
        if r.net_value is not None:
            amt.setdefault(d, {})[who] = float(r.net_value)
        if r.net_qty is not None:
            qty.setdefault(d, {})[who] = float(r.net_qty)
    inst = [INVESTOR_KO[i] for i in INST7]
    for src in (amt, qty):
        for row in src.values():
            if "기관합계" not in row and any(k in row for k in inst):
                row["기관합계"] = sum(row.get(k) or 0 for k in inst)
    return amt, qty, meta


def unit_check(
    amt: dict[str, dict[str, float]],
    qty: dict[str, dict[str, float]],
    closes: dict[str, float],
    dates: Sequence[str] | None = None,
) -> tuple[str, float | None, bool] | None:
    """금액이 수량 x 종가와 자릿수가 같은가(ET kissrc.unit_check). (라벨, 배수, 통과여부).

    KRX 기간합계 검산을 대신하지는 못한다 — '단위를 잘못 알지 않았는가' 만 본다. 견줄 값이
    모자라면(수량이 없는 원천 등) None — 대조를 걸지 않는다.
    """
    got = ref = 0.0
    for d in dates or sorted(amt):
        c = closes.get(d)
        if not c:
            continue
        for who in UNIT_WHO:
            a = (amt.get(d) or {}).get(who)
            q = (qty.get(d) or {}).get(who)
            if a is None or q is None:
                continue
            got += abs(a)
            ref += abs(q) * c
    if ref < RATIO_FLOOR or got <= 0:
        return None
    ratio = got / ref
    return ("금액 자릿수 대조(수량x종가)", round(ratio, 3), RATIO_LO <= ratio <= RATIO_HI)


def _ohlcv(repos: Any, code: str, end: date) -> list[dict[str, Any]]:
    """일봉 한 구간(비수정 — KRX 원장). 작년 12월 1일부터(연초 대비·1개월 대비가 쓴다)."""
    bars = repos.market.series([code], end, OHLCV_DAYS).get(code) or []
    start = date(end.year - 1, 12, 1)
    return [
        dict(
            asof=b.date.isoformat(),
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
        )
        for b in bars
        if b.date >= start and b.close is not None and b.open is not None
    ]


def build_one(
    code: str, name: str, *, repos: Any, end: date, days: int = DAYS, log: Log = print
) -> tuple[dict[str, Any] | None, str]:
    """한 종목 분석. 검산이 깨지거나 자료가 모자라면 (None, 사유)."""
    rows = repos.flows.window([code], end, days + 1).get(code) or []
    amt, qty, meta = ledger(rows)
    if not amt:
        return None, "수급 원장에 행이 없다"
    everything = sorted(amt)
    dates = everything[-days:]
    if len(dates) < 2:
        return None, f"거래일이 {len(dates)}일뿐이다"
    # 기준일 = 조회 구간 **직전** 거래일. 누적 0 과 가격 100 이 여기서 출발한다.
    i = everything.index(dates[0])
    base = everything[i - 1] if i > 0 else dates[0]

    ohlcv = _ohlcv(repos, code, end)
    closes = {r["asof"].replace("-", ""): r["close"] for r in ohlcv}
    if not closes:
        return None, "일봉을 못 받았다"

    sources = sorted({s for d in dates for s in meta[d]["sources"]} if meta else set())
    used = "+".join(sources) or "?"
    chk = unit_check(amt, qty, closes, dates)
    extra = (chk,) if chk is not None else ()
    rep: dict[str, Any] = A.analyze(
        code, name, amt, qty, None, closes, dates, base, source=used, extra_checks=extra
    )
    # 차트는 조회 구간만 보여 준다(수급 차트와 x축을 맞춘다). 수익률은 받아 온 전 구간으로 잰다.
    rep["ohlcv"] = [r for r in ohlcv if base <= r["asof"].replace("-", "") <= dates[-1]]
    rep["returns"] = RET.compute(ohlcv)
    rep["price_note"] = "비수정 종가(KRX)"
    last = meta.get(dates[-1]) or {}
    rep["flow_basis"] = dict(
        date=dates[-1],
        source="+".join(sorted(last.get("sources") or ())) or used,
        quality=str(getattr(last.get("quality"), "value", last.get("quality") or "")),
    )
    if not rep["ok"]:
        bad = "; ".join(f"{lbl} 차이 {diff}" for lbl, diff, ok in rep["checks"] if not ok)
        return None, f"검산 실패 — {bad}"
    return rep, used


# ── 발송(notifier 대기열 — 텔레그램을 직접 부르지 않는다) ─────────────────────────────────


def _ticket(t: Any) -> tuple[bool, str]:
    if getattr(t, "ok", False):
        return True, "중복(이미 보냄)" if getattr(t, "duplicate", False) else "넣음"
    return False, str(getattr(t, "reason", "사유 없음"))


def send_photos(
    paths: Sequence[str | Path],
    caption: str = "",
    *,
    notify_media: Callable[..., Any] | None,
    source: str = JOB,
    as_of: date | None = None,
    subject: str | None = None,
) -> tuple[bool, str]:
    """사진 여러 장을 한 묶음으로(10장씩 묶기·캡션 상한은 notifier). 반환 (ok, 사유).

    없는 파일은 조용히 건너뛰지 않는다 — 못 보낸 것을 보냈다고 적으면 받은 사람이 찾다가 끝난다.
    """
    ps = [Path(p) for p in paths or []]
    missing = [p.name for p in ps if not p.is_file()]
    if missing:
        return False, f"보낼 그림이 없다: {', '.join(missing)}"
    if not ps:
        return False, "보낼 그림이 없다"
    if notify_media is None:
        return False, "자원 notify_media 가 없다 — 그림을 넣지 못했다"
    ok, why = _ticket(
        notify_media(
            [str(p) for p in ps], caption or "",
            kind=NOTIFY_KIND, source=source, as_of=as_of, subject=subject,
        )
    )  # fmt: skip
    return (True, f"그림 {len(ps)}장 {why}") if ok else (False, why)


def send_text(
    text: str,
    *,
    notify: Callable[..., Any] | None,
    source: str = JOB,
    as_of: date | None = None,
    subject: str | None = None,
) -> tuple[bool, str]:
    """본문 한 통(Markdown). '대상 없음' 한 줄도 이것으로 보낸다."""
    if notify is None:
        return False, "자원 notify 가 없다 — 본문을 넣지 못했다"
    return _ticket(
        notify(
            text, kind=NOTIFY_KIND, source=source, as_of=as_of, subject=subject,
            parse_mode="Markdown",
        )
    )  # fmt: skip


def send_report(
    rep: dict[str, Any],
    charts: Sequence[str | Path],
    text: str,
    *,
    notify: Callable[..., Any] | None,
    notify_media: Callable[..., Any] | None,
    source: str = JOB,
    as_of: date | None = None,
) -> tuple[bool, str]:
    """차트 + 본문. 그림이 실패하면 본문도 보내지 않는다(ET — 반쯤 깨진 약속을 보내지 않는다)."""
    head = f"{rep['name']} {rep['code']} · 수급 {len(rep['dates'])}거래일"
    ok, why = send_photos(
        charts, head, notify_media=notify_media, source=source, as_of=as_of,
        subject=f"{rep['code']}:charts",
    )  # fmt: skip
    if not ok:
        return False, why
    ok2, why2 = send_text(text, notify=notify, source=source, as_of=as_of, subject=rep["code"])
    if not ok2:
        return False, f"그림은 갔으나 본문 실패: {why2}"
    return True, f"{why} · 본문 {why2}"


# ── 실행 ────────────────────────────────────────────────────────────────────────────────


def run(
    *,
    repos: Any = None,
    asof: date | None = None,
    notify: Callable[..., Any] | None = None,
    notify_media: Callable[..., Any] | None = None,
    codes: Sequence[str] | None = None,
    top: int = TOP_N,
    days: int = DAYS,
    dry: bool = False,
    log: Log = print,
    kind: str = KIND,
    vol_min: float = VOL_MULT_MIN,
    source: str = JOB,
    stats: dict[str, Any] | None = None,
) -> int:
    """대상 종목을 돌려 리포트를 만들고 보낸다. 반환: 보낸(또는 dry 면 만든) 건수.

    `stats` 를 주면 작업 기록(detail)에 남길 수(값·코드 없이)를 채운다.
    """
    st_out: dict[str, Any] = stats if stats is not None else {}
    label = LABEL_KO.get(kind, "전체")
    nh_asof = "?"
    if codes:
        targets: list[tuple[str, str, dict[str, Any]]] = [(c, c, {}) for c in codes]
    else:
        nh = latest_newhigh(getattr(repos, "board", None), asof)  # pyright: ignore[reportArgumentType]
        if not nh:
            # 조용히 0 건으로 끝내면 매일 아무것도 안 보내면서 초록으로 보인다.
            raise FlowReportError(
                "보드 신고가 산출(newhigh)이 없다 — board.daily(16:00~16:05) 실행 순서를 본다"
            )
        targets, st = pick_targets(nh, top, kind=kind, vol_min=vol_min)
        nh_asof = str(nh.get("as_of") or nh.get("asof") or "?")
        for _, _, m in targets:
            m["asof"] = nh_asof
        st_out.update(board_asof=nh_asof[:10], **{f"pick_{k}": v for k, v in st.items()})
        log(f"대상 {len(targets)}종목 · {label} 신고가 + 거래량 {vol_min}배 이상 (보드 {nh_asof})")
        log(
            f"  달성 {st['achieved']}종목 → {label} 아님 {st['kind_out']} · "
            f"거래량 미달 {st['below']} · 거래량 모름 {st['no_vol']} → 통과 {st['passed']}"
        )
        if st["achieved"] == 0:
            raise FlowReportError(
                "신고가 달성 종목이 0건이다. 보드 목록이 비었다 — 보드 산출을 본다"
            )
        if not targets:
            # 이건 사고가 아니다. 신고가는 났지만 거래량이 평소와 같은 날이다.
            # 그래도 침묵하지는 않는다 — 안 온 것과 못 온 것이 구분돼야 한다.
            msg = (
                f"오늘 {label} 신고가 {st['achieved'] - st['kind_out']}종목 중 "
                f"거래량이 20일 평균의 {vol_min}배를 넘은 종목이 없습니다. "
                f"(미달 {st['below']} · 거래량 모름 {st['no_vol']})"
            )
            log(msg)
            st_out["no_target"] = True
            if not dry:
                ok, why = send_text(
                    f"📉 수급 리포트 — 대상 없음\n{msg}",
                    notify=notify, source=source, as_of=asof, subject="none",
                )  # fmt: skip
                log(f"  {'알림 넣음' if ok else '알림 실패'} — {why}")
                if not ok:
                    raise FlowReportError(f"알림을 넣지 못했다: {why}")
            return 0

    end = asof or date.min
    sent = 0
    skipped: list[str] = []
    failed: list[str] = []
    with tempfile.TemporaryDirectory(prefix="kbj-flows-") as out:
        for code, name, meta in targets:
            log(f"[{code} {name}]")
            rep, why = build_one(code, name, repos=repos, end=end, days=days, log=log)
            if not rep:
                log(f"  건너뜀 — {why}")
                skipped.append(why)
                continue
            # 왜 이 종목이 뽑혔는지를 본문이 말한다.
            rep["pick"] = dict(kind=kind, label=label, **(meta or {}))
            log(
                f"  원자료 {why} · 구분 {len(rep['main'])}개"
                + ("" if rep["verified"] else " · 기간합계 대조 못 함")
            )
            charts = C.render_all(rep, out)
            text = T.compose(rep, N.lines(rep), N.numbers(rep))
            if dry:
                log(f"  그림 {len(charts)}장 · 본문 {len(text)}자 (보내지 않음)")
                sent += 1
                continue
            ok, why2 = send_report(
                rep, charts, text, notify=notify, notify_media=notify_media,
                source=source, as_of=asof,
            )  # fmt: skip
            log(f"  {'넣음' if ok else '실패'} — {why2}")
            if ok:
                sent += 1
            else:
                failed.append(code)
    st_out.update(targets=len(targets), sent=sent, skipped=len(skipped), failed=len(failed))
    if targets and not sent and not failed and not dry:
        # 대상은 있었는데 전부 건너뛰었다 — 그 사실을 한 줄로 알린다(조용히 끝내지 않는다).
        reasons = sorted(set(skipped))
        msg = f"📉 수급 리포트 — 대상 {len(targets)}종목 모두 건너뜀\n" + "\n".join(
            f"· {r}" for r in reasons[:5]
        )
        ok, why = send_text(msg, notify=notify, source=source, as_of=asof, subject="skipped")
        if not ok:
            raise FlowReportError(f"알림을 넣지 못했다: {why}")
    if failed:
        raise FlowReportError(f"{len(failed)}종목 알림을 넣지 못했다(넣은 것 {sent})")
    return sent


def _notify_media(ctx: JobContext) -> Callable[..., Any] | None:
    """사진 발송 함수. 자원 `notify_media` → `notify` 의 짝(같은 클라이언트·모듈의 notify_media)."""
    fn = ctx.resources.get("notify_media")
    if fn is not None:
        return cast(Callable[..., Any], fn)
    notify = ctx.resources.get("notify")
    if notify is None:
        return None
    owner = getattr(notify, "__self__", None) or inspect.getmodule(notify)
    return getattr(owner, "notify_media", None)


def _run_day(ctx: JobContext) -> date:
    try:
        return date.fromisoformat(ctx.as_of[:10])
    except ValueError:
        return to_kst(ctx.now).date()


def daily(ctx: JobContext) -> JobResult:
    """`flows.report`(16:00 거래일) 처리기."""
    repos = ctx.resources.get("repos")
    if repos is None:
        repos = pg_repos(ctx.resource("connect"))
    notify = ctx.resources.get("notify")
    if notify is None:
        raise RuntimeError("자원 notify 가 주입되지 않았다 — 수급 리포트를 넣지 못했다")
    stats: dict[str, Any] = {}
    sent = run(
        repos=repos,
        asof=_run_day(ctx),
        notify=notify,
        notify_media=_notify_media(ctx),
        source=ctx.job or JOB,
        log=lambda *a: None,
        stats=stats,
    )
    return JobResult("ok", collected=tuple(ctx.keys), rows=sent, detail=stats)


def main(argv: Sequence[str] | None = None) -> int:
    """미리보기(보내지 않음) — `python -m kbj.reports.flows --at 2026-11-17`.

    DB(KBJ_DATABASE_URL)가 있어야 한다."""
    ap = argparse.ArgumentParser(prog="python -m kbj.reports.flows")
    ap.add_argument("--at", required=True, help="기준일 YYYY-MM-DD")
    ap.add_argument("--top", type=int, default=TOP_N)
    ap.add_argument("--days", type=int, default=DAYS)
    ap.add_argument("--codes", default="", help="쉼표로 구분한 종목코드")
    ap.add_argument("--kind", default=KIND, choices=KINDS)
    ap.add_argument("--vol-mult", type=float, default=VOL_MULT_MIN, dest="vol_mult")
    a = ap.parse_args(argv)
    from kbj.config.settings import Settings
    from kbj.store.db import connect

    settings = Settings()
    repos = pg_repos(lambda: connect(settings, service="reports"))
    codes = [c.strip() for c in a.codes.split(",") if c.strip()]
    n = run(
        repos=repos, asof=date.fromisoformat(a.at), codes=codes or None, top=a.top,
        days=a.days, dry=True, kind=a.kind, vol_min=a.vol_mult,
    )  # fmt: skip
    print(f"완료: {n}건(보내지 않음)", file=sys.stderr)
    return 0
