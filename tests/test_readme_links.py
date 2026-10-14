"""README·문서 지도의 상대 링크가 모두 있는 파일을 가리킨다(docs/p9_design.md §0.3 — P9 WRAP).

README 는 포트폴리오의 첫 화면이라 깨진 링크·없는 그림이 바로 보인다. 마크다운 링크
`[..](path)` 와 그림 `![..](path)` 중 http(s)·mailto·앵커만인 것은 빼고, 나머지는 그 문서 기준
경로가 있어야 한다. legacy/ 는 P9 에 비웠으므로 README 가 legacy 경로를 가리키지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ("README.md", "docs/architecture.md", "docs/adr/README.md")
_LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def _targets(doc: str) -> list[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    out: list[str] = []
    for raw in _LINK.findall(text):
        if raw.startswith(("http://", "https://", "mailto:", "#")):
            continue
        out.append(raw.split("#", 1)[0])
    return out


@pytest.mark.parametrize("doc", DOCS)
def test_relative_links_resolve(doc: str) -> None:
    base = (ROOT / doc).parent
    links = _targets(doc)
    assert links, doc
    missing = [t for t in links if not (base / t).resolve().exists()]
    assert missing == [], doc


def test_readme_shows_demo_media_and_no_legacy_paths() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for media in (
        "docs/media/kbj_demo.gif",
        "docs/media/kbj_demo_p1_market.png",
        "docs/media/kbj_demo_p3_gex.png",
        "docs/media/kbj_demo_p4_flows.png",
        "docs/media/kbj_demo_p8_stock.png",
    ):
        assert f"]({media})" in text, media
    assert "docker compose -f docker-compose.demo.yml up --build" in text
    assert "legacy/" not in text
    assert "```mermaid" in text
