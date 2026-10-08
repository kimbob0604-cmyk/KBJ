"""내용 검사 — 공개 산출물에 로그인 등급 출처 이름·로그인 API 흔적이 하나라도 있으면 실패(§7.4).

검사가 빈말이 아님을 보이려고 위반을 하나씩 심어 잡히는지 본다. 문제 문구에는 값이 아니라 규칙·
JSON 경로만 담긴다.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from kbj.core.time import KST
from kbj.services.public_export.manifest import (
    LOGIN_SOURCES,
    MANIFEST_NAME,
    PublicFile,
    check_payload,
    check_tree,
    forbidden_hits,
    login_source_paths,
)

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)


def _env(**over: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "source": "KBJ 거래 캘린더(코드 계산)",
        "as_of": "2026-10-07T05:30:00+09:00",
        "quality": "ok",
        "notes": [],
        "generated_at": "2026-10-07T05:30:02+09:00",
        "data": {"days": []},
    }
    env.update(over)
    return env


def test_clean_envelope_passes() -> None:
    assert check_payload("calendar.json", json.dumps(_env(), ensure_ascii=False)) == []


@pytest.mark.parametrize(
    "src",
    [
        "KIS",
        "KRX",
        "krx+kis",
        "KIS(잠정)",
        "ETF_ISSUERS:삼성",
        "Yahoo",
        "NAVER",
        "KRX가 공표",
        "한국거래소",
        "네이버 금융",
    ],
)
def test_login_source_names_rejected_at_any_depth(src: str) -> None:
    top = check_payload("x.json", json.dumps(_env(source=src), ensure_ascii=False))
    assert any("로그인 등급 출처" in p for p in top)
    deep = _env(data={"rows": [{"v": 1, "source": src}]})
    probs = check_payload("x.json", json.dumps(deep, ensure_ascii=False))
    assert any("$.data.rows[0].source" in p for p in probs)
    assert all(src not in p for p in probs)  # 값은 문구에 싣지 않는다


@pytest.mark.parametrize(
    "src", ["XKRX 달력", "KBJ 거래 캘린더(코드 계산)", "한국은행 공표 일정", "DART", "KOSIS"]
)
def test_public_names_allowed(src: str) -> None:
    assert not LOGIN_SOURCES.search(src)


@pytest.mark.parametrize(
    "needle",
    [
        "/api/market/summary",
        "/telegram/webhook",
        "auth/login",
        "X-KBJ-CSRF",
        "KBJ_WEB_USER",
        "__Host-kbj_session",
    ],
)
def test_login_api_traces_rejected(needle: str) -> None:
    text = json.dumps(_env(notes=[f"see {needle}"]), ensure_ascii=False)
    probs = check_payload("x.json", text)
    assert any("로그인 API 흔적" in p for p in probs)
    assert forbidden_hits(text)


@pytest.mark.parametrize(
    ("over", "word"),
    [
        ({"as_of": "2026-10-07T05:30:00"}, "as_of"),
        ({"generated_at": "2026-10-07"}, "generated_at"),
        ({"quality": "good"}, "quality"),
        ({"source": ""}, "source"),
    ],
)
def test_envelope_fields_required(over: dict[str, Any], word: str) -> None:
    probs = check_payload("x.json", json.dumps(_env(**over)))
    assert any(word in p for p in probs)


def test_not_json_and_not_envelope() -> None:
    assert check_payload("x.json", "{") == ["x.json: JSON 이 아님"]
    assert any("봉투가 아님" in p for p in check_payload("x.json", "[1, 2]"))
    no_data = {k: v for k, v in _env().items() if k != "data"}
    assert any("봉투 필드 없음" in p for p in check_payload("x.json", json.dumps(no_data)))


def test_login_source_paths_only_source_keys() -> None:
    v = {"name": "KRX", "rows": [{"source": "KIS"}, {"source": "DART"}]}
    assert login_source_paths(v) == ["$.rows[0].source"]


def test_public_file_validates() -> None:
    with pytest.raises(ValueError):
        PublicFile(name="../x.json", source="s", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name=MANIFEST_NAME, source="s", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name="x.json", source=" ", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name="x.json", source="s", as_of=datetime(2026, 10, 7), quality="ok", data={})  # noqa: DTZ001


def _tree(tmp_path: Path) -> Path:
    from kbj.services.public_export.export import write_files

    f = PublicFile(
        name="calendar.json",
        source="KBJ 거래 캘린더(코드 계산)",
        as_of=NOW,
        quality="ok",
        data={"days": []},
    )
    write_files([f], tmp_path, now=NOW)
    return tmp_path


def test_check_tree_clean(tmp_path: Path) -> None:
    assert check_tree(_tree(tmp_path)) == []


def test_check_tree_detects_tamper_extra_and_missing(tmp_path: Path) -> None:
    d = _tree(tmp_path)
    cal = d / "calendar.json"
    env = json.loads(cal.read_text(encoding="utf-8"))
    env["data"]["rows"] = [{"source": "KRX"}]
    cal.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    (d / "stray.json").write_text("{}", encoding="utf-8")
    probs = check_tree(d)
    assert any("sha256 불일치" in p for p in probs)
    assert any("로그인 등급 출처" in p for p in probs)
    assert any("stray.json" in p and "manifest 에 없는" in p for p in probs)
    cal.unlink()
    assert any("파일이 없음" in p for p in check_tree(d))


def test_check_tree_meta_mismatch_and_no_manifest(tmp_path: Path) -> None:
    d = _tree(tmp_path)
    m = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    m["files"][0]["quality"] = "stale"
    (d / MANIFEST_NAME).write_text(json.dumps(m), encoding="utf-8")
    assert any("quality" in p and "다름" in p for p in check_tree(d))
    assert check_tree(tmp_path / "nothing") == ["manifest.json: 없음"]
