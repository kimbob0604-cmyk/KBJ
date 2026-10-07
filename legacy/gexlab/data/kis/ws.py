"""KIS 웹소켓 프레임 파서 (docs/phase1_design.md §2 ws-gateway·recorder, PLAN §4.5).

프레임은 세 가지다.

- 데이터: `0|TR_ID|건수|v1^v2^...` — 건수만큼의 레코드가 `^` 로 이어 붙어 온다.
  컬럼 순서는 `config/kis_ws_fields.yaml` (KIS 공식 샘플에서 옮김).
- 암호화: `1|TR_ID|건수|...` — 체결통보. 복호화는 범위 밖(Phase 8)이라 표시만 한다.
- 제어: JSON — `PINGPONG`, 구독 등록·해지 응답.

파싱은 녹화와 분리돼 있다: recorder 는 원문을 먼저 저장하고, 여기서 실패해도 녹화는 막히지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from data.kis.models import Dec, Int, ReqDec, ReqStr

Session = Literal["day", "night"]
DEFAULT_FIELDS_PATH = Path(__file__).resolve().parents[2] / "config" / "kis_ws_fields.yaml"


class WsParseError(ValueError):
    pass


# ── 컬럼 설정 ────────────────────────────────────────────────────────────────


class TrSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    kind: Literal["futures", "option"]
    session: Session
    url: str
    columns: tuple[str, ...]


class WsSource(BaseModel):
    repo: str
    commit: str
    fetched: date


class WsFieldsConfig(BaseModel):
    source: WsSource
    trs: dict[str, TrSpec]


@cache
def load_fields(path: Path = DEFAULT_FIELDS_PATH) -> dict[str, TrSpec]:
    cfg = WsFieldsConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    return dict(cfg.trs)


# ── 프레임 ───────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class WsData:
    """평문 데이터 프레임. `rows` 는 레코드별 원문 값, `columns` 는 알려진 TR 이면 컬럼 이름."""

    tr_id: str
    rows: tuple[tuple[str, ...], ...]
    columns: tuple[str, ...] = ()

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def width(self) -> int:
        return len(self.rows[0]) if self.rows else 0

    @property
    def width_mismatch(self) -> bool:
        """레코드 폭이 설정의 컬럼 수와 다르다 — KIS 가 필드를 바꿨을 수 있다(health 경고 대상).

        뒤에 필드가 붙은 경우는 앞쪽 이름 매핑이 그대로 맞지만, 가운데가 바뀌었으면 틀린다.
        폭만으로는 둘을 가릴 수 없어 `ticks_from` 은 기본으로 거부한다.
        """
        return bool(self.columns) and self.width != len(self.columns)

    def records(self) -> list[dict[str, str]]:
        """컬럼 이름 → 원문 값. 컬럼보다 긴 값은 버리고, 짧으면 있는 만큼만.

        폭 검사를 하지 않는 원문 보기다 — 타입이 붙은 값은 `ticks_from` 으로.
        """
        return [dict(zip(self.columns, r, strict=False)) for r in self.rows]


@dataclass(frozen=True)
class WsEncrypted:
    """암호화 프레임 (체결통보). 본문은 repr 에 싣지 않는다."""

    tr_id: str
    count: int
    payload: str = field(repr=False)


class WsControl(BaseModel):
    """JSON 제어 프레임. 체결통보 구독 응답의 AES key·iv 는 `output` 에 두되 repr 에서 뺀다."""

    model_config = ConfigDict(frozen=True)

    tr_id: str
    tr_key: str | None = None
    encrypt: str | None = None
    datetime: str | None = None
    rt_cd: str | None = None
    msg_cd: str | None = None
    msg1: str | None = None
    output: dict[str, Any] = Field(default_factory=dict[str, Any], repr=False)

    @property
    def is_pingpong(self) -> bool:
        return self.tr_id == "PINGPONG"

    @property
    def ok(self) -> bool:
        return self.is_pingpong or self.rt_cd == "0"


WsFrame = WsData | WsEncrypted | WsControl


def _control(raw: str) -> WsControl:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise WsParseError(f"JSON 제어 프레임이 아니다: {e}") from e
    if not isinstance(obj, dict):
        raise WsParseError("JSON 제어 프레임이 객체가 아니다")
    header = obj.get("header")
    body = obj.get("body") or {}
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise WsParseError("제어 프레임에 header·body 가 없다")
    tr_id = header.get("tr_id")
    if not isinstance(tr_id, str) or not tr_id:
        raise WsParseError("제어 프레임에 tr_id 가 없다")
    out = body.get("output")
    try:
        return _control_model(tr_id, header, body, out)
    except ValidationError as e:
        # 입력값(암호화 key·iv 가 있을 수 있다)을 오류 문구·체인에 싣지 않는다
        errs = e.errors(include_input=False, include_url=False)
        raise WsParseError(f"제어 프레임 검증 실패: {errs}") from None


def _control_model(
    tr_id: str, header: dict[str, Any], body: dict[str, Any], out: object
) -> WsControl:
    return WsControl.model_validate(
        {
            "tr_id": tr_id,
            "tr_key": header.get("tr_key"),
            "encrypt": header.get("encrypt"),
            "datetime": header.get("datetime"),
            "rt_cd": body.get("rt_cd"),
            "msg_cd": body.get("msg_cd"),
            "msg1": body.get("msg1"),
            "output": out if isinstance(out, dict) else {},
        }
    )


def parse_frame(raw: str, specs: Mapping[str, TrSpec] | None = None) -> WsFrame:
    """웹소켓 수신 문자열 하나를 분류·분해한다. 형식이 틀리면 WsParseError."""
    if not raw:
        raise WsParseError("빈 프레임")
    if raw.lstrip().startswith("{"):
        return _control(raw)
    parts = raw.split("|", 3)
    if len(parts) != 4 or parts[0] not in ("0", "1"):
        raise WsParseError(f"알 수 없는 프레임: {raw[:40]!r}")
    flag, tr_id, cnt, payload = parts
    try:
        count = int(cnt)
    except ValueError as e:
        raise WsParseError(f"건수가 숫자가 아니다: {cnt!r}") from e
    if count < 1:
        raise WsParseError(f"건수가 1 미만: {count}")
    if flag == "1":
        return WsEncrypted(tr_id, count, payload)
    values = payload.split("^")
    if len(values) % count:
        raise WsParseError(f"{tr_id}: 값 {len(values)}개를 {count}건으로 나눌 수 없다")
    width = len(values) // count
    rows = tuple(tuple(values[i * width : (i + 1) * width]) for i in range(count))
    spec = (load_fields() if specs is None else specs).get(tr_id)
    return WsData(tr_id, rows, spec.columns if spec else ())


# ── 체결 틱 ──────────────────────────────────────────────────────────────────


class _Tick(BaseModel):
    """체결 틱 공통 (PLAN §4.5 fut_ticks·opt_ticks). 시각은 원문 HHMMSS — 변환은 core/calendar."""

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    tr_id: str
    session: Session
    hhmmss: ReqStr = Field(validation_alias="bsop_hour")
    qty: Int = Field(default=None, validation_alias="last_cnqn")  # 이번 체결량
    cum_vol: Int = Field(default=None, validation_alias="acml_vol")
    cum_value: Int = Field(default=None, validation_alias="acml_tr_pbmn")
    cum_sell_qty: Int = Field(default=None, validation_alias="seln_cntg_smtn")  # 총매도수량
    cum_buy_qty: Int = Field(default=None, validation_alias="shnu_cntg_smtn")  # 총매수수량
    oi: Int = Field(default=None, validation_alias="hts_otst_stpl_qty")
    oi_chg: Int = Field(default=None, validation_alias="otst_stpl_qty_icdc")


class FuturesTick(_Tick):
    """선물 체결 (H0IFCNT0 주간 · H0MFCNT0 야간)."""

    code: ReqStr = Field(validation_alias="futs_shrn_iscd")
    price: ReqDec = Field(gt=0, validation_alias="futs_prpr")  # 체결가 > 0 (컬럼 밀림 방어)
    bid: Dec = Field(default=None, validation_alias="futs_bidp1")
    ask: Dec = Field(default=None, validation_alias="futs_askp1")


class OptionTick(_Tick):
    """옵션 체결 (H0IOCNT0 주간 · H0EUCNT0 야간). 그릭스·IV 는 KIS 값."""

    code: ReqStr = Field(validation_alias="optn_shrn_iscd")
    price: ReqDec = Field(gt=0, validation_alias="optn_prpr")
    bid: Dec = Field(default=None, validation_alias="optn_bidp1")
    ask: Dec = Field(default=None, validation_alias="optn_askp1")
    delta: Dec = None
    gamma: Dec = Field(default=None, validation_alias="gama")
    vega: Dec = None
    theta: Dec = None
    rho: Dec = None
    iv: Dec = Field(default=None, validation_alias="hts_ints_vltl")

    @property
    def iv_kis(self) -> Decimal | None:
        return self.iv if self.iv is not None and self.iv > 0 else None


Tick = FuturesTick | OptionTick


def ticks_from(
    data: WsData, specs: Mapping[str, TrSpec] | None = None, *, allow_appended: bool = False
) -> list[Tick]:
    """데이터 프레임 → 체결 틱. 체결 TR 이 아니거나 레코드 폭이 설정과 다르면 WsParseError.

    폭이 다르면 가운데 필드가 끼거나 빠져 뒤 컬럼이 밀렸을 수 있다 — 값은 여전히 숫자라
    검증을 통과하므로(가격 자리에 전일대비율, 누적매수 자리에 누적매도) 틱을 만들지 않는다.
    `allow_appended=True` 는 KIS 가 끝에만 필드를 붙였다고 확인했을 때 쓰는 명시적 허용으로,
    설정보다 **긴** 레코드의 앞쪽만 매핑한다. 짧은 레코드는 언제나 거부.
    """
    spec = (load_fields() if specs is None else specs).get(data.tr_id)
    if spec is None:
        raise WsParseError(f"컬럼 설정이 없는 TR: {data.tr_id}")
    expected = len(spec.columns)
    if data.width != expected and not (allow_appended and data.width > expected):
        raise WsParseError(
            f"{data.tr_id}: 레코드 폭 {data.width} ≠ 설정 컬럼 {expected} — 필드 변경 의심"
        )
    model = FuturesTick if spec.kind == "futures" else OptionTick
    extra: dict[str, str] = {"tr_id": data.tr_id, "session": spec.session}
    # 폭을 검사한 그 컬럼으로 매핑한다 (parse_frame 에 준 설정과 다를 수 있다)
    return [
        model.model_validate({**dict(zip(spec.columns, row, strict=False)), **extra})
        for row in data.rows
    ]
