"""SD 검사 스크립트 규칙의 kbj 승격(docs/p5_design.md §1.5·§1.9 — 원본 스크립트는 legacy 에 남는다).

- `legacy/stock_dashboard/scripts/check_brief_sections.py` — 본문 조립: 빈 섹션은
  '데이터 없음(점검 필요)'으로 내보내지 않고 뺀다(SKIP_TITLES), 신고가 축 줄·기준 표기 한 번·
  빼던 섹션이 돌아오지 않음.
- `check_closing_brief.py` 규칙 1~5 중 kbj 에 해당하는 것 — 16:00 cron(시각 한 곳), 하루 1회(dedup
  daily), 캐치업(평일·아직 안 보낸 날·시한), misfire(캐치업 시한), 재기동 캐치업(실행기의
  catch_up) —
  등록부·notifier 정책 단언으로.
- `check_futures_us_index.py` 규칙 1~3 — 미국 지수 줄의 미국 거래일·장중 '현재'·못 받으면 옛 값 대신
  '못 받음'. 규칙 4~5(코스피200 선물 근월·원월)는 P7(D-P5-15).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

from kbj.config.files import config_path
from kbj.core.quality import Quality
from kbj.reports.briefs import morning_brief as M
from kbj.reports.briefs.job import BUILDERS, build
from kbj.reports.briefs.render import assemble
from kbj.reports.briefs.sections import SectionOut
from kbj.services.notifier.policy import dedup_key, load_notify_config
from kbj.services.scheduler.registry import Registry
from tests.unit.reports.briefs.brief_world import (
    CLOSING,
    MORNING,
    cfg,
    closing_repos,
    ctx,
    morning_repos,
)

REG = Registry.load(config_path("jobs.yaml"))
NOTIFY = load_notify_config()
NY = ZoneInfo("America/New_York")


def job(name: str) -> Any:  # 등록부 JobSpec
    return next(j for j in REG.jobs if j.name == name)


# ── check_brief_sections ─────────────────────────────────────────────────────────────


def _closing_with(**over: object) -> tuple[str, object]:
    builders = {**BUILDERS["closing"], **over}
    brief = assemble("closing", ctx(closing_repos(), CLOSING), builders)
    from kbj.reports.briefs.render import render

    return render(brief), brief


def test_빈_섹션은_데이터없음_줄로_나가지_않고_빠진다() -> None:
    empty = lambda _: SectionOut(source="t", as_of=CLOSING, quality=Quality.OK)  # noqa: E731
    text, _ = _closing_with(flows=empty)
    assert "<b>수급 동향</b>" not in text
    assert "데이터 없음 (점검 필요)" not in text
    assert "빈 섹션: 수급 동향" in text  # SKIP 목록 밖이면 품질 줄이 말한다(SD '⚠️ 빈 섹션')


def test_SKIP_목록의_빈_섹션은_경고도_없다() -> None:
    brief = assemble("morning", ctx(morning_repos(), MORNING), BUILDERS["morning"])
    assert "한국 매크로(새 공표)" in cfg().skip_titles
    assert brief.section("kr_macro") is None
    assert brief.empty_titles == ()


def test_신고가_축마다_줄이_있다() -> None:
    text = build("closing", ctx(closing_repos(), CLOSING))[1]
    for icon, label in (("🏔", "역사적"), ("📈", "52주"), ("📊", "120일")):
        assert f"{icon} {label} 신고가" in text


def test_신고가_기준_표기는_한_줄이고_어느_기준인지_적는다() -> None:
    text = build("closing", ctx(closing_repos(), CLOSING))[1]
    assert text.count("종가 기준") == 1


def test_빼던_섹션이_돌아오지_않는다() -> None:
    text = build("closing", ctx(closing_repos(), CLOSING))[1]
    for t in ("AI 추천", "GEX·시그널", "옵션/선물"):
        assert t not in text
    omitted = {s.key for s in cfg().closing.sections if s.status == "omitted"}
    assert omitted == {"gex_signals"}


# ── check_closing_brief ───────────────────────────────────────────────────────────────


def test_1_마감_요약은_평일_16시_시각은_한_곳() -> None:
    j = job("brief.closing")
    assert j.schedule.cron == "0 16 * * 1-5"
    assert cfg().closing.send_at == "16:00"
    assert j.owner == "kbj.reports.briefs:closing"


def test_2_하루_한_번_같은_날_두_번째는_같은_중복_키() -> None:
    pol = NOTIFY.policy("brief.closing")
    assert pol.dedup == "daily"
    d = date(2026, 11, 17)
    k1 = dedup_key(pol, as_of=d, subject=None, body_sha256="a" * 64, now=CLOSING)
    k2 = dedup_key(pol, as_of=d, subject=None, body_sha256="b" * 64,
                   now=CLOSING + timedelta(hours=4))  # fmt: skip
    k3 = dedup_key(pol, as_of=d + timedelta(days=1), subject=None, body_sha256="a" * 64,
                   now=CLOSING + timedelta(days=1))  # fmt: skip
    assert k1 == k2 and k1 != k3
    # 보내는 작업은 하나(U2)
    assert [j.name for j in REG.jobs if j.notify and j.notify.kind == "brief.closing"] == [
        "brief.closing"
    ]


def test_3_밀리면_캐치업은_평일_시한까지만() -> None:
    j = job("brief.closing")
    assert j.schedule.when == "trading_day"
    assert j.schedule.catch_up_until == "20:30"
    assert NOTIFY.policy("brief.closing").catch_up_until == time(20, 30)


def test_4_재시도로_두_번_보내지_않는다() -> None:
    # SD 의 misfire_grace_time 자리 — kbj 는 캐치업 시한이 정각을 놓친 날을 받는다. 재시도는 0
    assert job("brief.closing").retry.max == 0
    assert job("brief.morning").retry.max == 0


def test_5_재기동_캐치업_아침도_시한이_있다() -> None:
    j = job("brief.morning")
    assert j.schedule.cron == "10 8 * * *"
    assert j.schedule.catch_up_until == "09:00"
    assert cfg().morning.send_at == "08:10"
    assert NOTIFY.policy("brief.morning").dedup == "daily"


# ── check_futures_us_index 1~3 ───────────────────────────────────────────────────────


def bar(d: date, close: float | None) -> SimpleNamespace:
    return SimpleNamespace(date=d, close=close, quality=Quality.OK)


def ny(y: int, m: int, d: int, hh: int, mm: int) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=NY)


NAMES = (("^GSPC", "S&P 500"), ("^IXIC", "NASDAQ"))


def test_1_한국_16시에는_전날_미국_종가와_미국_거래일() -> None:
    d21, d22 = date(2026, 9, 21), date(2026, 9, 22)
    bars = {"^GSPC": [bar(d21, 6600.0), bar(d22, 6633.0)],
            "^IXIC": [bar(d21, 22000.0), bar(d22, 21890.0)]}  # fmt: skip
    lines = M.us_index_lines(bars, NAMES, ny(2026, 9, 23, 3, 0))
    assert lines[0] == "S&amp;P 500 6,633.00 +0.50% <i>(09/22 종가)</i>"
    assert lines[1].startswith("NASDAQ 21,890.00 -0.50%")
    assert not any("⚠" in x for x in lines)


def test_2_뉴욕_장중이면_종가라_적지_않는다() -> None:
    bars = {"^GSPC": [bar(date(2026, 9, 22), 6600.0), bar(date(2026, 9, 23), 6633.0)]}
    (line,) = M.us_index_lines(bars, NAMES[:1], ny(2026, 9, 23, 11, 0))
    assert "09/23 현재" in line and "종가" not in line


def test_3_못_받으면_옛_값을_내지_않고_한_줄() -> None:
    bars = {"^IXIC": [bar(date(2026, 9, 22), 1.0)]}
    lines = M.us_index_lines(bars, NAMES, ny(2026, 9, 23, 3, 0))
    assert lines == ["미국 지수 수신 실패 — S&amp;P 500, NASDAQ"]
    assert "1.00" not in lines[0]


def test_3_종가가_비면_그_봉은_쓰지_않는다() -> None:
    bars = {"^GSPC": [bar(date(2026, 9, 18), 5334.3), bar(date(2026, 9, 21), 5394.4),
                      bar(date(2026, 9, 22), None)]}  # fmt: skip
    (line,) = M.us_index_lines(bars, NAMES[:1], ny(2026, 9, 23, 6, 22))
    assert line.endswith("<i>(09/21 종가)</i>")  # 묵은 값은 그 날짜를 그대로 적는다(SD 4-1)


def test_1_아침_브리핑에_한국_날짜_신선도_꼬리표가_없다() -> None:
    text = build("morning", ctx(morning_repos(), MORNING))[1]
    assert "야간선물" not in text and "옵션/선물" not in text
    assert "(11/16 종가)" in text  # 미국 거래일(뉴욕) — 실행일(한국 11/17)이 아니다
