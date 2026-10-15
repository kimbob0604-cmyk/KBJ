"""`/api/themes*` 합성 픽스처(페이지 9 테마·밸류체인 — ADR 0039) + 공개 파일 `themes.json` 같은
모양.

`web/test/fixtures/api/themes/*.json` 만 쓴다. 다시 쓰기:
    uv run python -m tests.unit.api.web_fixtures_p9.themes

- 세계: API 합성 세계(`api_world` — 고정 시드) + 합성 테마
  마스터(`tests/fixtures/synthetic/theme_master.py`) + 보드 종목 하루(신고가 골든 합성 300종목) —
  `tests/unit/api/theme_world.py`.
- 파일: `catalog.json`(/api/themes), `strength_1|5|20.json`, `detail.json`(첫 대테마)·
  `detail_plain.json`(밸류체인이 없는 첫 대테마), `none.json`(수집 전 404 본문),
  `strength_none.json`(편입은 있는데 보드가 없을 때 404), `public.json`(공개 내보내기 `themes.json`
  — 같은 세계의 공개 표로 만든 봉투).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from fastapi.testclient import TestClient

from kbj.config.files import REPO_ROOT
from kbj.services.public_export.pub_read import Query
from kbj.services.public_export.themes_json import build_themes
from tests.unit.api.api_world import World, build_world
from tests.unit.api.theme_world import seed_board, seed_themes
from tests.unit.public_export.pub_rows import theme_rows

__all__ = ["DIR", "OUT", "build", "files_on_disk", "render"]

OUT: Final = REPO_ROOT / "web" / "test" / "fixtures" / "api"
DIR: Final = "themes"


def build(*, board: bool = True, master: bool = True, classes: bool = True) -> World:
    w = build_world()
    seed_themes(w.repos, now=w.clock(), master=master, classes=classes)
    if board:
        day = w.repos.board.last_day(w.clock().date())
        assert day is not None
        seed_board(w.repos, day)
    return w


def _get(w: World, path: str) -> tuple[int, Any]:
    with TestClient(w.app(), base_url="https://testserver") as c:
        w.login(c)
        r = c.get(path)
        return r.status_code, r.json()


def _text(body: Any) -> str:
    return json.dumps(body, ensure_ascii=False, indent=2) + "\n"


def public_body(w: World) -> dict[str, Any]:
    """같은 세계의 공개 표로 만든 공개 파일 봉투(public.export 와 같은 글자 모양)."""

    def read(q: Query, _p: object) -> list[dict[str, Any]]:
        assert q.name == "themes"
        return theme_rows(w.repos.theme_pub)

    out = build_themes(read, now=w.clock())
    (f,) = out.files
    return {
        "source": f.source,
        "as_of": f.as_of.isoformat(timespec="seconds"),
        "quality": f.quality,
        "notes": list(f.notes),
        "generated_at": w.clock().isoformat(timespec="seconds"),
        "data": f.data,
    }


def render() -> dict[str, str]:
    """`themes/<이름>.json` → 글자(결정적)."""
    out: dict[str, str] = {}
    w = build()
    code, cat = _get(w, "/api/themes")
    assert code == 200, cat
    out[f"{DIR}/catalog.json"] = _text(cat)
    for p in (1, 5, 20):
        code, body = _get(w, f"/api/themes/strength?period={p}")
        assert code == 200, body
        out[f"{DIR}/strength_{p}.json"] = _text(body)
    themes = [t for s in cat["data"]["sections"] for t in s["themes"]]
    first = themes[0]["id"]
    plain = next(t["id"] for t in themes if not t["valuechain"])
    for name, tid in (("detail", first), ("detail_plain", plain)):
        code, body = _get(w, f"/api/themes/{tid}")
        assert code == 200, body
        out[f"{DIR}/{name}.json"] = _text(body)
    code, body = _get(build(board=False, master=False, classes=False), "/api/themes")
    assert code == 404, body
    out[f"{DIR}/none.json"] = _text(body)
    code, body = _get(build(board=False), "/api/themes/strength?period=1")
    assert code == 404, body
    out[f"{DIR}/strength_none.json"] = _text(body)
    out[f"{DIR}/public.json"] = _text(public_body(w))
    return out


def files_on_disk(out: Path = OUT) -> set[str]:
    return {f"{DIR}/{p.name}" for p in (out / DIR).glob("*.json")}


def main() -> None:  # pragma: no cover — 손으로 다시 쓰기
    files = render()
    (OUT / DIR).mkdir(parents=True, exist_ok=True)
    for stale in files_on_disk() - set(files):
        (OUT / stale).unlink()
    for name, text in files.items():
        (OUT / name).write_text(text, encoding="utf-8")
    print(f"썼다: {len(files)}개 → {OUT / DIR}")


if __name__ == "__main__":  # pragma: no cover
    main()
