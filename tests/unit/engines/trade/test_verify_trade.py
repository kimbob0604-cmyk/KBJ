"""매핑표 검증 CLI(묶음 MAP — probe_results #56): `--map-only`(파일·종료 코드)와 `--watchlist`
(주입한 DB 보기 — 합성 세계, 실데이터 아님). 출력은 건수만, 관심종목 코드는 내지 않는다."""

from __future__ import annotations

import io
import json
from pathlib import Path

from kbj.services.engine.verify_trade import DbView, main, verify_watchlist
from tests.fixtures.synthetic.p6_world import build_world
from tests.unit.api.web_fixtures_p6.map import map_api_world

ROOT = Path(__file__).resolve().parents[4]


def _run(*argv: str, db: object = None) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = main(list(argv), out=out, err=err, db=db)  # pyright: ignore[reportArgumentType]
    return code, out.getvalue(), err.getvalue()


def test_map_only_공개_매핑표_통과() -> None:
    code, out, _ = _run("--map-only", str(ROOT / "config/knowledge/trade_map.yaml"), "--json")
    s = json.loads(out)
    assert code == 0 and s["problems"] == 0 and s["stocks"] >= 60


def test_map_only_문제·형식·없는_파일(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "version: 1\nstocks:\n  - {code: '000001', name: x, reviewed: false,"
        " items: [{hs: '9901', flow: exp, weight: 0.1}],"
        " evidence: [{kind: synthetic, note: x}]}\n",
        encoding="utf-8",
    )
    code, out, _ = _run("--map-only", str(bad))
    assert code == 1 and "chapter_missing" in out
    broken = tmp_path / "broken.yaml"
    broken.write_text("version: 1\nstocks:\n  - {code: '1', name: x}\n", encoding="utf-8")
    assert _run("--map-only", str(broken))[0] == 1
    assert _run("--map-only", str(tmp_path / "none.yaml"))[0] == 2


def _view() -> DbView:
    aw = map_api_world()
    repos = aw.repos
    w = build_world()
    return DbView(
        watch=w.watchlist,
        smap=repos.trade.stock_map(),
        listed=set(repos.filings.corp_codes) | {c for c, _ in w.stocks},
        hs_codes={d.hs for d in repos.trade.hs_dim()},
        exports=repos.trade.stock_export(),
    )


def test_watchlist_건수만() -> None:
    v = _view()
    s = verify_watchlist(v)
    w = build_world()
    mapped = set(w.watchlist) & {r.code for r in v.smap}
    assert s["watchlist"] == 50 and s["mapped"] == len(mapped)
    assert s["unmapped"] == 50 - len(mapped)
    assert sum(s["status"].values()) == len(mapped)
    text = json.dumps(s)
    assert not any(c in text for c in w.watchlist)


def test_watchlist_CLI_종료_코드() -> None:
    v = _view()
    code, out, _ = _run("--watchlist", "main", "--json", db=lambda _n: v)
    assert code in (0, 1)
    s = json.loads(out)
    assert code == (0 if s["code_missing"] == 0 and s["hs_missing"] == 0 else 1)
    empty = DbView(watch=[], smap=v.smap, listed=v.listed, hs_codes=v.hs_codes, exports=[])
    assert _run("--watchlist", "main", db=lambda _n: empty)[0] == 1

    def boom(_n: str) -> DbView:
        raise RuntimeError("postgresql://secret")

    code, _, err = _run("--watchlist", "main", db=boom)
    assert code == 2 and "secret" not in err
