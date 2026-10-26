"""주식 분봉 받기(kbj.services.collectors.stock_minutes) — 가짜 KIS·메모리 저장소."""

from __future__ import annotations

import io
from datetime import date, time

from kbj.core.time import KST
from kbj.data.private.kis.rest import KisResponse
from kbj.services.collectors.stock_minutes import collect, fetch_day, probe
from kbj.store.repos.stock_minute import MemoryStockMinuteRepo
from tests.unit.services.cis30_fake import FakeKis

D = date(2026, 10, 23)


def test_하루를_과거로_이어_받는다() -> None:
    kis = FakeKis()
    res = fetch_day(kis, "000100", D)
    assert res.error is None and 4 <= res.calls <= 6
    assert [c[2] for c in kis.calls][:2] == ["153000", "132100"]  # 120봉 뒤 가장 이른 봉 − 1분
    times = [b.ts.astimezone(KST).time() for b in res.bars]
    assert times[0] == time(9, 0) and times[-1] == time(15, 30) and len(res.bars) == 78
    assert all(b.trade_date == D for b in res.bars)  # 앞 거래일 봉은 버린다


def test_묶음_받기·오류는_세고_넘어간다() -> None:
    kis = FakeKis(fail={("000200", D)})
    repo = MemoryStockMinuteRepo()
    out = io.StringIO()
    got = collect(kis, repo, [("000100", D), ("000200", D)], out=out, every=1, sleep=lambda s: None)
    assert got.days == 1 and got.errors == {"HTTP 200 OPSQ0001": 1}
    assert repo.days(D, D) == {("000100", D)} and "2/2" in out.getvalue()


def test_5xx_는_잠깐_쉬고_다시() -> None:
    class Flaky(FakeKis):
        n: int = 0

        def get(self, path, tr_id, params, tr_cont="", **kw):  # type: ignore[override]
            self.n += 1
            if self.n == 1:
                return KisResponse(503, {}, 1.0)
            return super().get(path, tr_id, params, tr_cont, **kw)

    waits: list[float] = []
    got = collect(Flaky(), MemoryStockMinuteRepo(), [("000100", D)], sleep=waits.append)
    assert got.days == 1 and waits == [5.0]


def test_probe_는_값을_찍지_않는다() -> None:
    out = io.StringIO()
    probe(FakeKis(fail={("000100", date(2026, 1, 2))}), "000100", [D, date(2026, 1, 2)], out)
    text = out.getvalue()
    assert "칸 이름: acml_tr_pbmn,cntg_vol" in text and "그날 120봉 13:22:00~15:30:00" in text
    assert "실패 HTTP 200 OPSQ0001" in text and "10000" not in text
