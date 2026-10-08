# 원본(ET) 시험 본문은 타입 표시 없는 dict 를 그대로 쓴다 — 본문을 고치지 않고 이 파일만 완화한다.
# pyright: reportOptionalSubscript=false
"""
신고가 엔진 검증 — 합성 일봉으로 정의를 직접 확인한다.

실행: python3 -m board.tests.test_newhigh   (또는 python3 board/run.py --test)
실데이터가 아니라 정의 자체를 검증한다. 소스 연결 검증은 run.py --check 다.

KBJ P3 묶음 E1 — ET `board/tests/test_newhigh.py`(35 전부) — 에서 승격했다(import 경로만 바꿨다,
docs/p3_design.md §1.3·§1.11). 대상 모듈 `kbj.engines.board`.

신고가 3축(ADR 0017 — 사용자 요청 2026-10-08): d60(직전 60봉)·w52(직전 252봉) 단정을 d120(직전 120
거래일)·w52(달력 364일) 단정으로 바꿨다 — 시험 수·단정 수는 줄이지 않았다. 여기 합성 일봉은 날짜가
하루씩이고 시장 달력을 넣지 않으므로 거래일 창 = 그 종목 봉 120개, 52주 창 = 364봉이다(52주 창을
채우려고 앞 구간을 365봉 이상으로 늘렸다). 시장 달력·공휴일·거래정지 경계는 test_newhigh_axes.py.
"""

import unittest
from datetime import date, timedelta

from kbj.engines.board import newhigh as nh
from kbj.engines.board.config import BoardConfig

CFG = BoardConfig.load()


def bars(closes, highs=None, vols=None, start=1):
    """오름차순 일봉 생성. 날짜는 2020-01-01 부터 하루씩 (영업일 개념은 인덱스로 대체)."""
    from datetime import date, timedelta

    d0 = date(2020, 1, 1)
    highs = highs or closes
    vols = vols or [1000.0] * len(closes)
    return [
        dict(
            asof=(d0 + timedelta(days=i + start)).isoformat(),
            open=c,
            high=h,
            low=min(c, h) * 0.99,
            close=c,
            volume=v,
        )
        for i, (c, h, v) in enumerate(zip(closes, highs, vols))  # noqa: B905 — 원본 그대로(승격)
    ]


