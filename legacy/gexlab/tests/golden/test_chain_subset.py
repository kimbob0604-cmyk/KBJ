"""작은 체인 fixture 자르기 (scripts/make_golden.py `chain-subset`, PLAN §6.3 골든).

- 커밋된 두 fixture(2026-09-28 14:27·14:52)는 이 규칙의 고정점이다 — 다시 잘라도 바이트까지 같다.
  14:27 은 규칙보다 먼저 손으로 잘랐고, 전체 스냅샷(probe_out, git 제외)에서 규칙으로 자른 결과가
  그것과 바이트까지 같았다(커밋 전 확인). 14:52 는 규칙으로 잘랐다
- 합성 전체 스냅샷: 고른 행·필드만 옮기고 나머지(응답 머리·다른 필드·다른 시리즈)는 버린다
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from scripts.make_golden import (
    BOARD_FIELDS,
    FILL_FIELDS,
    AroundAtm,
    Fixed,
    InputError,
    chain_subset,
    dump_chain,
    main,
)
from scripts.validate_greeks import Snapshot

VALIDATION = Path(__file__).resolve().parents[1] / "fixtures" / "validation"
SMALL = sorted(VALIDATION.glob("chain_snapshot_*_small.json"))
SECRET = "eyJsecret-looking-value-000"


def test_both_small_fixtures_exist() -> None:
    assert [p.name for p in SMALL] == [
        "chain_snapshot_synthetic_20260928_1427_small.json",
        "chain_snapshot_synthetic_20260928_1452_small.json",
    ]


@pytest.mark.parametrize("path", SMALL, ids=lambda p: p.name)
def test_small_fixture_is_a_fixed_point(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert dump_chain(chain_subset(json.loads(text))) == text
    Snapshot.model_validate_json(text)  # §6.2 검증 스크립트·골든이 읽는 형식


def _row(k: float, **extra: str) -> dict[str, str]:
    base = {f: "0.00" for f in BOARD_FIELDS} | {"acpr": f"{k:.2f}", "hts_otst_stpl_qty": "1"}
    return base | {"optn_shrn_iscd": "X", "approval_key": SECRET} | extra


def _board(strikes: list[float]) -> dict[str, Any]:
    rows = [_row(k) for k in sorted(strikes, reverse=True)]  # KIS 는 행사가 내림차순
    return {"ts_kst": "2026-09-28T14:00:01+09:00", "rt_cd": "0", "output1": rows, "output2": rows}


def _fill(series: str, k: float, rt_cd: str = "0") -> dict[str, Any]:
    row = {f: "1" for f in FILL_FIELDS} | {"acpr": f"{k:.2f}", "futs_last_tr_date": "20261008"}
    return {
        "ts_kst": "2026-09-28T14:00:05+09:00",
        "series": series,
        "rt_cd": rt_cd,
        "code": "B01610X",
        "row": row | {"hts_kor_isnm": "C 202610", "access_token": SECRET},
    }


def _grid(lo: float, hi: float) -> list[float]:
    n = round((hi - lo) / 2.5)
    return [lo + 2.5 * i for i in range(n + 1)]


def _full() -> dict[str, Any]:
    fills = [_fill(s, k) for s in ("C 202610", "P 202610") for k in _grid(1070, 1120)]
    fills += [_fill("C 202611", 1100.0), _fill("C 202610", 1100.0, rt_cd="1")]
    return {
        "started_kst": "2026-09-28T14:00:00.5+09:00",
        "atm_ref": {"code": "A01612", "price": 1096.3},
        "futures": {"access_token": SECRET},
        "boards": {
            "MONTH:202610": _board([1470.0, 1500.0, 1595.0]),
            "MONTH:202611": _board(_grid(1340, 1360)),
            "WKM:260904": _board(_grid(1080, 1110)),
            "WKI:261001": _board(_grid(1080, 1110)),
            "WKM:261001": _board(_grid(1080, 1110)),
        },
        "fills": fills,
    }


def _strikes(rows: list[dict[str, str]]) -> list[str]:
    return [r["acpr"] for r in rows]


def test_subset_keeps_chosen_rows_and_fields_only() -> None:
    small = chain_subset(_full())
    assert small["started_kst"] == "2026-09-28T14:00:00.5+09:00"  # 원문 표기 그대로
    assert small["atm_ref"] == {"code": "A01612", "price": 1096.3}
    assert list(small["boards"]) == ["MONTH:202610", "MONTH:202611", "WKM:260904", "WKM:261001"]
    b = small["boards"]
    assert _strikes(b["MONTH:202610"]["output1"]) == ["1595.00", "1470.00"]
    assert _strikes(b["MONTH:202611"]["output2"]) == ["1352.50", "1350.00", "1347.50"]
    # ATM = 1097.50 (S_ref 1096.3) — 콜 ATM±2, 풋 ATM±3, 원문(내림차순) 순서
    assert _strikes(b["WKM:260904"]["output1"]) == [
        "1102.50",
        "1100.00",
        "1097.50",
        "1095.00",
        "1092.50",
    ]
    assert _strikes(b["WKM:260904"]["output2"])[0] == "1105.00"
    assert _strikes(b["WKM:260904"]["output2"])[-1] == "1090.00"
    assert _strikes(b["WKM:261001"]["output1"]) == ["1097.50", "1095.00"]
    assert all(list(r) == list(BOARD_FIELDS) for s in b.values() for r in s["output1"])
    fills = small["fills"]
    assert {f["series"] for f in fills} == {"C 202610", "P 202610"}  # 다른 만기·rt_cd ≠ 0 은 뺀다
    assert sorted({f["row"]["acpr"] for f in fills}) == [f"{k:.2f}" for k in _grid(1085, 1110)]
    assert all(list(f) == ["ts_kst", "series", "rt_cd", "row"] for f in fills)
    assert all(list(f["row"]) == list(FILL_FIELDS) for f in fills)
    text = dump_chain(small)
    assert SECRET not in text and "access_token" not in text and "approval_key" not in text
    assert json.loads(text) == small


def test_subset_rules_are_configurable() -> None:
    small = chain_subset(
        _full(), {"WKM:260904": AroundAtm((0, 0), (-1, 1)), "MONTH:202610": Fixed(("1500.00",))}
    )
    assert list(small["boards"]) == ["WKM:260904", "MONTH:202610"]
    assert _strikes(small["boards"]["WKM:260904"]["output1"]) == ["1097.50"]
    assert _strikes(small["boards"]["WKM:260904"]["output2"]) == ["1100.00", "1097.50", "1095.00"]
    assert _strikes(small["boards"]["MONTH:202610"]["output2"]) == ["1500.00"]


def test_dump_writes_empty_lists_inline() -> None:
    full = _full()
    full["fills"] = []
    full["boards"]["WKM:261001"]["output2"] = []
    small = chain_subset(full, {"WKM:261001": AroundAtm((-1, 0), (-1, 0))})
    text = dump_chain(small)
    assert '"output2": []\n' in text and '"fills": []\n' in text
    assert json.loads(text) == small


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d.update(started_kst="2026-09-28T14:00:00"), "형식"),  # naive
        (lambda d: d["boards"].pop("WKM:261001"), "WKM:261001"),
        (lambda d: [r.pop("gama") for r in d["boards"]["WKM:260904"]["output1"]], "gama"),
        (lambda d: d["boards"]["WKM:260904"]["output1"][0].update(acpr=SECRET), "숫자"),
        (lambda d: d["atm_ref"].update(price=0), "형식"),
        # 고정 행사가가 한쪽(풋)에 없다 — 조용히 짧은 fixture 를 만들지 않는다
        (
            lambda d: d["boards"]["MONTH:202610"].update(
                output2=d["boards"]["MONTH:202610"]["output2"][:-1]
            ),
            r"MONTH:202610 output2: 행사가 \['1470.00'\] 가 전광판에 없다",
        ),
    ],
)
def test_subset_rejects_bad_input_without_echoing_values(change: Any, message: str) -> None:
    full = _full()
    change(full)
    with pytest.raises(InputError, match=message) as e:
        chain_subset(full)
    assert SECRET not in str(e.value)


def test_fixed_strike_missing_from_board_is_an_error() -> None:
    with pytest.raises(InputError, match=r"MONTH:202610 output1: 행사가 \['9999.00'\]"):
        chain_subset(_full(), {"MONTH:202610": Fixed(("1500.00", "9999.00"))})
    same = chain_subset(_full(), {"MONTH:202610": Fixed(("1500",))})  # 값이 같으면 든다
    assert _strikes(same["boards"]["MONTH:202610"]["output2"]) == ["1500.00"]


def test_cli_writes_only_with_write(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src, out = tmp_path / "chain_snapshot.json", tmp_path / "small.json"
    src.write_text(json.dumps(_full()), encoding="utf-8")
    assert main(["chain-subset", str(src), "--out", str(out)]) == 1  # 없다 — 쓰지 않는다
    assert not out.exists()
    assert main(["chain-subset", str(src), "--out", str(out), "--write"]) == 0
    assert out.read_text(encoding="utf-8") == dump_chain(chain_subset(_full()))
    assert main(["chain-subset", str(src), "--out", str(out)]) == 0  # 같다
    out.write_text("{}", encoding="utf-8")
    assert main(["chain-subset", str(src), "--out", str(out)]) == 1  # 다르다 — 그대로 둔다
    assert out.read_text(encoding="utf-8") == "{}"
    src.write_text("{", encoding="utf-8")
    assert main(["chain-subset", str(src), "--out", str(out)]) == 2
    assert "입력 오류" in capsys.readouterr().err
