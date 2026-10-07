"""설정 파일(kbj.config.files)·새 설정 필드(`KBJ_SERVICE`·`KBJ_CONFIG_DIR`)·config/*.yaml 틀.

실제 `.env` 는 읽지 않는다(`Settings(_env_file=None)`, 바깥 KBJ_* 환경은 지운다).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from kbj.config import files
from kbj.config.files import ConfigFileError, config_dir, config_path, load_yaml, load_yaml_mapping
from kbj.config.settings import Settings

ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in list(os.environ):
        if name.upper().startswith("KBJ_"):
            monkeypatch.delenv(name)


def load(**kw: object) -> Settings:
    return Settings(_env_file=None, **kw)  # pyright: ignore[reportCallIssue, reportArgumentType]


def test_repo_root_and_default_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert files.REPO_ROOT == ROOT
    assert load().config_dir == Path("config")
    monkeypatch.chdir(tmp_path)  # 작업 디렉터리와 무관하게 레포 루트 기준
    assert config_dir(load()) == ROOT / "config"
    assert config_path("jobs.yaml", settings=load()) == ROOT / "config" / "jobs.yaml"


def test_config_dir_from_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KBJ_CONFIG_DIR", str(tmp_path))
    assert config_dir(load()) == tmp_path
    monkeypatch.setenv("KBJ_CONFIG_DIR", "")  # .env.example 을 복사만 한 빈 값 → 기본값
    assert load().config_dir == Path("config")
    monkeypatch.setenv("KBJ_CONFIG_DIR", "deploy/config")  # 상대 경로 → 레포 루트 기준
    assert config_dir(load()) == ROOT / "deploy" / "config"


@pytest.mark.parametrize("name", ["", " ", "/etc/passwd", "../secrets.yaml", "a/../../b", "C:\\x"])
def test_names_must_stay_inside_the_folder(name: str) -> None:
    with pytest.raises(ValueError):
        config_path(name, settings=load())


def test_load_yaml_and_mapping(tmp_path: Path) -> None:
    s = load(config_dir=tmp_path)
    (tmp_path / "a.yaml").write_text("version: 1\nx: [1, 2]\n", encoding="utf-8")
    (tmp_path / "list.yaml").write_text("- 1\n", encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("a: [\n", encoding="utf-8")
    (tmp_path / "intkey.yaml").write_text("1: a\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yaml").write_text("k: v\n", encoding="utf-8")
    assert load_yaml("a.yaml", settings=s) == {"version": 1, "x": [1, 2]}
    assert load_yaml_mapping("sub/b.yaml", settings=s) == {"k": "v"}
    with pytest.raises(ConfigFileError, match="매핑"):
        load_yaml_mapping("list.yaml", settings=s)
    with pytest.raises(ConfigFileError, match="문자열"):
        load_yaml_mapping("intkey.yaml", settings=s)
    with pytest.raises(yaml.YAMLError):
        load_yaml("bad.yaml", settings=s)
    with pytest.raises(FileNotFoundError):
        load_yaml("none.yaml", settings=s)


def test_repo_config_skeletons() -> None:
    """S0 틀: jobs.yaml(version·defaults — F 가 채운다), notify.yaml(빈 표 — E 가 채운다)."""
    s = load()
    jobs = load_yaml_mapping("jobs.yaml", settings=s)
    assert jobs["version"] == 1
    d = jobs["defaults"]
    assert d["tz"] == "Asia/Seoul" and d["enabled"] is False and d["deadline_min"] == 120
    assert d["retry"] == {"max": 3, "backoff_s": [60, 300, 900]}
    notify = load_yaml_mapping("notify.yaml", settings=s)
    assert notify["version"] == 1 and {"topics", "kinds", "legacy_kinds"} <= set(notify)
    limits = load_yaml_mapping("limits.yaml", settings=s)
    assert limits["version"] == 1


def test_service_field(monkeypatch: pytest.MonkeyPatch) -> None:
    assert load().service is None
    monkeypatch.setenv("KBJ_SERVICE", "auth")
    assert load().service == "auth"
    monkeypatch.setenv("KBJ_SERVICE", "   ")
    assert load().service is None
    for bad in ("Auth", "auth service", "-x"):
        monkeypatch.setenv("KBJ_SERVICE", bad)
        with pytest.raises(ValidationError):
            load()
