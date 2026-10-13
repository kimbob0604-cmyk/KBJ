"""미국장 설정 로더 — `config/us.yaml`(ET `board/config/us.yaml` 을 git mv) + 신고가 엔진 절.

승격 원본: ET `board/engine/config.py:load`(P3 shim 판) — 엔진 절은 `config/board.yaml` 이 기본이고
`us.yaml` 에 같은 절이 있으면 `us.yaml` 이 이긴다(newhigh·integrity 등 — 252봉 정의, ADR 0001 Q10.
ADR 0017 의 국장 3축은 미국장에 걸지 않는다). 숫자는 이 두 파일에만 있다.

자체 사전(`us_sectors.yaml`·`us_overrides.yaml`)은 `config/knowledge/us/`(공개 — 자체 규칙).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from kbj.config.files import ConfigFileError, config_path
from kbj.config.settings import Settings
from kbj.engines.board.config import BoardConfig, knowledge_path

__all__ = ["US_FILE", "load_us", "us_config_path", "us_knowledge_path"]

US_FILE: Final = "us.yaml"
US_KNOWLEDGE: Final = "us"


def us_config_path(*, settings: Settings | None = None) -> Path:
    """`config/us.yaml`(legacy `run.py us_cfg`·`build.run` 도 이 경로를 읽는다)."""
    return config_path(US_FILE, settings=settings)


def us_knowledge_path(name: str, *, settings: Settings | None = None) -> Path:
    """`config/knowledge/us/<name>`(자체 미국 섹터 규칙·손수정)."""
    return knowledge_path(f"{US_KNOWLEDGE}/{name}", settings=settings)


def load_us(path: str | Path | None = None, *, settings: Settings | None = None) -> dict[str, Any]:
    """`config/board.yaml` 엔진 절 + `config/us.yaml`(이긴다). 새 dict 를 돌려준다."""
    p = Path(path) if path is not None else us_config_path(settings=settings)
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigFileError(f"{p.name}: 최상위가 매핑이 아니다")
    eng = BoardConfig.load(settings=settings).to_dict()
    eng.pop("service", None)  # kbj 국장 서비스만 읽는 절
    eng.update(data)
    for need in ("sources", "universe", "newhigh", "newlow", "brief", "integrity"):
        if need not in eng:
            raise ConfigFileError(f"{p.name}: '{need}' 절이 없다")
    return eng
