"""P4 공시 목록 공개 파일 — 페이지 5(공시·일정 패널)의 공개판 원천(docs/p4_design.md §6.4·§7).

- `filings_feed.json`: 공시가 있는 최근 2일(DART 접수일)의 상장사 공시 — `disclosure`
- `earnings_prelim.json`: 최근 14일 잠정실적(원) + YoY·QoQ(%) — `earnings_prelim`(+ `disclosure`
  정정 여부)
- `overhang_recent.json`: 최근 30일 오버행 결정 + 회사 오버행 비율(최근 요약) — `overhang_event`·
  `overhang_summary`
- `insider_recent.json`: 최근 30일 내부자 창(회사별 최신) — 비율 큰 순 상위 100 — `insider_window`
- `earnings_schedule.json`: 오늘부터 30일 실적 일정 — `earnings_schedule`

규칙
- 원천은 DART 하나 — 봉투와 행의 `source` 는 전부 `DART`(§7 내용 검사). 시세가 드는 값(오버행
  금액·ITM·내부자 금액)·관심종목·시총 가산 중요도·컨센서스 대비 서프라이즈는 **싣지 않는다**(로그인
  화면에서 읽을 때 계산 — §2 혼합 값). 내부자 보고자 이름도 싣지 않는다 [제안 — §7].
- 행 모양은 로그인 API 응답 모델(`kbj/services/api/models/filings.py`)의 같은 이름 칸에 맞췄다 —
  공개판 화면이 같은 그리기 코드를 쓰게. 로그인 전용 칸(`login_score`·`importance`·`is_watch`·
  `signal`·`alerted`·`amount`·`itm`)은 없다. 공시 중요도는 **제목 키워드 점수만**의 등급
  (`kw_level` — `config/fin.yaml filings.importance_levels`).
- 잠정실적 YoY·QoQ 는 docs/metrics.md §14 의 식((당기 − 기준) ÷ |기준| × 100, 기준 0·없음 → null).
  YoY **분류**(신호·등급)는 이 파일에 아직 없다 — 분류 규칙은 엔진(`kbj.engines.filings.earnings`)에
  있고 이 패키지는 엔진을 import 하지 못한다(계약 ⑧). 계약을 좁히거나 파생 열이 생기면 더한다(묶음 S
  요청).
- 상장사(종목코드 있는 회사 — `corp_index.Corps`)만 싣는다. 회사 이름은 DART 고유번호 목록 값.
- as_of: 공시 피드 = 가장 늦게 처음 본 시각(`first_seen_at`), 잠정실적·오버행(같은 피드가 채운다) =
  피드의 as_of, 내부자 창·실적 일정 = 가장 늦은 계산 시각(없으면 피드의 as_of). 7일 넘게 묵었으면
  stale.
- 공시 표에 상장사 행이 하나도 없으면(수집 전) 다섯 파일 모두 **만들지 않는다**(빈 파일·가짜 값 금지
  — 이유는 manifest.skipped). 기간 안에 행이 없을 뿐이면 빈 목록 그대로 만든다(사실이다).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.config.fin import FinConfig
from kbj.core.fin_rows import FiscalPeriod
from kbj.core.time import KST
from kbj.services.public_export.corp_index import Corps
from kbj.services.public_export.manifest import PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import (
    DART,
    Query,
    Reader,
    Row,
    canon,
    day,
    num,
    stamp,
    won,
)

__all__ = [
    "DART_VIEWER",
    "FEED_DAYS",
    "FEED_NAME",
    "INSIDER_LABEL",
    "INSIDER_NAME",
    "INSIDER_TOP",
    "NAMES",
    "OVERHANG_NAME",
    "PRELIM_NAME",
    "Q_FEED",
    "Q_INSIDER",
    "Q_OVERHANG",
    "Q_PRELIMS",
    "Q_SCHEDULE",
    "SCHEDULE_NAME",
    "STALE_AFTER",
    "FilingsOut",
    "build_filings",
    "day_start",
    "link",
    "pct_change",
    "prelim_label",
    "schedule_row",
]

FEED_NAME: Final = "filings_feed.json"
PRELIM_NAME: Final = "earnings_prelim.json"
OVERHANG_NAME: Final = "overhang_recent.json"
INSIDER_NAME: Final = "insider_recent.json"
SCHEDULE_NAME: Final = "earnings_schedule.json"
NAMES: Final = (FEED_NAME, PRELIM_NAME, OVERHANG_NAME, INSIDER_NAME, SCHEDULE_NAME)

FEED_DAYS: Final = 2  # 공시가 있는 최근 날 수(DART 접수일 — 주말·휴일은 자연히 건너뛴다)
FEED_FLOOR_DAYS: Final = 31  # 그 날들을 찾는 범위(달력일)
PRELIM_DAYS: Final = 14
OVERHANG_DAYS: Final = 30
INSIDER_DAYS: Final = 30  # 창(달력일 — 0010 insider_window.days)
INSIDER_FRESH_DAYS: Final = 7  # 회사별 최신 창이 이보다 오래되면 싣지 않는다(파생 작업이 멈춘 회사)
INSIDER_TOP: Final = 100
SCHEDULE_DAYS: Final = 30
STALE_AFTER: Final = timedelta(days=7)
INSIDER_LABEL: Final = "보고 기준 순증감(사유 미구분 포함)"
DART_VIEWER: Final = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

Q_FEED: Final = Query(
    "feed",
    "WITH days AS ("
    " SELECT rcept_dt FROM pub_filings.disclosure"
    " WHERE stock_code IS NOT NULL AND rcept_dt <= %(today)s AND rcept_dt > %(floor)s"
    " GROUP BY rcept_dt ORDER BY rcept_dt DESC LIMIT %(n_days)s)"
    " SELECT d.rcept_no, d.rcept_dt, d.first_seen_at, d.corp_code, d.stock_code, d.corp_name,"
    " d.corp_cls, d.title, d.kind, d.is_correction, d.kw_score, d.kw_matched, d.quality"
    " FROM pub_filings.disclosure d JOIN days ON days.rcept_dt = d.rcept_dt"
    " WHERE d.stock_code IS NOT NULL"
    " ORDER BY d.rcept_dt DESC, d.first_seen_at DESC, d.rcept_no DESC",
)
Q_PRELIMS: Final = Query(
    "prelims",
    "SELECT e.rcept_no, e.corp_code, e.rcept_dt, e.scope, e.fiscal_year, e.fiscal_q, e.is_annual,"
    " e.period_end, e.period_inferred, e.revenue_cur, e.revenue_prev_q, e.revenue_prev_y,"
    " e.op_cur, e.op_prev_q, e.op_prev_y, e.ni_cur, e.ni_prev_q, e.ni_prev_y, e.ni_owner_cur,"
    " e.eps_cur, e.yoy_reported, e.currency, e.notes, e.quality,"
    " coalesce(d.is_correction, false) AS is_correction"
    " FROM pub_filings.earnings_prelim e"
    " LEFT JOIN pub_filings.disclosure d ON d.rcept_no = e.rcept_no"
    " WHERE e.rcept_dt BETWEEN %(start)s AND %(end)s"
    " ORDER BY e.rcept_dt DESC, e.rcept_no DESC",
)
Q_OVERHANG: Final = Query(
    "overhang",
    "SELECT e.rcept_no, e.rcept_dt, e.corp_code, e.instrument, e.face_value, e.price, e.shares,"
    " e.exercise_start, e.exercise_end, e.quality, s.ratio_pct"
    " FROM pub_filings.overhang_event e"
    " LEFT JOIN LATERAL (SELECT x.ratio_pct FROM pub_filings.overhang_summary x"
    "   WHERE x.corp_code = e.corp_code AND x.as_of <= %(today)s"
    "   ORDER BY x.as_of DESC LIMIT 1) s ON true"
    " WHERE e.rcept_dt BETWEEN %(start)s AND %(end)s"
    " ORDER BY e.rcept_no DESC",
)
Q_INSIDER: Final = Query(
    "insider",
    "SELECT DISTINCT ON (w.corp_code) w.corp_code, w.as_of, w.net_shares, w.buy_shares,"
    " w.sell_shares, w.n_reports, w.n_reporters, w.ratio_pct, w.reason_known_pct, w.quality,"
    " w.computed_at"
    " FROM pub_filings.insider_window w"
    " WHERE w.days = %(days)s AND w.as_of BETWEEN %(since)s AND %(today)s"
    " ORDER BY w.corp_code, w.as_of DESC",
)
Q_SCHEDULE: Final = Query(
    "schedule",
    "SELECT s.corp_code, s.period_end, s.kind, s.fiscal_year, s.fiscal_q, s.planned_date,"
    " s.rcept_no, s.note, s.quality, s.computed_at"
    " FROM pub_filings.earnings_schedule s"
    " WHERE s.planned_date BETWEEN %(start)s AND %(end)s"
    " ORDER BY s.planned_date, s.corp_code, s.period_end, s.kind",
)


def link(rcept_no: str) -> str:
    """DART 뷰어 링크(공개 — 키 없음). `kbj.core.fin_rows.DisclosureRow.link` 와 같은 주소."""
    return DART_VIEWER + rcept_no


def pct_change(cur: float | None, base: float | None) -> float | None:
    """(당기 − 기준) ÷ |기준| × 100 — 기준 0·없음은 None(docs/metrics.md §14)."""
    if cur is None or base is None or base == 0:
        return None
    return (cur - base) / abs(base) * 100.0


def _month_start_back(d: date, months: int) -> date:
    m = d.month - 1 - months
    return date(d.year + m // 12, m % 12 + 1, 1)


def prelim_label(fiscal_year: int, fiscal_q: int, period_end: date, *, is_annual: bool) -> str:
    """`2026 3Q`(12월 결산) · `FY26 2Q (26.07~26.09)`(비12월) · 연간 잠정은 `2026 연간`.

    엔진 `kbj.engines.filings.earnings.period_label` 과 같은 결과(시험이 대조한다 — 계약 ⑧ 로 import
    하지 못한다).
    """
    if is_annual:
        return f"{fiscal_year} 연간"
    try:
        fp = FiscalPeriod(fiscal_year, fiscal_q, _month_start_back(period_end, 2), period_end)
    except ValueError:
        return f"{fiscal_year} {fiscal_q}Q"
    return fp.label


def day_start(d: date) -> datetime:
    """DART 날짜 값의 기준 시각 — 그날 00:00 KST(로그인 API `dart_as_of` 와 같은 뜻)."""
    return datetime(d.year, d.month, d.day, tzinfo=KST)


def _stale(as_of: datetime, now: datetime, quality: Quality, notes: list[str]) -> Quality:
    if now - as_of > STALE_AFTER:
        notes.append("원천 수집이 7일 넘게 멈췄다 — 값이 오래됐다")
        return "stale"
    return quality


def _file(
    name: str,
    as_of: datetime,
    now: datetime,
    rows_q: Sequence[str],
    data: dict[str, Any],
    notes: list[str],
) -> PublicFile:
    quality = _stale(as_of, now, worst_quality(rows_q), notes)
    return PublicFile(
        name=name,
        source=DART,
        as_of=as_of,
        quality=quality,
        data=data,
        notes=tuple(notes),
        compact=True,
    )


def _corp_cols(corps: Corps, corp_code: str) -> dict[str, Any] | None:
    c = corps.stock_of(corp_code)
    if c is None:
        return None
    return {"stock_code": c.stock_code, "corp_name": c.name, "market": c.market}


# ── 공시 피드 ──────────────────────────────────────────────────────────────────────────


def _feed_row(r: Row, cfg: FinConfig) -> dict[str, Any]:
    score = won(r.get("kw_score"))
    cls = r.get("corp_cls")
    return {
        "rcept_no": r["rcept_no"],
        "rcept_dt": day(r["rcept_dt"]),
        "first_seen_at": stamp(r["first_seen_at"]),
        "corp_code": r["corp_code"],
        "stock_code": r["stock_code"],
        "corp_name": r["corp_name"],
        "corp_cls": cls,
        "title": r["title"],
        "kind": r["kind"],
        "is_correction": bool(r["is_correction"]),
        "kw_score": score,
        "kw_matched": list(r.get("kw_matched") or []),
        "kw_level": cfg.filings.importance_levels.level(score) if score is not None else None,
        "quality": str(r["quality"]),
        "link": link(str(r["rcept_no"])),
    }


def _feed(rows: Sequence[Row], corps: Corps, now: datetime, cfg: FinConfig) -> PublicFile:
    picked = [r for r in rows if r.get("stock_code") in corps.by_stock]
    days = sorted({r["rcept_dt"] for r in rows}, reverse=True)
    by_kind: dict[str, int] = {}
    for r in picked:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    as_of: datetime = max(r["first_seen_at"] for r in rows)
    data: dict[str, Any] = {
        "dates": [day(d) for d in days],
        "n_total": len(picked),
        "by_kind": dict(sorted(by_kind.items())),
        "rows": [_feed_row(r, cfg) for r in picked],
    }
    notes = [
        f"공시가 있는 최근 {FEED_DAYS}일(DART 접수일)의 상장사 공시",
        "접수 시각은 우리가 처음 본 시각(DART 응답은 날짜만 — first_seen_at)",
        "kw_level = 제목 키워드 점수만의 중요도(관심종목·시총 가산은 로그인 화면)",
    ]
    return _file(FEED_NAME, as_of, now, [str(r["quality"]) for r in picked], data, notes)


# ── 잠정실적 ───────────────────────────────────────────────────────────────────────────


def _yoy_reported(v: object) -> dict[str, float | None]:
    if not isinstance(v, Mapping):
        return {}
    return {k: num(x) for k, x in canon(v).items()}  # jsonb — 키 정렬


def _notes(v: object) -> list[str]:
    if not isinstance(v, list | tuple):
        return []
    return [str(x) for x in v]  # pyright: ignore[reportUnknownVariableType]


def _prelim_row(r: Row, who: Mapping[str, Any]) -> dict[str, Any]:
    fy, fq = int(r["fiscal_year"]), int(r["fiscal_q"])
    annual = bool(r["is_annual"])
    rev, op = won(r.get("revenue_cur")), won(r.get("op_cur"))
    return {
        "rcept_no": r["rcept_no"],
        "rcept_dt": day(r["rcept_dt"]),
        "corp_code": r["corp_code"],
        **who,
        "scope": r["scope"],
        "period": f"{fy}FY" if annual else f"{fy}Q{fq}",
        "label": prelim_label(fy, fq, r["period_end"], is_annual=annual),
        "fiscal_year": fy,
        "fiscal_q": fq,
        "is_annual": annual,
        "period_end": day(r["period_end"]),
        "period_inferred": bool(r["period_inferred"]),
        "currency": r["currency"],
        "revenue_cur": rev,
        "revenue_prev_q": won(r.get("revenue_prev_q")),
        "revenue_prev_y": won(r.get("revenue_prev_y")),
        "op_cur": op,
        "op_prev_q": won(r.get("op_prev_q")),
        "op_prev_y": won(r.get("op_prev_y")),
        "ni_cur": won(r.get("ni_cur")),
        "ni_prev_q": won(r.get("ni_prev_q")),
        "ni_prev_y": won(r.get("ni_prev_y")),
        "ni_owner_cur": won(r.get("ni_owner_cur")),
        "eps_cur": num(r.get("eps_cur")),
        "rev_yoy": pct_change(rev, won(r.get("revenue_prev_y"))),
        "op_yoy": pct_change(op, won(r.get("op_prev_y"))),
        "rev_qoq": pct_change(rev, won(r.get("revenue_prev_q"))),
        "op_qoq": pct_change(op, won(r.get("op_prev_q"))),
        "yoy_reported": _yoy_reported(r.get("yoy_reported")),
        "is_correction": bool(r.get("is_correction")),
        "quality": str(r["quality"]),
        "notes": _notes(r.get("notes")),
        "link": link(str(r["rcept_no"])),
    }


def _prelims(
    rows: Sequence[Row], corps: Corps, now: datetime, as_of: datetime, start: date, end: date
) -> PublicFile:
    out: list[dict[str, Any]] = []
    for r in rows:
        who = _corp_cols(corps, str(r["corp_code"]))
        if who is not None:
            out.append(_prelim_row(r, who))
    data: dict[str, Any] = {"from": day(start), "to": day(end), "rows": out}
    notes = [
        f"최근 {PRELIM_DAYS}일(접수일) 잠정실적 — 금액은 원(본문 단위 × 배수)",
        "YoY·QoQ = (당기 − 기준) ÷ |기준| × 100 — 기준 0·없음은 null",
        "컨센서스 대비 서프라이즈는 로그인 화면(공개 출처 없음)",
    ]
    return _file(PRELIM_NAME, as_of, now, [o["quality"] for o in out], data, notes)


# ── 오버행 ─────────────────────────────────────────────────────────────────────────────


def _overhang(rows: Sequence[Row], corps: Corps, now: datetime, as_of: datetime) -> PublicFile:
    out: list[dict[str, Any]] = []
    for r in rows:
        who = _corp_cols(corps, str(r["corp_code"]))
        if who is None:
            continue
        out.append(
            {
                "rcept_no": r["rcept_no"],
                "rcept_dt": day(r["rcept_dt"]),
                "corp_code": r["corp_code"],
                **who,
                "instrument": r["instrument"],
                "face_value": won(r.get("face_value")),
                "price": num(r.get("price")),
                "shares": won(r.get("shares")),
                "exercise_start": day(r.get("exercise_start")),
                "exercise_end": day(r.get("exercise_end")),
                "ratio_pct": num(r.get("ratio_pct")),
                "quality": str(r["quality"]),
                "link": link(str(r["rcept_no"])),
            }
        )
    data: dict[str, Any] = {"days": OVERHANG_DAYS, "rows": out}
    notes = [
        f"최근 {OVERHANG_DAYS}일 오버행 결정(CB·BW·EB·유상·무상증자)",
        "ratio_pct = 회사 오버행 비율(최근 요약 — 분모 DART 주식총수, 보통주 발행)",
        "오버행 금액·ITM(시세 기반)은 로그인 화면",
    ]
    return _file(OVERHANG_NAME, as_of, now, [o["quality"] for o in out], data, notes)


# ── 내부자 ─────────────────────────────────────────────────────────────────────────────


def _insider(rows: Sequence[Row], corps: Corps, now: datetime, feed_as_of: datetime) -> PublicFile:
    picked: list[tuple[dict[str, Any], datetime]] = []
    for r in rows:
        who = _corp_cols(corps, str(r["corp_code"]))
        if who is None or not won(r.get("n_reports")):
            continue
        row = {
            "corp_code": r["corp_code"],
            **who,
            "as_of": day(r["as_of"]),
            "n_reports": won(r["n_reports"]),
            "n_reporters": won(r["n_reporters"]),
            "net_shares": won(r["net_shares"]),
            "buy_shares": won(r["buy_shares"]),
            "sell_shares": won(r["sell_shares"]),
            "ratio_pct": num(r.get("ratio_pct")),
            "reason_known_pct": num(r.get("reason_known_pct")),
            "quality": str(r["quality"]),
        }
        picked.append((row, r["computed_at"]))

    def rank(item: tuple[dict[str, Any], datetime]) -> tuple[int, float, str]:
        ratio = item[0]["ratio_pct"]
        return (ratio is None, -abs(ratio) if ratio is not None else 0.0, str(item[0]["corp_code"]))

    picked.sort(key=rank)
    top = picked[:INSIDER_TOP]
    as_of = max((t for _, t in top), default=feed_as_of)
    data: dict[str, Any] = {
        "days": INSIDER_DAYS,
        "label": INSIDER_LABEL,
        "n_total": len(picked),
        "rows": [row for row, _ in top],
    }
    notes = [
        f"{INSIDER_LABEL} — 최근 {INSIDER_DAYS}일 임원·주요주주 소유보고, 회사별 최신 창",
        f"비율(순증감 ÷ DART 주식총수) 절댓값 큰 순 상위 {INSIDER_TOP} — 비율 없는 회사는 뒤",
        "보고자 이름·금액(시세 기반)은 싣지 않는다",
    ]
    return _file(INSIDER_NAME, as_of, now, [row["quality"] for row, _ in top], data, notes)


# ── 실적 일정 ──────────────────────────────────────────────────────────────────────────


def schedule_row(r: Row) -> dict[str, Any]:
    """실적 일정 한 줄(공통 칸 — 회사 이름 칸은 호출자가 붙인다)."""
    return {
        "corp_code": r["corp_code"],
        "period_end": day(r["period_end"]),
        "kind": r["kind"],
        "planned_date": day(r["planned_date"]),
        "fiscal_year": int(r["fiscal_year"]),
        "fiscal_q": int(r["fiscal_q"]),
        "rcept_no": r.get("rcept_no"),
        "note": r.get("note"),
        "quality": str(r["quality"]),
    }


def _schedule(
    rows: Sequence[Row], corps: Corps, now: datetime, feed_as_of: datetime, start: date, end: date
) -> PublicFile:
    out: list[dict[str, Any]] = []
    stamps: list[datetime] = []
    for r in rows:
        who = _corp_cols(corps, str(r["corp_code"]))
        if who is None:
            continue
        out.append({**schedule_row(r), **who})
        stamps.append(r["computed_at"])
    as_of = max(stamps, default=feed_as_of)
    data: dict[str, Any] = {"from": day(start), "to": day(end), "rows": out}
    notes = [
        f"오늘부터 {SCHEDULE_DAYS}일 실적 일정",
        "pre_announce = 결산실적공시예고 본문 예정일, statutory = 법정 제출기한(추정)",
    ]
    return _file(SCHEDULE_NAME, as_of, now, [o["quality"] for o in out], data, notes)


# ── 묶음 ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FilingsOut:
    files: tuple[PublicFile, ...]
    skipped: tuple[str, ...]
    feed_as_of: datetime | None


def build_filings(read: Reader, corps: Corps, *, now: datetime, cfg: FinConfig) -> FilingsOut:
    """다섯 파일을 읽고 만든다. 공시 표에 상장사 행이 없으면 하나도 만들지 않는다(사유 하나)."""
    today = now.astimezone(KST).date()
    feed = read(
        Q_FEED,
        {"today": today, "floor": today - timedelta(days=FEED_FLOOR_DAYS), "n_days": FEED_DAYS},
    )
    if not feed or not corps:
        why = "공시 표에 상장사 공시가 없다" if not feed else "상장사 표(corp_code)가 비었다"
        return FilingsOut((), tuple(f"{n}: {why} — 만들지 않았다" for n in NAMES), None)
    feed_file = _feed(feed, corps, now, cfg)
    feed_as_of = feed_file.as_of
    p_start = today - timedelta(days=PRELIM_DAYS - 1)
    prelims = read(Q_PRELIMS, {"start": p_start, "end": today})
    o_start = today - timedelta(days=OVERHANG_DAYS - 1)
    overhang = read(Q_OVERHANG, {"start": o_start, "end": today, "today": today})
    insider = read(
        Q_INSIDER,
        {"days": INSIDER_DAYS, "since": today - timedelta(days=INSIDER_FRESH_DAYS), "today": today},
    )
    s_end = today + timedelta(days=SCHEDULE_DAYS - 1)
    schedule = read(Q_SCHEDULE, {"start": today, "end": s_end})
    files = (
        feed_file,
        _prelims(prelims, corps, now, feed_as_of, p_start, today),
        _overhang(overhang, corps, now, feed_as_of),
        _insider(insider, corps, now, feed_as_of),
        _schedule(schedule, corps, now, feed_as_of, today, s_end),
    )
    return FilingsOut(files, (), feed_as_of)
