"""데이터 품질 줄(metrics §20 ⑤) — 재료별 `stale`·`estimated`·`invalid`·늦은 기준일·`미준비` 목록.

모두 ok 면 `config/briefs.yaml texts.quality_ok`('자료 이상 없음'). 단계 밖 섹션(omitted)의 재료는
처음부터 없다(싣지 않고 품질 줄에도 적지 않는다 — ④).
"""

from __future__ import annotations

from collections.abc import Sequence

from kbj.config.briefs import BriefsConfig
from kbj.core.quality import Quality

from .sections import Material, esc

__all__ = ["quality_lines"]


def quality_lines(
    materials: Sequence[Material], cfg: BriefsConfig, *, empty_titles: Sequence[str] = ()
) -> tuple[str, ...]:
    """재료 상태 → 품질 줄. 같은 사유는 한 줄로 묶는다(순서는 재료 순서)."""
    groups: dict[str, list[str]] = {}

    def put(kind: str, what: str) -> None:
        items = groups.setdefault(kind, [])
        if what not in items:
            items.append(what)

    for m in materials:
        if m.status == "not_ready":
            why = f"({m.note})" if m.note else ""
            put("미준비", f"{m.name}{why} — {m.job}({m.due})" if m.job and m.due else m.name + why)
            continue
        if m.status == "late" and m.basis is not None:
            put("지연", f"{m.name}(기준일 {m.basis.isoformat()})")
        elif m.quality is Quality.STALE:
            put("지연", m.name)
        if m.quality is Quality.ESTIMATED:
            put("잠정", m.name)
        if m.quality is Quality.INVALID:
            put("무효", m.name)
    for t in empty_titles:
        put("빈 섹션", t)
    if not groups:
        return (esc(cfg.texts.quality_ok),)
    return tuple(esc(f"{k}: " + ", ".join(v)) for k, v in groups.items())
