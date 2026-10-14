"""KBJ P7(묶음 ENG): 기능 플래그의 정본은 `kbj.config.gex`(docs/p7_design.md §1.3·D-P7-11) — 이름을
다시 내보낸다.

플래그 값은 KBJ `config/gex.yaml` 의 `features:` 가 정본이다(`FEATURES_PATH`). 이 폴더의
`config/features.yaml` 은 GX compose 마운트·통합 시험이 읽어서 남겨 두며, 값이 KBJ 와 같은지는
`tests/unit/test_kbj_gex_engine_bridge.py` 가 본다(두 벌이 어긋나지 않게 — WRAP 이 compose 를 옮기면 지운다).
옮긴 원본 시험은 KBJ `tests/unit/engines/gex/test_features.py`.
"""

from kbj.config.gex import *  # noqa: F403
from kbj.config.gex import (  # noqa: F401 — 밑줄 없는 이름 중 __all__ 밖은 없다. 명시
    CATALOG,
    CORE_NAMES,
    FEATURES_PATH,
    NEW_DEFAULT,
    FeatureError,
    Features,
    FeatureWatcher,
    FlagSpec,
    load_features,
)
