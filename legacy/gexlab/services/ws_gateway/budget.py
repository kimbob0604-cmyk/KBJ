"""KIS 웹소켓 41건 예산 (PLAN §4.3, `config/ws_budget.yaml`).

옵션 구독 수는 하드코딩하지 않고 식으로 도출한다: ATM±r 행사가 × 콜·풋 = (2r+1)×2 가
`limit − futures − notices − min_spare` 를 넘지 않는 가장 큰 r. 남는 건 여유로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

Session = Literal["day", "night"]
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "ws_budget.yaml"


class SessionBudgetConfig(BaseModel):
    futures: int = Field(ge=0)
    notices: int = Field(ge=0)
    min_spare: int = Field(ge=0)


class BudgetConfig(BaseModel):
    limit: int = Field(gt=0)
    day: SessionBudgetConfig
    night: SessionBudgetConfig


@dataclass(frozen=True)
class Budget:
    session: Session
    limit: int
    futures: int
    notices: int
    atm_range: int
    options: int
    spare: int

    @property
    def total(self) -> int:
        return self.futures + self.notices + self.options + self.spare


def derive(cfg: BudgetConfig, session: Session) -> Budget:
    s = cfg.day if session == "day" else cfg.night
    avail = cfg.limit - s.futures - s.notices - s.min_spare
    if avail < 2:  # ATM 한 행사가(콜·풋)도 못 넣는다
        raise ValueError(f"{session}: 옵션에 쓸 구독이 {avail}건뿐이다")
    atm_range = (avail // 2 - 1) // 2
    options = (2 * atm_range + 1) * 2
    return Budget(
        session=session,
        limit=cfg.limit,
        futures=s.futures,
        notices=s.notices,
        atm_range=atm_range,
        options=options,
        spare=cfg.limit - s.futures - s.notices - options,
    )


def load(path: Path = DEFAULT_PATH) -> BudgetConfig:
    return BudgetConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
