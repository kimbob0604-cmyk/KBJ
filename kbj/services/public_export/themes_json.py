"""공개 대테마 분류 파일 — `themes.json`(페이지 9 공개판, ADR 0039).

- `pub_themes.theme` 만 읽는다(SQL 은 `pub_` 스키마, 계약 ⑧·㉑). 대테마 id·이름·섹션·밸류체인 —
  사용자가 만든 분류만. 네이버 유래(테마 이름·편입 종목·사유·종목 마스터)와 시세 기반 강도는 없다
  (로그인 — DATA_TIERS §2 9 행).
- 파일 data = 로그인 API `GET /api/themes` 의 `ThemeCatalog` 과 같은 모양(built_at·n_themes·
  sections[섹션 첫 등장 순서]·valuechains[이름순]) — 화면이 같은 코드로 그린다(시험이 대조).
- source = `stock-dashboard:data/master`(네이버 이름을 공개 source 칸에 두지 않는다 — LOGIN_SOURCES
  검사), as_of = 원천 built_at, quality = 표 품질(판이 `config/theme.yaml stale_days` 보다 오래되면
  stale).
- 표가 비면(수집 전) 파일 0·이유 0 — 빈 파일·가짜 값을 만들지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from kbj.config.theme import load_theme
from kbj.core.theme_rows import SOURCE_MASTER
from kbj.services.public_export.manifest import BuildOut, PublicFile, Quality, worst_quality
from kbj.services.public_export.pub_read import Query, Reader, stamp

__all__ = ["NAME", "Q_THEMES", "build_themes"]

NAME: Final = "themes.json"
NOTE: Final = (
    "대테마 분류는 사용자 작성(stock-dashboard 테마 마스터 — 주 1회). 테마 강도·편입 종목은 로그인"
)
_STALE_FALLBACK_DAYS: Final = 15

Q_THEMES: Final = Query(
    "themes",
    "SELECT id, name, section, valuechain, ord, quality, as_of FROM pub_themes.theme "
    "ORDER BY ord, id",
)


def _q(v: object) -> Quality:
    q = str(v)
    if q not in ("ok", "stale", "estimated", "invalid"):
        raise ValueError(f"모르는 quality: {q[:12]!r}")
    return q  # pyright: ignore[reportReturnType]


def _stale_days() -> int:
    try:
        return load_theme().master.stale_days
    except (OSError, ValueError):
        return _STALE_FALLBACK_DAYS


def build_themes(read: Reader, *, now: datetime) -> BuildOut:
    """공개 대테마 분류 파일(표가 비면 파일 0 + 이유 0)."""
    rows = read(Q_THEMES, {})
    if not rows:
        return BuildOut()  # 수집 전(theme.classes_sync 일 12:40) — 파일도 이유도 없다
    built = max(r["as_of"] for r in rows)
    if not isinstance(built, datetime):
        raise TypeError("as_of 가 시각이 아니다")
    sections: dict[str, list[dict[str, Any]]] = {}
    chains: dict[str, list[str]] = {}
    for r in rows:
        chain = r["valuechain"]
        sections.setdefault(str(r["section"]), []).append(
            {"id": r["id"], "name": r["name"], "section": r["section"], "valuechain": chain}
        )
        if chain is not None:
            chains.setdefault(str(chain), []).append(str(r["id"]))
    quality = worst_quality([_q(r["quality"]) for r in rows])
    if now - built > timedelta(days=_stale_days()):
        quality = worst_quality([quality, "stale"])
    data = {
        "built_at": stamp(built),
        "n_themes": len(rows),
        "sections": [{"section": k, "themes": v} for k, v in sections.items()],
        "valuechains": [{"key": k, "themes": chains[k]} for k in sorted(chains)],
    }
    f = PublicFile(
        name=NAME, source=SOURCE_MASTER, as_of=built, quality=quality, data=data, notes=(NOTE,)
    )
    return BuildOut(files=(f,))
