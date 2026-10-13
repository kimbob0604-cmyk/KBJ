"""브리핑 조립·렌더 — 섹션 구성(config/briefs.yaml) → `BriefSection` 행 → 텔레그램 HTML 한 통.

조립 규칙(metrics §20, SD `scripts/check_brief_sections.py` 승격 — `test_sd_rules.py`)
- `omitted`(단계 밖 — P7·P8) 섹션은 싣지 않고 품질 줄에도 적지 않는다.
- 재료가 시한까지 없으면 제목 아래 '미준비 — <작업>(<예정 시각>)' 한 줄(무른 의존).
- 빈 섹션: `empty: none_line` 이면 '일정 없음' 한 줄, 그 밖은 **제목 없이 뺀다**
  (SD SKIP_TITLES 승계) —
  `skip_titles`·`empty: skip` 이면 조용히(경고 로그만), 아니면 품질 줄에 '빈 섹션: <제목>'.
  '⚠️ 데이터 없음 (점검 필요)' 같은 빈 제목을 메시지에 내보내지 않는다.
- 마지막은 품질 줄(⑦). 4,096자 나눔은 notifier 가 HTML 태그를 지키며 한다(여기서 자르지 않는다).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from kbj.config.briefs import KindCfg, SectionCfg
from kbj.core.p5_rows import BriefSection
from kbj.core.quality import Quality

from .quality import quality_lines
from .sections import BriefCtx, Material, SectionOut, day_label, esc, worst

__all__ = ["HEADS", "Brief", "assemble", "render"]

log = logging.getLogger(__name__)

Kind = Literal["morning", "closing"]
Builder = Callable[[BriefCtx], SectionOut]

HEADS: dict[str, str] = {"morning": "☀️ 아침 브리핑", "closing": "🔔 마감 요약"}
SOURCE = "kbj.reports.briefs"


@dataclass(frozen=True)
class Brief:
    """조립된 브리핑 한 통 — 섹션 행(순서대로)과 빈 섹션 제목."""

    kind: Kind
    ctx: BriefCtx
    sections: tuple[BriefSection, ...]
    empty_titles: tuple[str, ...]
    dropped: tuple[str, ...]

    def section(self, key: str) -> BriefSection | None:
        return next((s for s in self.sections if s.key == key), None)


def _kind_cfg(bc: BriefCtx, kind: Kind) -> KindCfg:
    return bc.cfg.morning if kind == "morning" else bc.cfg.closing


def _is_skip(bc: BriefCtx, sc: SectionCfg) -> bool:
    return sc.empty == "skip" or sc.title in bc.cfg.skip_titles


def assemble(kind: Kind, bc: BriefCtx, builders: Mapping[str, Builder]) -> Brief:
    """config 순서대로 섹션 함수를 부른다. 한 섹션의 실패가 다른 섹션을 멈추지 않는다(격리) —
    예외는 그 섹션을 '미준비'로 적고 품질 줄에 남긴 뒤 로그로 올린다(삼키지 않는다)."""
    kc = _kind_cfg(bc, kind)
    out: list[BriefSection] = []
    mats: list[Material] = []
    empty: list[str] = []
    dropped: list[str] = []
    for sc in kc.sections:
        if sc.status == "omitted":
            out.append(BriefSection(kind, sc.key, sc.title, (), "omitted", None, None, None))
            continue
        if sc.key == "quality":
            continue
        fn = builders.get(sc.key)
        if fn is None:
            raise KeyError(f"섹션 함수가 없다: {kind}.{sc.key}")
        try:
            res = fn(bc)
        except Exception as e:
            log.exception("brief_section_failed", extra={"kind": kind, "section": sc.key})
            res = SectionOut(
                materials=(
                    Material(
                        sc.title, "not_ready", job=sc.source_job, due=sc.due, note=type(e).__name__
                    ),
                ),
                not_ready=True,
            )
        mats += res.materials
        if res.not_ready:
            line = (
                bc.cfg.texts.not_ready.format(job=sc.source_job, due=sc.due)
                if sc.source_job and sc.due
                else "미준비"
            )
            out.append(BriefSection(kind, sc.key, sc.title, (esc(line),), "not_ready",
                                    None, None, None))  # fmt: skip
            continue
        lines = res.lines
        if not lines:
            if sc.empty == "none_line":
                lines = (esc(sc.none_text or bc.cfg.texts.none_line),)
            else:
                log.warning("brief_section_empty", extra={"kind": kind, "section": sc.key})
                dropped.append(sc.title)
                if not _is_skip(bc, sc):
                    empty.append(sc.title)
                continue
        out.append(BriefSection(
            kind, sc.key, sc.title, tuple(lines), "ok", res.source or SOURCE,
            res.as_of or bc.now, res.quality or Quality.OK,
        ))  # fmt: skip
    q_lines = quality_lines(mats, bc.cfg, empty_titles=empty)
    q_sec = next(s for s in kc.sections if s.key == "quality")
    out.append(BriefSection(
        kind, "quality", q_sec.title, q_lines, "ok", SOURCE, bc.now,
        worst([m.quality for m in mats if m.status != "not_ready"]) or Quality.OK,
    ))  # fmt: skip
    return Brief(kind, bc, tuple(out), tuple(empty), tuple(dropped))


def render(brief: Brief) -> str:
    """텔레그램 HTML 한 통. omitted 는 싣지 않는다."""
    bc = brief.ctx
    hhmm = _kind_cfg(bc, brief.kind).send_at
    parts = [f"<b>{HEADS[brief.kind]}</b> · {esc(day_label(bc.day))} {hhmm}"]
    for s in brief.sections:
        if s.status == "omitted":
            continue
        parts.append(f"<b>{esc(s.title)}</b>\n" + "\n".join(s.lines))
    return "\n\n".join(parts)
