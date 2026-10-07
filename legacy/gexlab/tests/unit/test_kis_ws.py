"""KIS 웹소켓 프레임 파서.

여기 프레임은 전부 **합성(SYNTHETIC)** 이다 — `config/kis_ws_fields.yaml` 컬럼 목록으로 만든 값이지
녹화가 아니다. Phase 1 녹화 뒤 실제 프레임(tests/golden)으로 바꾼다.
"""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from data.kis.ws import (
    FuturesTick,
    OptionTick,
    TrSpec,
    WsControl,
    WsData,
    WsEncrypted,
    WsParseError,
    load_fields,
    parse_frame,
    ticks_from,
)

SPECS = load_fields()


def synth_record(tr_id: str, **values: str) -> list[str]:
    """SYNTHETIC: 컬럼 순서대로 값을 채운 레코드 한 건 (지정 안 한 칸은 '0')."""
    cols = SPECS[tr_id].columns
    unknown = set(values) - set(cols)
    assert not unknown, unknown
    return [values.get(c, "0") for c in cols]


def synth_frame(tr_id: str, *records: list[str], flag: str = "0") -> str:
    """SYNTHETIC: `flag|TR|건수|v^v^...` — 레코드를 `^` 로 이어 붙인다."""
    payload = "^".join(v for r in records for v in r)
    return f"{flag}|{tr_id}|{len(records):03d}|{payload}"


# ── 컬럼 설정 ──


def test_field_config_has_four_trade_trs() -> None:
    assert set(SPECS) == {"H0IFCNT0", "H0IOCNT0", "H0MFCNT0", "H0EUCNT0"}
    widths = {k: len(v.columns) for k, v in SPECS.items()}
    assert widths == {"H0IFCNT0": 50, "H0IOCNT0": 58, "H0MFCNT0": 49, "H0EUCNT0": 56}
    assert {k: (v.kind, v.session) for k, v in SPECS.items()} == {
        "H0IFCNT0": ("futures", "day"),
        "H0IOCNT0": ("option", "day"),
        "H0MFCNT0": ("futures", "night"),
        "H0EUCNT0": ("option", "night"),
    }
    for spec in SPECS.values():
        assert len(set(spec.columns)) == len(spec.columns)
        assert spec.columns[1] == "bsop_hour"
        # 누적 매도·매수 체결수량 (PLAN §5.5 HIRO-lite)
        assert {"seln_cntg_smtn", "shnu_cntg_smtn", "last_cnqn", "acml_vol"} <= set(spec.columns)
        assert {"hts_otst_stpl_qty", "otst_stpl_qty_icdc"} <= set(spec.columns)
        assert spec.url.startswith("https://github.com/koreainvestment/open-trading-api/")
    for tr in ("H0IOCNT0", "H0EUCNT0"):
        assert {"delta", "gama", "vega", "theta", "hts_ints_vltl"} <= set(SPECS[tr].columns)


# ── 데이터 프레임 ──


def test_single_futures_record() -> None:
    rec = synth_record(
        "H0IFCNT0",
        futs_shrn_iscd="A01612",
        bsop_hour="142701",
        futs_prpr="1095.10",
        last_cnqn="3",
        acml_vol="82481",
        seln_cntg_smtn="41000",
        shnu_cntg_smtn="41481",
        hts_otst_stpl_qty="136321",
        otst_stpl_qty_icdc="-12",
        futs_askp1="1095.15",
        futs_bidp1="1095.10",
    )
    frame = parse_frame(synth_frame("H0IFCNT0", rec))
    assert isinstance(frame, WsData)
    assert (frame.tr_id, frame.count, frame.width, frame.width_mismatch) == (
        "H0IFCNT0",
        1,
        50,
        False,
    )
    assert frame.records()[0]["futs_prpr"] == "1095.10"
    [t] = ticks_from(frame)
    assert isinstance(t, FuturesTick)
    assert (t.code, t.hhmmss, t.price, t.qty) == ("A01612", "142701", Decimal("1095.10"), 3)
    assert (t.cum_vol, t.cum_sell_qty, t.cum_buy_qty) == (82481, 41000, 41481)
    assert (t.oi, t.oi_chg) == (136321, -12)
    assert (t.bid, t.ask) == (Decimal("1095.10"), Decimal("1095.15"))
    assert (t.tr_id, t.session) == ("H0IFCNT0", "day")


