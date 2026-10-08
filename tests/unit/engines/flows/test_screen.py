"""스크리닝 6기준 — 정렬·필터·관리종목 제외·우선주 별도·급증 10일 미만(docs/metrics.md §3·§5)."""

from __future__ import annotations

import pytest

from kbj.config.markets import load_markets
from kbj.core.quality import Quality
from kbj.engines.flows.checks import apply_checks
from kbj.engines.flows.ledger import build_ledger
from kbj.engines.flows.screen import ScreenTuning, screen, spike_multiple
from tests.fixtures.synthetic.ledger_gen import generate, ledger_inputs
from tests.unit.engines.flows.flow_rows import days, ledger_of, lrow

D = days(25)
LAST = D[-1]
EOK = 100_000_000


def _flat(code: str, *, turnover: int = 500 * EOK, **kw) -> list:
    """25일 동안 같은 값 — 마지막 날만 kw 로 바꾼다."""
    rows = [lrow(code, d, turnover=turnover) for d in D[:-1]]
    rows.append(lrow(code, LAST, turnover=turnover, **kw))
    return rows


def _run(rows, **kw):
    led, _ = apply_checks(ledger_of(rows, D))
    kw.setdefault("min_avg_turnover", 0)
    return screen(led, **kw)


def test_거래대금_상위는_기간_합_내림차순_같으면_코드순() -> None:
    rows = _flat("A00002", turnover=100 * EOK) + _flat("A00001", turnover=300 * EOK)
    rows += _flat("A00003", turnover=100 * EOK)
    res = _run(rows, mode="value", period=5)
    assert [r.code for r in res.rows] == ["A00001", "A00002", "A00003"]
    assert [r.rank for r in res.rows] == [1, 2, 3]
    assert res.rows[0].turnover_sum == 5 * 300 * EOK
    assert res.rows[0].turnover_avg == 300 * EOK
    assert res.rows[0].turnover_rate_pct == pytest.approx(300 * EOK / 100_000_000_000 * 100)


def test_외국인_기관_정렬() -> None:
    rows = _flat("A00001", foreign=5, inst=-5, indiv=0) + _flat("A00002", foreign=9, inst=1)
    f = _run(rows, mode="foreign", period=1)
    i = _run(rows, mode="inst", period=1)
    assert [r.code for r in f.rows] == ["A00002", "A00001"]
    assert [r.code for r in i.rows] == ["A00002", "A00001"]


def test_동반은_둘_다_양수만() -> None:
    rows = (
        _flat("A00001", foreign=5, inst=-1)
        + _flat("A00002", foreign=1, inst=1)
        + _flat("A00003", foreign=3, inst=4)
    )
    res = _run(rows, mode="both", period=1)
    assert [r.code for r in res.rows] == ["A00003", "A00002"]


def test_연속은_최대_연속일_같으면_순매수_합() -> None:
    def seq(code: str, f: list[int], i: list[int]) -> list:
        base = [lrow(code, d, foreign=-1, inst=-1) for d in D[: -len(f)]]
        tail = [lrow(code, D[-len(f) + k], foreign=f[k], inst=i[k]) for k in range(len(f))]
        return base + tail

    rows = (
        seq("A00001", [1, 1, 1], [-1, -1, -1])  # 외국인 3일
        + seq("A00002", [5, 5, 5], [-1, -1, -1])  # 외국인 3일, 합이 더 큼
        + seq("A00003", [-1, 1, 1, 1, 1], [1, 1, 1, 1, 1])  # 기관 5일
        + seq("A00004", [1, 1], [-1, -1])  # 2일 — 기준 미달
    )
    res = _run(rows, mode="streak", period=5)
    assert [(r.code, r.streak_foreign, r.streak_inst) for r in res.rows] == [
        ("A00003", 4, 5),
        ("A00002", 3, 0),
        ("A00001", 3, 0),
    ]


def test_급증은_배수_내림차순_하한_이상() -> None:
    rows = _flat("A00001", turnover=100 * EOK)
    rows[-1] = lrow("A00001", LAST, turnover=300 * EOK)
    rows += _flat("A00002", turnover=100 * EOK)
    rows[-1] = lrow("A00002", LAST, turnover=140 * EOK)  # 1.4배 — 하한 미달
    res = _run(rows, mode="spike", period=1)
    assert [r.code for r in res.rows] == ["A00001"]
    assert res.rows[0].spike_mult == pytest.approx(3.0)


def test_급증_직전_거래일_10일_미만이면_계산하지_않는다() -> None:
    rows = [lrow("A00001", d, turnover=100 * EOK) for d in D[-10:]]  # 직전 9일
    rows[-1] = lrow("A00001", LAST, turnover=900 * EOK)
    led = ledger_of(rows, D)
    assert spike_multiple(led, "A00001", LAST, min_prior_days=10) is None
    rows2 = [lrow("A00001", d, turnover=100 * EOK) for d in D[-11:]]
    rows2[-1] = lrow("A00001", LAST, turnover=900 * EOK)
    assert spike_multiple(ledger_of(rows2, D), "A00001", LAST, min_prior_days=10) == 9.0


def test_급증_분모는_정지일을_뺀다() -> None:
    rows = [lrow("A00001", d, turnover=100 * EOK) for d in D[:-1]]
    rows[-2] = lrow("A00001", D[-3], turnover=0, traded=False, flags=("halted",))
    rows.append(lrow("A00001", LAST, turnover=200 * EOK))
    assert spike_multiple(ledger_of(rows, D), "A00001", LAST, min_prior_days=10) == 2.0