class TestGuard(unittest.TestCase):
    def test_normal_series_is_clean(self):
        rows = bars([100 * (1.05**i) for i in range(30)])
        self.assertEqual(nh.split_guard(rows, 0.31)[0], False)

    def test_split_detected(self):
        # 5:1 액면분할이 수정주가로 반영되지 않은 시계열
        rows = bars([1000] * 10 + [200] * 10)
        s, d, note, floor = nh.split_guard(rows, 0.31)  # noqa: RUF059 — 원본 그대로(승격)
        self.assertTrue(s)
        self.assertIn("-80", note)
        self.assertEqual(floor, 10)  # 분할 다음 날 인덱스

    def test_upper_limit_is_not_a_split(self):
        # 상한가 +29.9% 는 정상. 가드에 걸리면 안 된다.
        rows = bars([100, 129.9, 168.7])
        self.assertFalse(nh.split_guard(rows, 0.31)[0])


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        # 370일: 앞 369일은 100 근방 횡보(170일 전에 최고 120 — 120일 창 밖·52주 창 안), 마지막 날
        # 신고가
        self.flat = [100.0] * 200 + [120.0] + [110.0] * 168
        self.rows = bars(self.flat + [130.0])  # noqa: RUF005 — 원본 그대로(승격)
        self.asof = self.rows[-1]["asof"]

    def test_hits_120d_and_52w(self):
        ev = nh.evaluate(self.rows, self.asof, CFG)
        b = ev["basis"]["high"]
        self.assertTrue(b["hit"]["d120"])  # 직전 120거래일 최고 110 < 130
        self.assertEqual(b["refs"]["d120"], 110.0)
        self.assertTrue(b["hit"]["w52"])  # 직전 52주 최고 120 < 130
        self.assertEqual(b["refs"]["w52"], 120.0)
        self.assertFalse(b["hit"]["hist"])  # hist_ref 없음 -> 판정 안 함
        self.assertEqual(b["label"], "w52")  # 우선순위상 상위가 라벨

    def test_hist_hit_with_scalar(self):
        ev = nh.evaluate(
            self.rows, self.asof, CFG, hist_ref={"high": 125.0, "close": 125.0}, hist_days=3000
        )
        self.assertTrue(ev["basis"]["high"]["hit"]["hist"])
        self.assertEqual(ev["basis"]["high"]["label"], "hist")

    def test_hist_suppressed_when_series_suspect(self):
        # 5:1 분할이 260일차에 있었고 수정주가가 반영되지 않은 시계열.
        # 분할 후 130거래일이 지나 120일 룩백은 분할 뒤 구간으로만 채워진다.
        rows = bars([1000.0] * 260 + [200.0] * 130 + [260.0])
        ev = nh.evaluate(
            rows, rows[-1]["asof"], CFG, hist_ref={"high": 250.0, "close": 250.0}, hist_days=3000
        )
        self.assertTrue(ev["suspect"])
        self.assertIsNone(ev["basis"]["high"]["refs"]["hist"])
        self.assertFalse(ev["basis"]["high"]["hit"]["hist"])
        # 120일 기준 최고가는 분할 전 1000 이 아니라 분할 후 200 이어야 한다
        self.assertEqual(ev["basis"]["high"]["refs"]["d120"], 200.0)
        self.assertTrue(ev["basis"]["high"]["hit"]["d120"])

    def test_pre_split_prices_never_leak_into_lookback(self):
        # 분할 직후 30일차. 120일 창을 채우려면 분할 전 구간을 써야 하므로
        # 120일 라벨 자체를 계산하지 않는다. 1000원을 최고가로 들고 있으면
        # 이 종목은 몇 달간 신고가가 뜨지 않는 거짓 음성이 된다.
        rows = bars([1000.0] * 260 + [200.0] * 30 + [260.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertIsNone(ev["basis"]["high"]["refs"]["d120"])
        self.assertIsNone(ev["basis"]["high"]["refs"]["w52"])
        self.assertEqual(ev["usable_days"], 30)

    def test_gap_sign_and_value(self):
        rows = bars([100.0] * 370 + [90.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertAlmostEqual(ev["basis"]["high"]["gap"]["w52"], 10.0, places=6)
        rows2 = bars([100.0] * 370 + [110.0])
        ev2 = nh.evaluate(rows2, rows2[-1]["asof"], CFG)
        self.assertAlmostEqual(ev2["basis"]["high"]["gap"]["w52"], -10.0, places=6)

    def test_narrow5_negative_when_closing_in(self):
        # 5일 전 갭 20% -> 오늘 갭 5%  => 축소폭 -15%p
        rows = bars([100.0] * 370 + [80.0, 82.0, 85.0, 88.0, 92.0, 95.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertAlmostEqual(ev["basis"]["high"]["gap"]["w52"], 5.0, places=6)
        self.assertAlmostEqual(ev["basis"]["high"]["narrow5"]["w52"], -15.0, places=6)

    def test_volume_multiple_excludes_today(self):
        rows = bars([100.0] * 300 + [101.0], vols=[1000.0] * 300 + [5000.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertAlmostEqual(ev["vol_mult"], 5.0, places=6)

    def test_giveback(self):
        rows = bars([100.0, 102.0], highs=[100.0, 110.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        # (110-102)/(110-100) = 0.8
        self.assertAlmostEqual(ev["giveback"], 0.8, places=3)

    def test_giveback_none_when_no_material(self):
        rows = bars([100.0, 98.0], highs=[100.0, 99.0])
        self.assertIsNone(nh.evaluate(rows, rows[-1]["asof"], CFG)["giveback"])

    def test_short_history_blocks_longer_labels_only(self):
        # 121거래일 — 120일 창은 채워지지만 52주는 못 채운다
        # (+29% 는 가격제한폭 안이라 수정주가 가드에 걸리지 않는다)
        rows = bars([100.0] * 120 + [129.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        b = ev["basis"]["high"]
        self.assertIsNone(b["refs"]["w52"])
        self.assertEqual(b["refs"]["d120"], 100.0)
        self.assertEqual(b["label"], "d120")

    def test_계산하는_창은_설정이_정하는_셋뿐이다(self):
        # 20일을 뺐다(D-071). 60일을 120일로 바꿨다(ADR 0017). 창을 늘렸다 줄였다 할 때 여기가 먼저
        # 걸린다.
        self.assertEqual(nh.lookbacks(CFG), {"w52": ("calendar", 364), "d120": ("trading", 120)})
        self.assertEqual(CFG["newhigh"]["priority"], ["hist", "w52", "d120"])

    def test_all_windows_computed(self):
        rows = bars([100.0] * 370 + [130.0])
        refs = nh.evaluate(rows, rows[-1]["asof"], CFG)["basis"]["high"]["refs"]
        for k in ("d120", "w52"):
            self.assertEqual(refs[k], 100.0, k)
        self.assertNotIn("d20", refs, "20일은 더 이상 계산하지 않는다")
        self.assertNotIn("d60", refs, "60일은 더 이상 계산하지 않는다(ADR 0017)")

    def test_close_and_high_basis_differ(self):
        # 장중 고가로는 갱신했지만 종가로는 못 갱신한 날
        rows = bars([100.0] * 370 + [105.0], highs=[100.0] * 370 + [130.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertTrue(ev["basis"]["high"]["hit"]["w52"])
        self.assertTrue(ev["basis"]["close"]["hit"]["w52"])  # 105 > 100 이라 종가도 갱신
        rows2 = bars([100.0] * 370 + [99.0], highs=[100.0] * 370 + [130.0])
        ev2 = nh.evaluate(rows2, rows2[-1]["asof"], CFG)
        self.assertTrue(ev2["basis"]["high"]["hit"]["w52"])
        self.assertFalse(ev2["basis"]["close"]["hit"]["w52"])

    def test_resistance_uses_bar_range_not_close(self):
        """고가 기준에서 저항두께가 0 으로 깔리던 버그.

        구간이 [당일 고가, 기간 최고가]인데 종가로 판정하면 종가가 체계적으로
        고가보다 아래라 거의 안 걸린다. 2026-08-27 실행에서 근접 종목 대부분이
        '얇음 0.0' 으로 나왔다.
        """
        d0 = date(2020, 1, 1)
        rows = [
            dict(
                asof=(d0 + timedelta(days=i + 1)).isoformat(),
                open=97.0,
                high=100.0,
                low=96.0,
                close=97.0,
                volume=1000.0,
            )
            for i in range(300)
        ]
        rows.append(
            dict(
                asof=(d0 + timedelta(days=301)).isoformat(),
                open=98.0,
                high=98.5,
                low=97.5,
                close=98.0,
                volume=1000.0,
            )
        )
        b = nh.evaluate(rows, rows[-1]["asof"], CFG)["basis"]["high"]
        # 구간 [98.5, 100] · 앞 120봉의 범위 96~100 이 전부 겹친다
        self.assertEqual(b["resistance"]["d120"], 120.0)
        self.assertEqual(b["resistance_label"]["d120"], "두꺼움")

    def test_resistance_zero_when_band_is_empty(self):
        """구간에 봉이 하나도 안 겹치면 0 이 맞다. 그건 진짜 얇은 것이다."""
        d0 = date(2020, 1, 1)
        rows = [
            dict(
                asof=(d0 + timedelta(days=i + 1)).isoformat(),
                open=50.0,
                high=52.0,
                low=48.0,
                close=50.0,
                volume=1000.0,
            )
            for i in range(120)
        ]
        # 직전 120봉은 48~52 인데 오늘 고가는 60, 기준 최고가는 52 -> 이미 돌파
        rows.append(
            dict(
                asof=(d0 + timedelta(days=121)).isoformat(),
                open=59.0,
                high=60.0,
                low=58.0,
                close=59.0,
                volume=1000.0,
            )
        )
        b = nh.evaluate(rows, rows[-1]["asof"], CFG)["basis"]["high"]
        self.assertTrue(b["hit"]["d120"])
        self.assertIsNone(b["resistance"]["d120"])  # 돌파했으면 구간이 없다

    def test_resistance_thickness(self):
        # 현재가 95, 기준최고가 100. 그 구간(95~100)에 20일치 거래량이 쌓여 있다.
        closes = [90.0] * 330 + [98.0] * 40 + [95.0]
        vols = [1000.0] * 330 + [2000.0] * 40 + [1000.0]
        rows = bars(closes, vols=vols)
        # 직전 52주 최고가는 98
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        r = ev["basis"]["high"]["resistance"]["w52"]
        self.assertIsNotNone(r)
        self.assertGreater(r, 0)


class TestContinuity(unittest.TestCase):
    def test_new_when_no_prev(self):
        self.assertEqual(nh.continuity(1, None), "신규")

    def test_continue_when_prev_same(self):
        self.assertEqual(nh.continuity(1, 1), "이어감")

    def test_continue_when_prev_higher_rank(self):
        # 어제 역사적(0), 오늘 52주(1) -> 이어감
        self.assertEqual(nh.continuity(1, 0), "이어감")

    def test_new_when_upgrading(self):
        # 어제 120일(2), 오늘 52주(1) -> 신규
        self.assertEqual(nh.continuity(1, 2), "신규")


class TestProximity(unittest.TestCase):
    def test_picks_highest_rank_within_limit(self):
        rows = bars([100.0] * 300 + [97.0])
        ev = nh.evaluate(
            rows, rows[-1]["asof"], CFG, hist_ref={"high": 100.0, "close": 100.0}, hist_days=3000
        )
        self.assertEqual(nh.proximity_kind(ev, "high", CFG), "hist")

    def test_none_when_already_hit(self):
        rows = bars([100.0] * 300 + [130.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertIsNone(nh.proximity_kind(ev, "high", CFG))

    def test_none_when_too_far(self):
        rows = bars([100.0] * 300 + [80.0])
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG)
        self.assertIsNone(nh.proximity_kind(ev, "high", CFG))


class TestRollAlltime(unittest.TestCase):
    def test_incremental_update(self):
        a = nh.roll_alltime(None, bars([100.0, 120.0, 110.0]), CFG)
        self.assertEqual(a["hi"], 120.0)
        self.assertEqual(a["n_days"], 3)
        b = nh.roll_alltime(a, bars([115.0, 130.0], start=10), CFG)
        self.assertEqual(b["hi"], 130.0)
        self.assertEqual(b["n_days"], 5)

    def test_today_high_does_not_block_its_own_hist_label(self):
        # 수집은 당일 봉까지 받는다. hi 만 들고 있으면 당일 고가가 사상 최고가가 된
        # 순간 '당일 갱신' 판정이 영원히 거짓이 된다. prev_hi 가 그걸 막는다.
        # 상장 후 71영업일, 마지막 날 +29%(가격제한폭 안)로 사상 최고가 경신
        rows = bars([100.0] * 70 + [129.0])
        a = nh.roll_alltime(None, rows, CFG)
        self.assertEqual(a["hi"], 129.0)
        self.assertEqual(a["prev_hi"], 100.0)
        ref, why = nh.hist_ref_for(a, rows[-1]["asof"])
        self.assertEqual(why, "")
        self.assertEqual(ref["high"], 100.0)
        ev = nh.evaluate(rows, rows[-1]["asof"], CFG, hist_ref=ref, hist_days=a["n_days"])
        self.assertTrue(ev["basis"]["high"]["hit"]["hist"])

    def test_prev_hi_survives_a_rerun(self):
        rows = bars([100.0] * 10 + [129.0])
        a = nh.roll_alltime(None, rows, CFG)
        b = nh.roll_alltime(a, rows, CFG)  # 같은 날 두 번 돌린 경우
        self.assertEqual(b["prev_hi"], 100.0)
        self.assertEqual(b["hi"], 129.0)

    def test_hist_ref_refuses_past_asof(self):
        rows = bars([100.0, 110.0, 120.0])
        a = nh.roll_alltime(None, rows, CFG)
        ref, why = nh.hist_ref_for(a, rows[0]["asof"])
        self.assertIsNone(ref)
        self.assertIn("복원 불가", why)

    def test_reingesting_same_days_does_not_double_count(self):
        rows = bars([100.0, 120.0, 110.0])
        a = nh.roll_alltime(None, rows, CFG)
        b = nh.roll_alltime(a, rows, CFG)  # 같은 구간 재적재
        self.assertEqual(b["n_days"], 3)

    def test_keeps_old_high(self):
        a = nh.roll_alltime(None, bars([500.0]), CFG)
        b = nh.roll_alltime(a, bars([100.0, 110.0], start=10), CFG)
        self.assertEqual(b["hi"], 500.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class GivebackMissingCloseTest(unittest.TestCase):
    """종가를 못 받은 날의 재료 반납.

    옛 판은 today['close'] 를 확인하지 않고 뺄셈에 넣어 TypeError 로 그날
    평가가 통째로 죽었다. 그리고 chg 가 None 이면 0 으로 대신해 give_pp 에
    고가 등락률을 그대로 실었다 — '고가 대비 종가 괴리 %p' 자리에 다른 수치가
    사실처럼 찍힌다.
    """

    def _rows(self, close):
        base = [
            dict(
                asof=f"2026-01-{d:02d}",
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=1000.0,
            )
            for d in range(1, 25)
        ]
        base.append(
            dict(asof="2026-01-25", open=100.0, high=112.0, low=99.0, close=close, volume=1000.0)
        )
        return base

    def test_missing_close_does_not_crash(self):
        ev = nh.evaluate(self._rows(None), "2026-01-25", CFG)
        self.assertIsNone(ev["giveback"])
        self.assertIsNone(ev["giveback_pp"])

    def test_missing_close_still_reports_high_chg(self):
        # 고가 등락률은 종가 없이도 계산된다. 그건 남긴다.
        ev = nh.evaluate(self._rows(None), "2026-01-25", CFG)
        self.assertIsNotNone(ev["high_chg_pct"])

    def test_normal_close_gives_pp_as_the_difference(self):
        ev = nh.evaluate(self._rows(101.0), "2026-01-25", CFG)
        self.assertAlmostEqual(ev["giveback_pp"], ev["high_chg_pct"] - ev["chg_pct"], places=2)
        self.assertAlmostEqual(ev["giveback"], (112.0 - 101.0) / 12.0, places=3)