def test_multi_record_option_frame() -> None:
    recs = [
        synth_record(
            "H0IOCNT0",
            optn_shrn_iscd=f"B01610A{i}",
            bsop_hour=f"14270{i}",
            optn_prpr=f"2{i}.50",
            last_cnqn=str(i + 1),
            acml_vol=str(100 + i),
            seln_cntg_smtn=str(40 + i),
            shnu_cntg_smtn=str(60 + i),
            delta="0.5320",
            gama="0.0029",
            vega="0.7201",
            theta="-2.7581",
            rho="0.1442",
            hts_ints_vltl="34.8772",
        )
        for i in range(3)
    ]
    frame = parse_frame(synth_frame("H0IOCNT0", *recs))
    assert isinstance(frame, WsData)
    assert frame.count == 3 and frame.width == 58
    ticks = ticks_from(frame)
    assert [t.code for t in ticks] == ["B01610A0", "B01610A1", "B01610A2"]
    assert [t.price for t in ticks] == [Decimal("20.50"), Decimal("21.50"), Decimal("22.50")]
    assert [t.cum_buy_qty for t in ticks] == [60, 61, 62]
    assert [t.cum_sell_qty for t in ticks] == [40, 41, 42]
    t = ticks[1]
    assert isinstance(t, OptionTick)
    assert (t.delta, t.gamma, t.vega, t.theta, t.rho) == (
        Decimal("0.5320"),
        Decimal("0.0029"),
        Decimal("0.7201"),
        Decimal("-2.7581"),
        Decimal("0.1442"),
    )
    assert t.iv == Decimal("34.8772") == t.iv_kis
    assert t.session == "day"


@pytest.mark.parametrize(("tr_id", "model"), [("H0MFCNT0", FuturesTick), ("H0EUCNT0", OptionTick)])
def test_night_trs_have_their_own_widths(tr_id: str, model: type) -> None:
    code_col = "futs_shrn_iscd" if model is FuturesTick else "optn_shrn_iscd"
    price_col = "futs_prpr" if model is FuturesTick else "optn_prpr"
    rec = synth_record(tr_id, **{code_col: "X1", price_col: "1.25", "bsop_hour": "231500"})
    frame = parse_frame(synth_frame(tr_id, rec, rec))
    assert isinstance(frame, WsData) and not frame.width_mismatch
    ticks = ticks_from(frame)
    assert len(ticks) == 2
    assert all(isinstance(t, model) and t.session == "night" for t in ticks)
    assert ticks[0].price == Decimal("1.25")


def test_blank_values_become_none() -> None:
    rec = synth_record(
        "H0IOCNT0", optn_shrn_iscd="C1", optn_prpr="1.00", optn_bidp1="", hts_ints_vltl=" "
    )
    frame = parse_frame(synth_frame("H0IOCNT0", rec))
    assert isinstance(frame, WsData)
    [t] = ticks_from(frame)
    assert isinstance(t, OptionTick)
    assert t.bid is None and t.iv is None and t.iv_kis is None


def test_zero_iv_is_not_an_iv() -> None:
    rec = synth_record("H0EUCNT0", optn_shrn_iscd="C1", optn_prpr="1.00", hts_ints_vltl="0.0000")
    frame = parse_frame(synth_frame("H0EUCNT0", rec))
    assert isinstance(frame, WsData)
    [t] = ticks_from(frame)
    assert isinstance(t, OptionTick) and t.iv == 0 and t.iv_kis is None


def test_appended_fields_need_opt_in() -> None:
    # KIS 가 레코드 끝에 필드를 붙인 경우 (dynm_* 가 그렇게 추가됐다) — 앞쪽 매핑은 맞지만
    # 끝에 붙었는지 가운데가 바뀌었는지는 폭만으로 알 수 없어 기본은 거부
    recs = [[*synth_record("H0IFCNT0", futs_shrn_iscd="A01612", futs_prpr="1.5"), "N", "9"]] * 2
    frame = parse_frame(synth_frame("H0IFCNT0", *recs))
    assert isinstance(frame, WsData)
    assert frame.width == 52 and frame.width_mismatch
    with pytest.raises(WsParseError, match="폭"):
        ticks_from(frame)
    ticks = ticks_from(frame, allow_appended=True)
    assert [t.price for t in ticks] == [Decimal("1.5"), Decimal("1.5")]


