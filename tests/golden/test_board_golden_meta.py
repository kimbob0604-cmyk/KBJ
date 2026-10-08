"""골든 자체를 지킨다 — legacy 커밋 고정·파일 체크섬 고정·비교기가 실제로 다름을 잡는지.

docs/p3_design.md §8.1: 골든 입력은 shim(D-P3-10 ④) **전** legacy 엔진으로 캡처했다. shim 뒤에
`make_golden.py` 를 다시 돌리면 legacy 가 kbj 를 부르므로 자기 자신과 비교하게 된다 — 체크섬이
바뀌면 이 시험이 실패한다.

2026-10-08 신고가 3축 정의 변경(사용자 요청, ADR 0017)으로 기대 산출을 kbj 엔진으로 **한 번** 다시
캡처했다(`tests/golden/board/recapture.py` — META `recaptured`). 정의대로인지는 독립 오라클이 지킨다
(tests/golden/test_board_golden.py `test_골든은_독립_오라클과_같다`,
tests/property/test_newhigh_oracle.py).
"""

from __future__ import annotations

import copy
import hashlib

from tests.golden.board_golden import GOLDEN, diff, load_meta

# 캡처한 커밋: P3 설계 커밋(4ea5b7f) — legacy board 엔진은 P2 완료(f959a9c) 와 같고 shim 전이다
LEGACY_COMMIT = "4ea5b7f"


def test_legacy_커밋이_shim_이전으로_고정돼_있다() -> None:
    meta = load_meta()
    assert meta["legacy_commit"] == LEGACY_COMMIT
    assert "shim 전" in meta["legacy_engine"]  # 입력의 출처
    rc = meta["recaptured"]
    assert rc["date"] == "2026-10-08" and rc["adr"] == "docs/adr/0017-newhigh-three-axes.md"
    assert "사용자 요청" in rc["reason"] and "kbj" in meta["legacy_engine"]
    assert meta["seed"] == 20260826
    assert (meta["n_days"], meta["n_stocks"]) == (30, 300)
    assert meta["compare"] == dict(rel_tol=1e-9, abs_tol=1e-6, ignore=["generated_at"])


def test_골든_파일_체크섬이_그대로다() -> None:
    meta = load_meta()
    want: dict[str, str] = meta["checksums"]
    got = {
        str(p.relative_to(GOLDEN)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(GOLDEN.glob("*/*.json.gz"))
    }
    assert got == want
    assert len(want) == 2 * 30 + 1  # 날짜별 입력·기대 + 공통 입력


def test_비교기는_허용오차_밖과_키_차이를_잡는다() -> None:
    base = {"a": 1.0, "b": [1, 2, {"c": "x"}], "generated_at": "t1", "source": "KRX"}
    same = copy.deepcopy(base)
    same["a"] = 1.0 + 1e-12  # 허용오차 안
    same["generated_at"] = "t2"  # 무시
    same["source"] = "krx"  # 대소문자 정규화
    out: list[str] = []
    diff("$", base, same, out)
    assert out == []

    for mutate in (
        lambda d: d.__setitem__("a", 1.001),
        lambda d: d["b"].append(3),
        lambda d: d["b"][2].__setitem__("c", "y"),
        lambda d: d.__setitem__("z", 0),
        lambda d: d["b"].reverse(),
    ):
        other = copy.deepcopy(base)
        mutate(other)
        out = []
        diff("$", base, other, out)
        assert out, other


def test_비교기는_정수와_불리언을_섞지_않는다() -> None:
    out: list[str] = []
    diff("$", {"x": True}, {"x": 1}, out)
    assert out
    out = []
    diff("$", {"x": 3}, {"x": 3.0}, out)  # sqlite REAL 대 int — 수로 같다
    assert out == []


def test_정규화는_잠정_문구_한_문장뿐이다() -> None:
    out: list[str] = []
    diff("$", {"n": "네이버 16:07 값으로 냈습니다"}, {"n": "KIS 마감값(잠정)으로 냈습니다"}, out)
    assert out == []
    diff("$", {"n": "네이버 값"}, {"n": "KIS 값"}, out)
    assert out
