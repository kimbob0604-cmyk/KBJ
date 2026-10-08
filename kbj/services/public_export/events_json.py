"""`events.json` — 금통위 일정(상단 띠 '금통위 D-n' — docs/p3_design.md §4.4·§7.2).

- 원천: `config/calendar_events.yaml`(한국은행 공표 일정을 옮긴 수기 설정 — 공개 등급). 해석기는
  `kbj.config.markets.load_calendar_events` 하나.
- 오늘(KST, 당일 포함) 이후 일정만 싣는다. D-n 은 화면이 보는 날짜로 계산한다(파일이 하루 묵어도
  틀리지 않게 — 파일에는 날짜만).
- 품질: 설정 날짜는 한국은행 원 일정과 아직 대조하지 않았다(2차 출처 확인뿐 — calendar_events.yaml
  머리말 [확인 필요]) → `estimated` + 사유. 대조가 끝나 `PRIMARY_CHECKED` 를 켜면 ok.
  다음 일정이 하나도 없으면 `stale`(설정 갱신 필요 — R25).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final

from kbj.config.markets import CalendarEvents
from kbj.core.time import KST
from kbj.services.public_export.manifest import PublicFile, Quality

__all__ = ["NAME", "PRIMARY_CHECKED", "SOURCE", "build_events"]

NAME: Final = "events.json"
SOURCE: Final = "한국은행 공표 일정(수기 설정)"
# [확인 필요] 2026 금통위 날짜를 한국은행 홈페이지 '금융통화위원회 > 회의 일정'과 대조하면 True 로.
# (설정 파일에 대조 여부 필드가 생기면 그 값을 읽는다 — 묶음 M 요청)
PRIMARY_CHECKED: Final = False
_KIND_LABEL: Final = {"bok_mpc": "금통위"}


def build_events(
    now: datetime, events: CalendarEvents, *, primary_checked: bool = PRIMARY_CHECKED
) -> PublicFile:
    today = now.astimezone(KST).date()
    rows: list[dict[str, Any]] = [
        {
            "date": e.date.isoformat(),
            "kind": e.kind,
            "label": _KIND_LABEL.get(e.kind, e.kind),
            "title": e.title,
            "source": e.source,
        }
        for e in events.events
        if e.date >= today
    ]
    nxt = events.upcoming(today)
    notes: list[str] = ["D-n 은 화면이 보는 날짜로 계산한다"]
    quality: Quality = "ok"
    if not primary_checked:
        quality = "estimated"
        notes.append("일정은 2차 출처로 옮긴 값 — 한국은행 원 일정과 대조 전 [확인 필요]")
    if nxt is None:
        quality = "stale"
        notes.append("다음 금통위 일정이 설정에 없다 — config/calendar_events.yaml 갱신 필요")
    data: dict[str, Any] = {
        "events": rows,
        "next_bok_mpc": nxt.date.isoformat() if nxt is not None else None,
    }
    return PublicFile(
        name=NAME, source=SOURCE, as_of=now, quality=quality, data=data, notes=tuple(notes)
    )