def _futures_rec_with_prev() -> list[str]:
    return synth_record(
        "H0IFCNT0",
        futs_shrn_iscd="A01612",
        bsop_hour="142701",
        futs_prdy_vrss="-32.65",
        futs_prdy_ctrt="-2.90",
        futs_prpr="1095.10",
        last_cnqn="3",
        seln_cntg_smtn="41000",
        shnu_cntg_smtn="41481",
    )


def test_field_inserted_mid_record_is_rejected() -> None:
    # 가운데 필드 하나가 끼면 뒤 컬럼이 한 칸씩 밀린다: 그대로 두면 가격=전일대비율,
    # 누적매수=실제 누적매도로 검증을 통과해 버린다 (HIRO-lite 가 읽는 값)
    rec = _futures_rec_with_prev()
    rec.insert(2, "7")
    frame = parse_frame(synth_frame("H0IFCNT0", rec))
    assert isinstance(frame, WsData) and frame.width == 51 and frame.width_mismatch
    with pytest.raises(WsParseError, match="폭"):
        ticks_from(frame)
    # 끝에 붙었다고 잘못 허용해도 밀린 가격(전일대비율 -2.90)은 체결가 > 0 에서 걸린다
    with pytest.raises(ValidationError, match="futs_prpr"):
        ticks_from(frame, allow_appended=True)


@pytest.mark.parametrize("allow_appended", [False, True])
def test_field_removed_is_rejected(allow_appended: bool) -> None:
    rec = _futures_rec_with_prev()
    del rec[2]
    frame = parse_frame(synth_frame("H0IFCNT0", rec))
    assert isinstance(frame, WsData) and frame.width == 49 and frame.width_mismatch
    with pytest.raises(WsParseError, match="폭"):
        ticks_from(frame, allow_appended=allow_appended)


@pytest.mark.parametrize("price", ["0", "-2.90", "0.00"])
@pytest.mark.parametrize(
    ("tr_id", "code_col", "price_col"),
    [
        ("H0IFCNT0", "futs_shrn_iscd", "futs_prpr"),
        ("H0IOCNT0", "optn_shrn_iscd", "optn_prpr"),
    ],
)
def test_non_positive_trade_price_is_rejected(
    tr_id: str, code_col: str, price_col: str, price: str
) -> None:
    # 체결가는 항상 양수 — 폭이 같아도 순서가 바뀐 경우를 값에서 한 번 더 거른다
    rec = synth_record(tr_id, **{code_col: "X1", price_col: price, "bsop_hour": "142701"})
    frame = parse_frame(synth_frame(tr_id, rec))
    assert isinstance(frame, WsData) and not frame.width_mismatch
    with pytest.raises(ValidationError):
        ticks_from(frame)


def test_values_not_divisible_by_count() -> None:
    rec = synth_record("H0IFCNT0")
    with pytest.raises(WsParseError, match="나눌 수 없다"):
        parse_frame(f"0|H0IFCNT0|002|{'^'.join(rec)}^extra")


@pytest.mark.parametrize("cnt", ["abc", "000", "-1"])
def test_bad_count(cnt: str) -> None:
    with pytest.raises(WsParseError):
        parse_frame(f"0|H0IFCNT0|{cnt}|a^b")


def test_unknown_tr_keeps_raw_rows() -> None:
    frame = parse_frame("0|H0IFASP0|001|A01612^142701^1095.10")
    assert isinstance(frame, WsData)
    assert frame.columns == () and frame.rows == (("A01612", "142701", "1095.10"),)
    assert frame.records() == [{}]
    assert not frame.width_mismatch
    with pytest.raises(WsParseError, match="H0IFASP0"):
        ticks_from(frame)


def test_custom_specs_override_config() -> None:
    specs = {
        "TEST0001": TrSpec(
            name="t",
            kind="futures",
            session="night",
            url="x",
            columns=("futs_shrn_iscd", "bsop_hour", "futs_prpr"),
        )
    }
    frame = parse_frame("0|TEST0001|002|A^010000^1.0^B^010001^2.0", specs)
    assert isinstance(frame, WsData)
    ticks = ticks_from(frame, specs)
    assert [(t.code, t.price, t.session) for t in ticks] == [
        ("A", Decimal("1.0"), "night"),
        ("B", Decimal("2.0"), "night"),
    ]


