"""대테마 분류 해석·검증(공개 — ADR 0039 §2). `meta.json`·`themes.json` → `ThemeRow` 목록.

순수 함수(계약 ⑨). 공개 작업(`theme.classes_sync`)·공개 내보내기와 같은 등급 경계 안에 있다(계약 ㉑
— 로그인 원천·저장소·이 패키지의 로그인 모듈 `master`·`strength` 를 import 하지 않는다). themes.json
의 네이버 테마 목록(`naver`·`excluded_naver`)은 **읽지 않는다** — 사용자가 만든 칸
(id·name·section·valuechain)만 꺼낸다.

검증(거부 — `ClassesInvalid`): built_at(시간대 있는 ISO), themes 목록·객체, id 형식·중복, 이름·섹션,
valuechain 형식, 대테마 수 하한(`min_themes`)·직전 판 대비 감소율(`max_drop_ratio`).
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from kbj.config.theme import MasterCfg
from kbj.core.quality import Quality
from kbj.core.theme_rows import SOURCE_MASTER, ThemeRow, theme_id_ok

__all__ = [
    "CLASS_FILES",
    "META",
    "THEMES",
    "Classes",
    "ClassesInvalid",
    "built_at_of",
    "parse_classes",
    "theme_row",
]

META: Final = "meta.json"
THEMES: Final = "themes.json"
CLASS_FILES: Final[tuple[str, ...]] = (META, THEMES)


class ClassesInvalid(ValueError):
    """검증 실패 — 적재하지 않는다. `problems` 는 파일·칸·개수만(값을 싣지 않는다)."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = tuple(problems)
        head = "; ".join(problems[:5])
        more = f" 외 {len(problems) - 5}건" if len(problems) > 5 else ""
        super().__init__(f"테마 마스터 검증 실패 {len(problems)}건: {head}{more}")


@dataclass(frozen=True)
class Classes:
    built_at: datetime
    themes: tuple[ThemeRow, ...]


def load_json(name: str, raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        raise ClassesInvalid([f"{name}: JSON 이 아니다"]) from None


def built_at_of(meta_raw: bytes) -> datetime:
    """meta.json 의 built_at(바뀜 감지 키). 없거나 시간대가 없으면 `ClassesInvalid`."""
    meta = load_json(META, meta_raw)
    text = meta.get("built_at") if isinstance(meta, dict) else None
    ts: datetime | None = None
    if isinstance(text, str):
        try:
            ts = datetime.fromisoformat(text)
        except ValueError:
            ts = None
    if ts is None or ts.tzinfo is None or ts.utcoffset() is None:
        raise ClassesInvalid([f"{META}: built_at 이 없거나 시간대가 없다"])
    return ts


def theme_row(
    x: Mapping[str, Any], ord_: int, *, as_of: datetime, quality: Quality, probs: list[str]
) -> ThemeRow | None:
    """themes.json 대테마 한 객체 → 공개 행(사용자 작성 칸만). 문제면 probs 에 더하고 None."""
    tid = x.get("id")
    if not theme_id_ok(tid):
        probs.append(f"{THEMES}: themes[{ord_}] id 형식")
        return None
    chain = x.get("valuechain")
    try:
        return ThemeRow(
            id=str(tid),
            name=x.get("name"),  # pyright: ignore[reportArgumentType] — 행 자료형이 검증한다
            section=x.get("section"),  # pyright: ignore[reportArgumentType]
            valuechain=chain if isinstance(chain, str) and chain.strip() else None,
            ord=ord_,
            source=SOURCE_MASTER,
            as_of=as_of,
            quality=quality,
        )
    except ValueError as e:
        probs.append(f"{THEMES} {tid}: {str(e).split(':', 1)[0]}")
        return None


def theme_items(doc: Any, probs: list[str]) -> list[dict[str, Any]]:
    seq = doc.get("themes") if isinstance(doc, dict) else None
    if not isinstance(seq, list):
        probs.append(f"{THEMES}: 'themes' 목록이 없다")
        return []
    out: list[dict[str, Any]] = []
    for i, x in enumerate(seq):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if not isinstance(x, dict):
            probs.append(f"{THEMES}: themes[{i}] 가 객체가 아니다")
            out.append({})
            continue
        out.append(x)  # pyright: ignore[reportUnknownArgumentType]
    return out


def check_count(
    probs: list[str], key: str, n: int, floor: int, before: int | None, max_drop: float
) -> None:
    """개수 하한·직전 판 대비 감소율(넘으면 probs 에 한 줄)."""
    if n < floor:
        probs.append(f"{key} {n}개 — 하한 {floor} 미만")
    if before and n < before * (1.0 - max_drop):
        probs.append(f"{key} {before} → {n}개 — 직전 판보다 {max_drop:.0%} 넘게 줄었다")


def parse_classes(
    files: Mapping[str, bytes],
    *,
    cfg: MasterCfg,
    previous: int | None = None,
    quality: Quality = Quality.OK,
) -> Classes:
    """meta.json·themes.json → 대테마 분류. `previous` = 지금 공개 표의 대테마 수(감소율)."""
    missing = [f for f in CLASS_FILES if f not in files]
    if missing:
        raise ClassesInvalid([f"파일 없음: {', '.join(missing)}"])
    built = built_at_of(files[META])
    probs: list[str] = []
    rows: list[ThemeRow] = []
    for i, x in enumerate(theme_items(load_json(THEMES, files[THEMES]), probs)):
        if not x:
            continue
        r = theme_row(x, i, as_of=built, quality=quality, probs=probs)
        if r is not None:
            rows.append(r)
    dup = [k for k, n in Counter(r.id for r in rows).items() if n > 1]
    if dup:
        probs.append(f"{THEMES}: id 중복 {len(dup)}개")
    check_count(probs, "themes", len(rows), cfg.min_themes, previous, cfg.max_drop_ratio)
    if probs:
        raise ClassesInvalid(probs)
    return Classes(built_at=built, themes=tuple(rows))