def test_관리종목_정지_정리매매는_기본_제외_상태_모름은_센다() -> None:
    rows = (
        _flat("A00001", flags=("managed",))
        + _flat("A00002", flags=("halted",), traded=False, turnover=0)
        + _flat("A00003", flags=("liquidation",))
        + _flat("A00004", flags=None)
        + _flat("A00005")
    )
    res = _run(rows, mode="value", period=1)
    assert {r.code for r in res.rows} == {"A00004", "A00005"}
    assert res.n_total == 5
    assert res.n_excluded["flagged"] == 3
    assert res.n_excluded["status_unknown"] == 1
    assert any("모르는 종목 1개" in n for n in res.notes)
    with_flagged = _run(rows, mode="value", period=1, include_flagged=True)
    assert {r.code for r in with_flagged.rows} == {"A00001", "A00003", "A00004", "A00005"}
    # A00002 는 오늘 거래가 없어 1일 일평균이 없다 → 하한 미달


def test_우선주는_보통주와_따로() -> None:
    rows = _flat("A00001") + [lrow("A00009", d, kind="pref") for d in D]
    rows += [lrow("A00010", d, kind="etf") for d in D]
    common = _run(rows, mode="value", period=1)
    pref = _run(rows, mode="value", period=1, share_class="pref")
    assert [r.code for r in common.rows] == ["A00001"]
    assert [r.code for r in pref.rows] == ["A00009"]


def test_시장_필터와_코넥스_제외() -> None:
    rows = _flat("A00001") + [lrow("A00002", d, market="KOSDAQ") for d in D]
    rows += [lrow("A00003", d, market="KONEX") for d in D]
    assert {r.code for r in _run(rows, mode="value").rows} == {"A00001", "A00002"}
    assert [r.code for r in _run(rows, mode="value", market="KOSDAQ").rows] == ["A00002"]


def test_일평균_하한() -> None:
    rows = _flat("A00001", turnover=100 * EOK) + _flat("A00002", turnover=400 * EOK)
    res = _run(rows, mode="value", min_avg_turnover=300 * EOK)
    assert [r.code for r in res.rows] == ["A00002"]
    assert res.n_excluded["below_min"] == 1


def test_invalid_행은_빼고_센다() -> None:
    rows = _flat("A00001", turnover=100 * EOK)
    rows[-2] = lrow("A00001", D[-2], turnover=999 * EOK, quality=Quality.INVALID)
    res = _run(rows, mode="value", period=5)
    assert res.rows[0].turnover_sum == 4 * 100 * EOK
    assert res.n_excluded["invalid"] == 1
    assert any("invalid 1행" in n for n in res.notes)


def test_신고가_라벨과_섹터를_싣는다() -> None:
    res = _run(
        _flat("A00001"), mode="value", newhigh={"A00001": "w52"}, sectors={"A00001": "반도체"}
    )
    assert (res.rows[0].newhigh_label, res.rows[0].sector) == ("w52", "반도체")


def test_limit_과_인자_검증() -> None:
    rows = _flat("A00001") + _flat("A00002")
    assert len(_run(rows, mode="value", limit=1).rows) == 1
    for bad in ({"mode": "x"}, {"mode": "value", "period": 3}, {"mode": "value", "limit": 0}):
        with pytest.raises(ValueError):
            _run(rows, **bad)


def test_합성_원장_전체() -> None:
    m = generate(5)
    led, _ = apply_checks(build_ledger(**ledger_inputs(m)))  # type: ignore[arg-type]
    tune = ScreenTuning.from_config(load_markets().screen)
    res = screen(led, mode="value", period=20, min_avg_turnover=0, tuning=tune)
    codes = {r.code for r in res.rows}
    assert "Q00016" not in codes  # 관리종목
    assert "Q00002" not in codes  # 오늘 거래정지
    assert "Q00012" in codes and res.n_excluded["status_unknown"] == 1
    assert not codes & {"Q00003", "Q00007", "Q00009", "Q00011", "Q00015", "Q00017"}
    assert res.as_of == m.last_day and res.quality is Quality.OK
    assert res.source == "KRX+KIS"
    spike = screen(led, mode="spike", period=1, min_avg_turnover=0, tuning=tune)
    assert spike.rows[0].code == "Q00000" and spike.rows[0].spike_mult == pytest.approx(3, rel=0.01)
    by = {r.code: r for r in res.rows}
    assert by["Q00006"].spike_mult is None  # 신규 상장 — 직전 거래일 2일
    streak = screen(led, mode="streak", period=5, min_avg_turnover=0, tuning=tune)
    st = {r.code: (r.streak_foreign, r.streak_inst) for r in streak.rows}
    assert st["Q00000"][0] >= 5 and st["Q00010"][1] >= 4
    assert "Q00014" not in st or st["Q00014"][0] < 3  # 0원에서 끊긴다


def test_검산_미적용_원장이면_알린다() -> None:
    res = screen(ledger_of(_flat("A00001"), D), mode="value", min_avg_turnover=0)
    assert "검산 ①② 를 적용하지 않은 원장" in res.notes


def test_빈_원장() -> None:
    from kbj.engines.flows.ledger import Ledger

    res = screen(Ledger(rows={}, trading_days=()), mode="value", min_avg_turnover=0)
    assert res.rows == () and res.as_of is None and res.n_total == 0
