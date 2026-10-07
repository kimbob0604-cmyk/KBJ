from collections.abc import Callable
from decimal import Decimal as D
from decimal import localcontext

import pytest

from core.chain import atm_strike, atm_window, by_distance, covers


def grid(lo: str, hi: str, step: str = "2.5") -> list[D]:
    out: list[D] = []
    k = D(lo)
    while k <= D(hi):
        out.append(k)
        k += D(step)
    return out


# 2026-09-28 13:33 실측(probe_results #11·#11a): 선물 약 1098, 전광판 100행
MONTHLY_BOARD = grid("1347.5", "1595.0")  # 최고 행사가부터 100개 — ATM 구간 빠짐
WEEKLY_BOARD = grid("997.5", "1245.0")  # 아래쪽이 잘렸지만 ATM 구간 포함
MASTER_202610 = grid("745.0", "1595.0")  # 마스터 전 행사가 341개


def test_fixture_sizes_match_probe() -> None:
    assert len(MONTHLY_BOARD) == len(WEEKLY_BOARD) == 100
    assert len(MASTER_202610) == 341


# --- atm_strike ---


@pytest.mark.parametrize(
    ("ref", "atm"),
    [
        (1098, "1097.5"),
        (1098.35, "1097.5"),  # float 기준가
        (D("1098.75"), "1097.5"),  # 정확히 가운데 → 낮은 행사가
        (1098.75, "1097.5"),
        (D("1098.76"), "1100.0"),
        (D("1100"), "1100.0"),  # 행사가와 같음
        (D("1"), "745.0"),  # 범위 밖 → 끝
        (D("5000"), "1595.0"),
    ],
)
def test_atm_strike(ref: D | float, atm: str) -> None:
    assert atm_strike(MASTER_202610, ref) == D(atm)


def test_atm_strike_ignores_order_duplicates_and_accepts_iterables() -> None:
    ks = [D("1100"), D("1097.5"), D("1097.50"), D("1102.5"), D("1097.5")]  # 콜·풋 두 행 등
    assert atm_strike(reversed(ks), 1098) == D("1097.5")
    assert atm_strike((k for k in ks), 1101.2) == D("1100")
    assert atm_strike(set(ks), 1101.25) == D("1100")  # 1100 과 1102.5 가운데 → 낮은 쪽


def test_atm_strike_on_truncated_board_is_not_true_atm() -> None:
    # 잘린 월물 전광판만 넘기면 집합 안 최근접(1347.5)이 나온다 — 참 ATM 은 마스터로 고른다
    assert atm_strike(MONTHLY_BOARD, 1098) == D("1347.5")
    assert not covers(MONTHLY_BOARD, 1098)


def test_atm_strike_uneven_spacing() -> None:
    ks = [D("1000"), D("1005"), D("1010"), D("1012.5")]
    assert atm_strike(ks, D("1007.5")) == D("1005")  # 1005·1010 가운데 → 낮은 쪽
    assert atm_strike(ks, D("1011.25")) == D("1010")  # 1010·1012.5 가운데
    assert atm_strike(ks, D("1011.26")) == D("1012.5")


# --- atm_window ---


def test_atm_window_centered() -> None:
    assert atm_window(MASTER_202610, 1098, 0) == [D("1097.5")]
    assert atm_window(MASTER_202610, 1098, 2) == [
        D("1092.5"),
        D("1095.0"),
        D("1097.5"),
        D("1100.0"),
        D("1102.5"),
    ]
    w = atm_window(MASTER_202610, 1098, 20)  # 보강 1: 월물 ATM±20
    assert len(w) == 41 and (w[0], w[-1]) == (D("1047.5"), D("1147.5"))


def test_atm_window_clipped_at_ends() -> None:
    assert atm_window(MASTER_202610, 746, 3) == [D("745.0"), D("747.5"), D("750.0"), D("752.5")]
    assert atm_window(MASTER_202610, 1600, 2) == [D("1590.0"), D("1592.5"), D("1595.0")]
    assert atm_window([D("1100")], 1098, 9) == [D("1100")]
    assert atm_window(WEEKLY_BOARD, 1098, 500) == WEEKLY_BOARD


def test_atm_window_sorted_unique_from_unsorted_input() -> None:
    ks = [D("1105"), D("1095"), D("1100"), D("1100.0"), D("1110"), D("1090")]
    assert atm_window(ks, 1101, 1) == [D("1095"), D("1100"), D("1105")]


# --- covers (전광판 잘림 검사) ---


@pytest.mark.parametrize("n", [0, 1, 9, 20])
def test_covers_truncated_monthly_board(n: int) -> None:
    assert covers(MONTHLY_BOARD, 1098, n) is False


def test_covers_weekly_board() -> None:
    # ATM 1097.5 는 index 40 — 아래 40개·위 59개
    assert covers(WEEKLY_BOARD, 1098) is True
    assert covers(WEEKLY_BOARD, 1098, 9) is True
    assert covers(WEEKLY_BOARD, 1098, 40) is True
    assert covers(WEEKLY_BOARD, 1098, 41) is False


