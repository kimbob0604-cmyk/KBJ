"""설정 파일(`config/*.yaml`) 찾기·읽기 — 비밀 아닌 튜닝값은 여기로만 읽는다(docs/secrets.md).

- 폴더는 `Settings.config_dir`(`KBJ_CONFIG_DIR`, 기본 `config`). 상대 경로는 **레포 루트**(이 파일
  기준 두 단계 위) 기준이다 — 작업 디렉터리가 어디든(시험이 `chdir` 해도) 같은 파일을 읽는다. 설치
  이미지처럼 코드가 site-packages 에 있으면 `KBJ_CONFIG_DIR` 을 절대 경로로 준다.
- 이름은 그 폴더 안의 상대 경로만 받는다(`jobs.yaml`). 절대 경로·`..` 는 `ValueError` — 설정
  이름으로 폴더 밖 파일을 읽지 않게 한다.
- YAML 은 `yaml.safe_load` 로만 연다. 파일이 없거나 문법이 틀리면 예외가 그대로 올라간다
  (절대 규칙 4 — 빈 설정으로 조용히 넘어가지 않는다).
- 캐시하지 않는다. 자주 읽는 쪽이 결과를 들고 있는다.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

from kbj.config.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]


class ConfigFileError(ValueError):
    """설정 파일 내용이 기대한 모양이 아니다(파일 이름을 담는다 — 값은 담지 않는다)."""


def config_dir(settings: Settings | None = None) -> Path:
    """설정 폴더의 절대 경로. `settings` 를 안 주면 환경(`KBJ_*`·`.env`)에서 읽는다."""
    d = (settings if settings is not None else Settings()).config_dir
    return d if d.is_absolute() else REPO_ROOT / d


def _check_name(name: str) -> None:
    if not name or not name.strip():
        raise ValueError("설정 파일 이름이 비었다")
    for flavor in (PurePosixPath(name), PureWindowsPath(name)):
        if flavor.is_absolute() or flavor.drive or ".." in flavor.parts:
            raise ValueError(f"설정 파일 이름은 설정 폴더 안의 상대 경로여야 한다: {name!r}")


def config_path(name: str, *, settings: Settings | None = None) -> Path:
    """`config_dir / name`. 있는지는 보지 않는다(읽을 때 없으면 FileNotFoundError)."""
    _check_name(name)
    return config_dir(settings) / name


def load_yaml(name: str, *, settings: Settings | None = None) -> object:
    """설정 YAML 하나를 읽는다(`yaml.safe_load`). 빈 파일은 None."""
    path = config_path(name, settings=settings)
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_yaml_mapping(name: str, *, settings: Settings | None = None) -> dict[str, Any]:
    """최상위가 매핑(키는 문자열)인 설정 YAML. 아니면 `ConfigFileError`."""
    data = load_yaml(name, settings=settings)
    if not isinstance(data, dict):
        raise ConfigFileError(f"{name}: 최상위가 매핑이 아니다({type(data).__name__})")
    out: dict[str, Any] = {}
    for k, v in data.items():  # pyright: ignore[reportUnknownVariableType]
        if not isinstance(k, str):
            raise ConfigFileError(f"{name}: 최상위 키가 문자열이 아니다({type(k).__name__})")
        out[k] = v
    return out
