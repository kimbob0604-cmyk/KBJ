"""GEX 확장 지표 — GX `core/metrics/` 이식(P7 FND-C). 공개 이름은 `kbj.core.gex.metrics.<모듈>`.

하위 모듈은 처음 쓸 때 읽는다(`kbj.core.gex` 와 같은 방식).
"""

import importlib
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kbj.core.gex.metrics import exposure, flow, futures, vol

__all__ = ["exposure", "flow", "futures", "vol"]


def __getattr__(name: str) -> ModuleType:
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