def test_covers_range_edges() -> None:
    assert covers(WEEKLY_BOARD, D("997.5")) is True  # 최저 행사가와 같음
    assert covers(WEEKLY_BOARD, D("997.5"), 1) is False  # 아래 이웃이 없다
    assert covers(WEEKLY_BOARD, D("997.4")) is False  # 범위 밖 — 보수적으로 False
    assert covers(WEEKLY_BOARD, D("1245")) is True
    assert covers(WEEKLY_BOARD, D("1245.1")) is False
    assert covers(MASTER_202610, 1098, 20) is True


# --- by_distance ---


def test_by_distance_order_and_ties() -> None:
    ks = [D("1090"), D("1095"), D("1100"), D("1105"), D("1110")]
    assert by_distance(ks, D("1100")) == [
        D("1100"),
        D("1095"),  # 1095·1105 같은 거리 → 낮은 쪽 먼저
        D("1105"),
        D("1090"),
        D("1110"),
    ]
    assert by_distance(ks, D("1097.5"))[:2] == [D("1095"), D("1100")]


def test_by_distance_first_is_atm_and_is_permutation() -> None:
    out = by_distance(MASTER_202610 + MASTER_202610[:5], 1098.35)
    assert out[0] == atm_strike(MASTER_202610, 1098.35)
    assert sorted(out) == MASTER_202610  # 중복 제거, 빠짐 없음


# --- 거리는 정밀 문맥에서 ---

# 유효숫자 32 — 기본 decimal 문맥(28자리)을 넘는다. 1100 까지 1.2499…9999 < 1097.5 까지 1.2500…0001
HIGH_PREC_REF = D("1098.7500000000000000000000000001")


def test_distance_is_exact_beyond_default_context() -> None:
    # 28자리로 자르면 두 거리가 1.25 로 같아져 낮은 행사가(1097.5)로 잘못 간다
    ks = [D("1097.5"), D("1100"), D("1102.5")]
    assert atm_strike(ks, HIGH_PREC_REF) == D("1100")
    assert atm_window(ks, HIGH_PREC_REF, 0) == [D("1100")]
    assert by_distance(ks, HIGH_PREC_REF) == [D("1100"), D("1097.5"), D("1102.5")]
    assert covers(ks[:2], HIGH_PREC_REF) is True


def test_distance_ignores_narrowed_ambient_context() -> None:
    # 호출 측이 전역 문맥을 좁혀도(3자리) 1.251·1.249 가 1.25 동률로 뭉개지지 않는다
    ks = [D("1097.5"), D("1100")]
    with localcontext() as ctx:
        ctx.prec = 3
        assert atm_strike(ks, D("1098.751")) == D("1100")
        assert atm_strike(ks, D("1098.749")) == D("1097.5")
        assert by_distance(ks, D("1098.751")) == [D("1100"), D("1097.5")]
        assert atm_window(MASTER_202610, D("1098.751"), 1) == [
            D("1097.5"),
            D("1100.0"),
            D("1102.5"),
        ]


def test_rejects_ref_beyond_exact_precision() -> None:
    # 정확히 못 잴 만큼 자릿수가 많은 기준가는 조용히 반올림하지 않고 거부한다
    ref = D("1098." + "1" * 70)
    for f in (atm_strike, by_distance):
        with pytest.raises(ValueError, match="정밀도"):
            f(MASTER_202610, ref)


# --- 입력 검증 ---

Fn = Callable[[list[D]], object]
CALLS: list[Fn] = [
    lambda ks: atm_strike(ks, 1098),
    lambda ks: atm_window(ks, 1098, 1),
    lambda ks: covers(ks, 1098),
    lambda ks: by_distance(ks, 1098),
]


@pytest.mark.parametrize("call", CALLS)
@pytest.mark.parametrize("bad", [[], [D(0)], [D("-2.5")], [D("NaN")], [D("sNaN")], [D("Infinity")]])
def test_rejects_bad_strikes(call: Fn, bad: list[D]) -> None:
    with pytest.raises(ValueError):
        call(bad)


@pytest.mark.parametrize("call", CALLS)
def test_rejects_float_strikes(call: Fn) -> None:
    with pytest.raises(TypeError):
        call([1097.5])  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("ref", [0, 0.0, -1098, float("nan"), float("inf"), D("NaN")])
def test_rejects_bad_ref(ref: D | float) -> None:
    # KIS 는 값 없음을 "0" 으로 준다 — 0 기준가로 최저 행사가를 ATM 삼지 않는다
    for f in (atm_strike, by_distance):
        with pytest.raises(ValueError):
            f(MASTER_202610, ref)
    with pytest.raises(ValueError):
        atm_window(MASTER_202610, ref, 1)
    with pytest.raises(ValueError):
        covers(MASTER_202610, ref)


@pytest.mark.parametrize("n", [-1, True, 1.0])
def test_rejects_bad_n(n: int) -> None:
    with pytest.raises(ValueError, match="n"):
        atm_window(MASTER_202610, 1098, n)
    with pytest.raises(ValueError, match="n"):
        covers(MASTER_202610, 1098, n)
