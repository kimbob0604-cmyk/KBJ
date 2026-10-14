"""GEX 순수 계산 — GX `core/` 이식(P7 묶음 FND-C, docs/p7_design.md §1.2).

정본 명세는 `docs/metrics.md` §G(GX `docs/metrics.md` §0~§8 그대로). 수식·기본값은 GX 와 같고
경로만 바뀌었다(`core.<모듈>` → `kbj.core.gex.<모듈>`, `core.calendar` → `kbj.core.calendar`).
I/O 가 없다(계약 ③) — 파일·DB·HTTP 는 서비스(`kbj.services.engine`)·저장소가 한다.

**공개 이름은 모듈 경로로 고정한다**: `from kbj.core.gex.<모듈> import <이름>`. 모듈마다 같은 이름
(`FuturesQuote` — forward·metrics.futures, `IvSource` — iv·metrics.vol, `DAYS_PER_YEAR` — forward·
greeks)이 있어 이 패키지에서 이름을 납작하게 다시 내보내지 않는다. 이 파일은 하위 모듈을 미리
import 하지 않는다(`__getattr__` 로 처음 쓸 때 읽음) — `kbj.core.gex.records`(FND-B — 레코드 모델)를
읽을 때 vollib(import 약 1초)를 끌어오지 않게.

| 모듈 | 내용(GX 원래 자리) |
|---|---|
| `specs` | 상품 사양·호가 단위·Decimal 변환(`core/specs.py`) |
| `black76` | Black-76 가격·입력 검사(`core/black76.py`) |
| `greeks` | 그릭스(`core/greeks.py`) |
| `iv` | 내재변동성 역산(`core/iv.py`) |
| `chain` | ATM·창·거리 순(`core/chain.py`) |
| `preprocess` | 품질·가격 선택·제외 OI 비율(`core/preprocess.py`) |
| `forward` | 합성 선도·S_ref·만기까지 시간(`core/forward.py`) |
| `gex` | 만기별 평가·행사가 GEX·범위 선택·순 GEX(`core/gex.py`) |
| `levels` | 월·Flip·0DTE·ATM IV·기대변동폭·상위 레벨(`core/levels.py`) |
| `metrics.exposure` | VEX·CEX·GEX P/C(`core/metrics/exposure.py`) |
| `metrics.flow` | PCR·맥스페인·OI 변화·HIRO-lite·딜러 점검·대량 체결(`core/metrics/flow.py`) |
| `metrics.futures` | 선물 베이시스·지표(`core/metrics/futures.py`) |
| `metrics.vol` | 기간구조·25Δ 스큐·IV 랭크·IV−HV(`core/metrics/vol.py`) |

`records`(GX 레코드 pydantic 모델 — FND-B)·플래그 읽기(`core/features.py` → `kbj/config/gex.py`,
ENG)는 이 묶음이 옮기지 않는다. 녹화 체인 스냅샷 읽기(KIS 전광판 필드 — `gama`·`delta_val` 같은
KIS 그릭스 이름이 든다)는 `kbj.engines.gex.snapshot` — core 소스에는 KIS 그릭스 필드 이름이 없어야
한다(`tests/unit/core/gex/test_gex.py::test_gex_never_reads_kis_greeks`).
"""

import importlib
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kbj.core.gex import (
        black76,
        chain,
        forward,
        gex,
        greeks,
        iv,
        levels,
        metrics,
        preprocess,
        specs,
    )

__all__ = [
    "black76",
    "chain",
    "forward",
    "gex",
    "greeks",
    "iv",
    "levels",
    "metrics",
    "preprocess",
    "specs",
]


def __getattr__(name: str) -> ModuleType:
    """`kbj.core.gex.<모듈>` 을 처음 쓸 때 읽는다(위 설명 — vollib 를 미리 끌어오지 않게)."""
    if name in __all__:
        return importlib.import_module(f"{__name__}.{name}")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
