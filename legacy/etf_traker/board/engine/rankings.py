"""KBJ P3 묶음 E1 — 정본은 `kbj.engines.board.rankings`. 여기는 다시 내보내기 + state 파일 읽기만 한다.

docs/p3_design.md §1.3: kbj 의 `build_rankings(universe, prev_rankings, cfg, taxonomy, …)` 는 파일을
모른다. legacy 의 `build(asof, cfg, log)` 는 예전처럼 state/<날짜>/*.json 을 읽어 그 함수에 넘긴다.
분류 사전은 `config/knowledge/sectors.yaml`(레포 루트 — 묶음 E1 이 옮겼다).
"""
from kbj.core.time import now_kst
from kbj.engines.board.config import load_taxonomy as _load_taxonomy
from kbj.engines.board.rankings import *  # noqa: F401,F403
from kbj.engines.board.rankings import __all__  # noqa: F401
from kbj.engines.board.rankings import build_rankings
from kbj.engines.board.rankings import format_cell as _fmt  # noqa: F401 — 예전 이름(합성 견본 도구)


def load_taxonomy():
    return _load_taxonomy()


def build(asof, cfg, log=print):
    from .build import read
    uni = read(asof, 'universe.json') or {}
    nhj = read(asof, 'newhigh.json') or {}
    prev_asof = nhj.get('prev_asof')
    return build_rankings(
        uni, read(prev_asof, 'rankings.json') if prev_asof else None, cfg, load_taxonomy(),
        asof=asof, generated_at=now_kst().isoformat(timespec='seconds'),
        newhigh=nhj, sectors=read(asof, 'sectors.json') or {},
        market=read(asof, 'market.json'), log=log)
