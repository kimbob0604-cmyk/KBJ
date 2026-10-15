"""페이지 9(테마·밸류체인) 합성 세계 — API 시험·웹 픽스처·데모가 같이 쓴다(ADR 0039).

- 테마 마스터: `tests/fixtures/synthetic/theme_master.py`(고정 시드 합성 — 실제 stock-dashboard 내용
  아님)를 엔진(`parse_master`·`parse_classes`)으로 검증해 저장소 두 개(로그인 `theme`·공개
  `theme_pub`)에 싣는다 — 작업 처리기와 같은 경로(받기만 뺀다).
- 보드 종목 하루: 신고가 골든 입력(합성 300종목 — `tests/golden/board/expected/day_29.json.gz`)의
  universe 행을 보드 서비스와 같은 규칙(억원 → 원)으로 `StockDay` 로 바꿔 보드 마지막 날에
  끼운다(산출물·라벨은 그대로 — 메모리 저장소 표에 직접).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Final

from kbj.config.theme import load_theme
from kbj.core.quality import Quality
from kbj.core.rows import StockDay
from kbj.engines.theme.classes import parse_classes
from kbj.engines.theme.master import parse_master
from kbj.store.repos import MemoryRepos
from tests.fixtures.synthetic.theme_master import build_files
from tests.unit.api.api_world import golden_payloads

__all__ = ["EOK", "board_stock_days", "golden_names", "seed_board", "seed_themes"]

EOK: Final = 100_000_000
_COLS: Final = frozenset({
    "code", "name", "close", "chg_pct", "turnover", "turnover_is_estimate", "mktcap", "label",
    "near_kind", "near_gap", "status", "suspect", "sector", "theme", "ret_5d", "ret_21d",
    "vol_mult",
})  # fmt: skip


def _won(v: object) -> int | None:
    return None if not isinstance(v, int | float) else round(float(v) * EOK)


def golden_names() -> dict[str, str]:
    return {s["code"]: s["name"] for s in golden_payloads()["universe"]["stocks"]}


def board_stock_days(day: date) -> list[StockDay]:
    out: list[StockDay] = []
    for r in golden_payloads()["universe"]["stocks"]:
        extra: dict[str, Any] = {k: v for k, v in r.items() if k not in _COLS}
        out.append(
            StockDay(
                code=r["code"],
                date=day,
                name=r.get("name"),
                close=r.get("close"),
                chg_pct=r.get("chg_pct"),
                turnover=_won(r.get("turnover")),
                turnover_is_estimate=bool(r.get("turnover_is_estimate")),
                mktcap=_won(r.get("mktcap")),
                label=r.get("label"),
                near_kind=r.get("near_kind"),
                near_gap=r.get("near_gap"),
                status=r.get("status"),
                suspect=bool(r.get("suspect")),
                sector=r.get("sector"),
                theme=r.get("theme"),
                ret_5d=r.get("ret_5d"),
                ret_21d=r.get("ret_21d"),
                vol_mult=r.get("vol_mult"),
                source="kis",
                quality=Quality.ESTIMATED,
                extra=extra,
            )
        )
    return out


def seed_board(repos: MemoryRepos, day: date) -> int:
    """보드 마지막 날에 종목 하루를 끼운다(산출물·라벨은 그대로)."""
    rows = board_stock_days(day)
    for s in rows:
        repos.board.stock_day_rows[(s.code, day)] = s
    return len(rows)


def seed_themes(
    repos: MemoryRepos,
    *,
    now: datetime,
    files: dict[str, bytes] | None = None,
    classes: bool = True,
    master: bool = True,
) -> None:
    """합성 테마 마스터를 두 저장소에 싣는다(작업과 같은 검증 — 기본 설정 하한). 판 built_at = now
    − 2일."""
    cfg = load_theme().master
    # 판은 '지금' 이틀 전 일요일 갱신처럼(합성 세계 시계 기준 — 화면 품질 ok)
    got = (
        files
        if files is not None
        else build_files(anchor_names=golden_names(), built_at=now - timedelta(days=2))
    )
    if master:
        m = parse_master(got, cfg=cfg)
        repos.theme.replace_master(m, loaded_by="theme.master_sync", received_at=now)
    if classes:
        c = parse_classes(got, cfg=cfg)
        repos.theme_pub.replace_themes(c.themes, loaded_by="theme.classes_sync", received_at=now)
