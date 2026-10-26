"""cis 30분봉 CLI(kbj.services.engine.cis30) — 합성 일봉 + 가짜 KIS 분봉 + 메모리 저장소."""

from __future__ import annotations

import io
from datetime import date

from kbj.config.screen import CisCfg, load_screen
from kbj.services.engine.backtest_cis import Loaded
from kbj.services.engine.cis30 import SWEEP30, Env, _signals, main
from kbj.store.repos.stock_minute import MemoryStockMinuteRepo
from tests.unit.services.cis30_fake import FakeKis
from tests.unit.services.test_backtest_cis import _world


def _load(n: int, end: date | None, cfg: CisCfg) -> Loaded:
    w = _world()
    return Loaded(w.series, w.meta, w.index_bars, w.days[-n:])


def _env() -> tuple[Env, MemoryStockMinuteRepo, FakeKis]:
    conf = load_screen()
    got = _load(400, None, conf.cis)
    days = sorted(got.days)
    sig = _signals(got, conf.cis, conf.cis30, days[25])
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    kis = FakeKis(boom={(s.code, nxt[d]) for d, v in sig.items() if d in nxt for s in v})
    repo = MemoryStockMinuteRepo()
    return Env(load=_load, minutes=lambda: repo, kis=lambda: kis), repo, kis


def _cli(env: Env, *argv: str) -> tuple[int, str]:
    out = io.StringIO()
    return main(list(argv), out=out, env=env), out.getvalue()


def test_받기_계획·이어받기·백테스트() -> None:
    env, repo, kis = _env()
    rc, text = _cli(env, "fetch", "--dry-run")
    assert rc == 0 and "이번에 받을 것" in text and not kis.calls
    rc, text = _cli(env, "fetch", "--limit", "40")
    assert rc == 0 and "끝 — 받음 40일" in text and kis.closed
    rc, text = _cli(env, "fetch", "--dry-run")
    assert "이미 있음 40" in text  # 다시 돌리면 남은 것만
    _cli(env, "fetch")
    rc, text = _cli(env, "run", "--trades", "3")
    assert rc == 0 and "cis 30분봉 백테스트" in text and "총수익" in text and "NaN" not in text
    assert "30분봉 돌파 신호" in text and repo.rows


def test_비교_표() -> None:
    env, _, _ = _env()
    _cli(env, "fetch")
    rc, text = _cli(env, "run", "--sweep")
    assert rc == 0 and "연도별" in text
    for name, _ in SWEEP30:
        assert name in text, name


def test_분봉이_없으면_오류·probe() -> None:
    env, _, kis = _env()
    rc, text = _cli(env, "run")
    assert rc == 2 and "먼저" in text
    rc, text = _cli(env, "probe", "--code", "100100")
    assert rc == 0 and "칸 이름" in text and len(kis.calls) == 7
