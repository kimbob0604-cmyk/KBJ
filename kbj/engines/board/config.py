"""보드 엔진 설정 — 임계값은 코드가 아니라 `config/board.yaml` 한 곳에만 있다(숫자는 한 곳).

ET `board/engine/config.py`(`load`:13·`themes`:19) + ET `board/config/settings.yaml` 의 엔진 절
(`newhigh`·`proximity`·`volume`·`resistance`·`giveback`·`themes`·`display`·`integrity`·`detect`·
`rankings`)을 옮겼다(docs/p3_design.md §1.3). legacy `config.load` 는 이 파일을 합쳐 읽는다 —
값이 두 벌이 되지 않는다.

- `BoardConfig` 는 dict 처럼 읽히는 `Mapping`(엔진 함수는 ET 그대로 `cfg['newhigh']['lookback']`
  처럼 읽는다 — 골든 비교와 legacy shim 이 같은 함수를 부른다). 엔진 함수는 아무 `Mapping` 이나
  받는다(legacy 시험의 dict 그대로).
- **모르는 키는 거부한다**(절·키 둘 다) — 철자 틀린 임계값이 조용히 기본값으로 돌지 않게.
- 지식 사전(`config/knowledge/{themes,sectors,sector_map}.yaml` — 자체 사전, conflict_map §1.8)도
  여기서 읽는다. 파일이 없거나 모양이 틀리면 예외가 그대로 올라간다(절대 규칙 4).
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Final

import yaml

from kbj.config.files import ConfigFileError, config_path
from kbj.config.settings import Settings

__all__ = [
    "BOARD_FILE",
    "SECTIONS",
    "BoardConfig",
    "knowledge_path",
    "load_sector_map",
    "load_taxonomy",
    "load_themes",
]

BOARD_FILE: Final = "board.yaml"
KNOWLEDGE_DIR: Final = "knowledge"
UNMAPPED: Final = "미분류"

# 절 → 허용 키. 값의 뜻·근거는 config/board.yaml 주석(ET settings.yaml 주석 그대로).
SECTIONS: Final[Mapping[str, frozenset[str]]] = {
    "newhigh": frozenset({"lookback", "labels", "priority", "min_display_kind", "default_basis"}),
    "proximity": frozenset({"max_gap_pct", "min_mktcap_eok", "narrow_days"}),
    "volume": frozenset({"avg_days"}),
    "resistance": frozenset({"thin_below", "thick_above"}),
    "giveback": frozenset({"ratio", "max_chg_pct", "min_pp"}),
    "themes": frozenset({"seed_confidence", "min_confidence_for_narration", "unmapped_bucket"}),
    "display": frozenset(
        {
            "min_mktcap_eok",
            "min_turnover_eok",
            "max_rows_achieved",
            "max_rows_proximity",
            "heatmap_groups",
            "heatmap_cells",
        }
    ),
    "integrity": frozenset({"split_guard_ratio", "suppress_hist_on_suspect", "min_history_days"}),
    "detect": frozenset({"volume_anomaly_mult", "multi_label_min", "proximity_cluster_min"}),
    "rankings": frozenset(
        {
            "excel_scale",
            "min_mktcap_eok",
            "min_turnover_eok",
            "min_sector_members",
            "top_per_sector",
            "rows_per_board",
        }
    ),
    # kbj 서비스(kbj.services.engine.board)만 읽는 절 — 엔진 계산에는 들어가지 않는다
    "service": frozenset({"series_days"}),
}
# 엔진이 `cfg[절][키]` 로 바로 읽는 것(없으면 KeyError 가 계산 중간에 난다 → 읽을 때 막는다)
_REQUIRED: Final[Mapping[str, frozenset[str]]] = {
    "newhigh": frozenset({"lookback", "labels", "priority", "default_basis"}),
    "proximity": frozenset({"max_gap_pct", "min_mktcap_eok", "narrow_days"}),
    "volume": frozenset({"avg_days"}),
    "resistance": frozenset({"thin_below", "thick_above"}),
    "giveback": frozenset({"ratio", "max_chg_pct"}),
    "themes": frozenset({"seed_confidence"}),
    "display": frozenset(
        {"max_rows_achieved", "max_rows_proximity", "heatmap_groups", "heatmap_cells"}
    ),
    "integrity": frozenset({"split_guard_ratio", "suppress_hist_on_suspect", "min_history_days"}),
    "detect": frozenset({"volume_anomaly_mult", "multi_label_min", "proximity_cluster_min"}),
}


class BoardConfig(Mapping[str, Any]):
    """보드 설정(읽기 전용 `Mapping`). 값은 깊은 복사본 — 원본 dict 를 바꿔도 따라 바뀌지 않는다."""

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        _validate(data)
        self._data: dict[str, Any] = copy.deepcopy(dict(data))

    # Mapping
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"BoardConfig({sorted(self._data)})"

    def to_dict(self) -> dict[str, Any]:
        """깊은 복사 dict(legacy `config.load` 가 settings.yaml 과 합칠 때)."""
        return copy.deepcopy(self._data)

    @property
    def series_days(self) -> int:
        """서비스가 읽는 일봉 창(거래일 수). 엔진 계산과 무관."""
        return int((self._data.get("service") or {}).get("series_days") or 420)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> BoardConfig:
        return cls(data)

    @classmethod
    def load(
        cls, path: str | Path | None = None, *, settings: Settings | None = None
    ) -> BoardConfig:
        """`config/board.yaml`(또는 path)을 읽는다. 모르는 키·빠진 키는 `ConfigFileError`."""
        p = Path(path) if path is not None else config_path(BOARD_FILE, settings=settings)
        with p.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ConfigFileError(f"{p.name}: 최상위가 매핑이 아니다")
        data.pop("meta", None)  # 판·시간대 표시(엔진은 안 읽는다)
        try:
            return cls(data)
        except ValueError as e:
            raise ConfigFileError(f"{p.name}: {e}") from None


def _validate(data: Mapping[str, Any]) -> None:
    unknown = sorted(set(data) - set(SECTIONS))
    if unknown:
        raise ValueError(f"모르는 절: {unknown}")
    for sec, keys in SECTIONS.items():
        body = data.get(sec)
        if body is None:
            if sec in _REQUIRED:
                raise ValueError(f"빠진 절: {sec}")
            continue
        if not isinstance(body, Mapping):
            raise ValueError(f"{sec}: 매핑이 아니다")
        bad = sorted(set(body) - keys)
        if bad:
            raise ValueError(f"{sec}: 모르는 키 {bad}")
        missing = sorted(_REQUIRED.get(sec, frozenset()) - set(body))
        if missing:
            raise ValueError(f"{sec}: 빠진 키 {missing}")
    nh = data["newhigh"]
    pri = list(nh["priority"])
    if "hist" not in pri or len(set(pri)) != len(pri):
        raise ValueError("newhigh.priority 는 hist 를 포함하고 겹치지 않아야 한다")
    if set(nh["lookback"]) - set(pri):
        raise ValueError("newhigh.lookback 의 라벨이 priority 에 없다")
    if nh["default_basis"] not in ("close", "high"):
        raise ValueError("newhigh.default_basis 는 close 또는 high")


# ── 지식 사전(config/knowledge) ────────────────────────────────────────────────────────


def knowledge_path(name: str, *, settings: Settings | None = None) -> Path:
    """`config/knowledge/<name>`(자체 사전 — themes·sectors·sector_map)."""
    return config_path(f"{KNOWLEDGE_DIR}/{name}", settings=settings)


def _yaml_mapping(p: Path) -> dict[str, Any]:
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigFileError(f"{p.name}: 최상위가 매핑이 아니다")
    return data


def load_themes(
    path: str | Path | None = None, *, settings: Settings | None = None
) -> dict[str, Any]:
    """2층 자체 테마 사전(themes.yaml — `themes.build` 의 입력)."""
    return _yaml_mapping(Path(path) if path else knowledge_path("themes.yaml", settings=settings))


def load_taxonomy(
    path: str | Path | None = None, *, settings: Settings | None = None
) -> dict[str, Any]:
    """1층 섹터 분류 체계(sectors.yaml — board48). 랭킹이 '미배정' 을 셀 때 쓴다."""
    return _yaml_mapping(Path(path) if path else knowledge_path("sectors.yaml", settings=settings))


def load_sector_map(
    path: str | Path | None = None, *, settings: Settings | None = None
) -> tuple[dict[str, str], str]:
    """종목 → 섹터(sector_map.yaml `map:`) 와 분류 체계 이름.

    ET `classify/sectors.py:apply_to_db`:328 와 같은 뜻 — 섹터가 비면 '미분류', 체계는 sectors.yaml
    `meta.taxonomy`(board48).
    """
    p = Path(path) if path else knowledge_path("sector_map.yaml", settings=settings)
    data = _yaml_mapping(p)
    raw = data.get("map") or {}
    if not isinstance(raw, Mapping):
        raise ConfigFileError("sector_map.yaml: map 이 매핑이 아니다")
    out = {
        str(code): str((v or {}).get("sector") or UNMAPPED)
        for code, v in raw.items()  # pyright: ignore[reportUnknownVariableType]
    }
    tax = (load_taxonomy(settings=settings).get("meta") or {}).get("taxonomy") or "board48"
    return out, str(tax)
