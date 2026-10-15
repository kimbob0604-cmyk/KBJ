"""공개 `themes.json`(페이지 9 대테마 분류 — ADR 0039). 공개 표만 읽고, 네이버 유래 칸·로그인 출처
이름이 없고, 내용 검사(`check_payload`)를 통과한다. 위반 사례(네이버 칸이 끼면)는 검사가 잡는다."""

from __future__ import annotations

import json
from datetime import timedelta

from kbj.config.theme import load_theme
from kbj.engines.theme.classes import parse_classes
from kbj.services.public_export.manifest import (
    FILE_CAPS,
    check_payload,
    login_source_paths,
    price_problems,
    theme_problems,
)
from kbj.services.public_export.pub_read import Query
from kbj.services.public_export.themes_json import NAME, Q_THEMES, build_themes
from kbj.store.repos.theme_pub import MemoryThemePublicRepo
from tests.fixtures.synthetic.theme_master import BUILT_AT, build_files
from tests.unit.public_export.pub_rows import theme_rows

NOW = BUILT_AT + timedelta(days=1)


def _repo() -> MemoryThemePublicRepo:
    repo = MemoryThemePublicRepo()
    c = parse_classes(build_files(), cfg=load_theme().master)
    repo.replace_themes(c.themes, loaded_by="theme.classes_sync", received_at=NOW)
    return repo


def _read(repo: MemoryThemePublicRepo):
    def read(q: Query, _p: object) -> list[dict[str, object]]:
        assert q.name == Q_THEMES.name
        return theme_rows(repo)

    return read


def _text(f: object) -> str:
    from kbj.services.public_export.manifest import PublicFile

    assert isinstance(f, PublicFile)
    body = {"source": f.source, "as_of": f.as_of.isoformat(), "quality": f.quality,
            "notes": list(f.notes), "generated_at": NOW.isoformat(), "data": f.data}  # fmt: skip
    return json.dumps(body, ensure_ascii=False)


def test_sql_reads_public_schema_only() -> None:
    assert "pub_themes.theme" in Q_THEMES.sql and "prv_" not in Q_THEMES.sql


def test_builds_one_file_with_classification_only() -> None:
    out = build_themes(_read(_repo()), now=NOW)
    (f,) = out.files
    assert f.name == NAME and f.source == "stock-dashboard:data/master" and f.quality == "ok"
    assert f.as_of == BUILT_AT and f.data["n_themes"] == 36
    text = _text(f)
    assert check_payload(NAME, text) == []
    assert (
        login_source_paths(json.loads(text)) == [] and price_problems(NAME, json.loads(text)) == []
    )
    assert len(text.encode()) < FILE_CAPS[NAME]
    assert "원본 테마" not in text and "편입" not in json.dumps(f.data, ensure_ascii=False)


def test_empty_table_makes_no_file_and_old_build_is_stale() -> None:
    assert build_themes(_read(MemoryThemePublicRepo()), now=NOW).files == ()
    (f,) = build_themes(_read(_repo()), now=NOW + timedelta(days=40)).files
    assert f.quality == "stale"


def test_content_check_catches_login_fields() -> None:
    bad = {"source": "stock-dashboard:data/master", "as_of": NOW.isoformat(), "quality": "ok",
           "notes": [], "generated_at": NOW.isoformat(),
           "data": {"sections": [{"themes": [{"id": "a", "naver": [{"no": 1}]}]}]}}  # fmt: skip
    probs = check_payload(NAME, json.dumps(bad))
    assert any("로그인 전용 테마 칸" in p for p in probs)
    assert theme_problems("other.json", bad) == []
    named = dict(bad, data={}, source="stock-dashboard+NAVER")
    assert any("로그인 등급 출처" in p for p in check_payload(NAME, json.dumps(named)))