def test_missing_price_is_a_validation_error() -> None:
    rec = synth_record("H0IFCNT0", futs_shrn_iscd="A01612", futs_prpr="")
    frame = parse_frame(synth_frame("H0IFCNT0", rec))
    assert isinstance(frame, WsData)
    with pytest.raises(ValidationError):
        ticks_from(frame)


# ── 암호화·제어 ──


def test_encrypted_frame_is_marked_not_decrypted() -> None:
    frame = parse_frame("1|H0IFCNI0|001|U0lOVEhFVElDLUNJUEhFUlRFWFQ=")
    assert isinstance(frame, WsEncrypted)
    assert (frame.tr_id, frame.count) == ("H0IFCNI0", 1)
    assert frame.payload.startswith("U0lO")
    assert "U0lO" not in repr(frame)


def test_pingpong() -> None:
    frame = parse_frame('{"header":{"tr_id":"PINGPONG","datetime":"20260928142700"}}')
    assert isinstance(frame, WsControl)
    assert frame.is_pingpong and frame.ok
    assert frame.datetime == "20260928142700"


def test_subscribe_ack_hides_cipher_material() -> None:
    raw = (
        '{"header":{"tr_id":"H0IFCNI0","tr_key":"SYNTHETICID","encrypt":"N"},'
        '"body":{"rt_cd":"0","msg_cd":"OPSP0000","msg1":"SUBSCRIBE SUCCESS",'
        '"output":{"iv":"SYNTHETIC-IV-0000","key":"SYNTHETIC-KEY-000000000000000000"}}}'
    )
    frame = parse_frame(raw)
    assert isinstance(frame, WsControl)
    assert (frame.tr_id, frame.tr_key, frame.rt_cd, frame.msg1) == (
        "H0IFCNI0",
        "SYNTHETICID",
        "0",
        "SUBSCRIBE SUCCESS",
    )
    assert frame.ok and not frame.is_pingpong
    assert frame.output["iv"] == "SYNTHETIC-IV-0000"
    assert "SYNTHETIC-KEY" not in repr(frame) and "SYNTHETIC-IV" not in repr(frame)


def test_subscribe_error_ack() -> None:
    raw = (
        '{"header":{"tr_id":"H0IOCNT0","tr_key":"B01610A51","encrypt":"N"},'
        '"body":{"rt_cd":"1","msg_cd":"OPSP0002","msg1":"ALREADY IN SUBSCRIBE"}}'
    )
    frame = parse_frame(raw)
    assert isinstance(frame, WsControl)
    assert not frame.ok and frame.output == {}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{not json",
        "[1, 2]",
        '{"body": {}}',
        "2|H0IFCNT0|001|a",
        "0|H0IFCNT0|001",
        "hello",
    ],
)
def test_malformed_frames(raw: str) -> None:
    with pytest.raises(WsParseError):
        parse_frame(raw)


def test_control_validation_error_does_not_echo_cipher_material() -> None:
    raw = (
        '{"header":{"tr_id":"H0IFCNI0","tr_key":123},'
        '"body":{"rt_cd":"0","output":{"key":"SYNTHETIC-KEY-LEAK-CHECK"}}}'
    )
    with pytest.raises(WsParseError) as ei:
        parse_frame(raw)
    assert "SYNTHETIC-KEY-LEAK-CHECK" not in str(ei.value)
    assert ei.value.__cause__ is None


def test_control_without_tr_id() -> None:
    with pytest.raises(WsParseError, match="tr_id"):
        parse_frame('{"header":{},"body":{}}')


def test_ticks_map_with_the_checked_spec() -> None:
    # parse_frame 은 기본 설정(모르는 TR → 컬럼 없음), ticks_from 은 주어진 설정으로 매핑·폭 검사
    specs = {
        "TEST0002": TrSpec(
            name="t",
            kind="futures",
            session="day",
            url="x",
            columns=("futs_shrn_iscd", "bsop_hour", "futs_prpr"),
        )
    }
    frame = parse_frame("0|TEST0002|001|A^090000^3.5")
    assert isinstance(frame, WsData) and frame.columns == ()
    [t] = ticks_from(frame, specs)
    assert (t.code, t.price) == ("A", Decimal("3.5"))
