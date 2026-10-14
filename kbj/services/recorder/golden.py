"""raw 녹화 골든 도구 — GX `scripts/make_golden.py` 의 `raw`·`raw-parse` 이식(P7 ING,
docs/p7_design.md §1.4). `core`·`chain-subset` 은 `kbj.services.engine.gex_golden`(FND-C) — 파일
I/O 도우미(`rel`·`check_or_write`·`load_json`·`write_golden`·`check_schema`·`InputError`·
`GoldenSchemaError`)도 그쪽 것을 쓴다(두 벌 금지).

KIS·KRX 를 부르지 않는다(`raw --from-db` 만 `KBJ_DATABASE_URL` — 출력·오류에 싣지 않는다). 쓰는
곳은 늘 `--write` 를 준 때뿐이다 — 없으면 이미 있는 파일과 비교만 하고, 다르거나 없으면 까닭을 찍고
종료 코드 1(같으면 0). 입력·DB 오류·비밀 같은 문자열·단위(허용)를 모르는 골든 float
(`GoldenSchemaError`)는 2(아무것도 쓰지 않음).

    uv run python -m kbj.services.recorder.golden raw (--from-db | --from-jsonl <내보내기.jsonl>) \
        --start <ISO+시간대> --end <ISO+시간대> --name <이름> \
        [--source kis_ws|kis_rest|krx ...] [--tr-id <TR> ...] [--max-rows 2000] [--write]
    uv run python -m kbj.services.recorder.golden raw-parse [--name <이름> ...] [--write]

- raw: `prv_gex.raw_messages` 의 시간 창 [start, end)(거르기 `--source`·`--tr-id`)을 잘라
  tests/golden/gex/raw/<이름>.jsonl(녹화 — 한 줄에 한 행, 정규화)과 <이름>.parsed.json(파서 출력
  스냅샷 — `kbj.data.private.kis.ws` 프레임·체결 틱·체결 시각 변환,
  `kbj.data.private.kis.derivatives` REST 모델)을 만든다. 입력은 DB(`--from-db`) 또는 DB 내보내기
  JSONL(`--from-jsonl` — 예: `psql "$KBJ_DATABASE_URL" -At -c "SELECT row_to_json(r) FROM
  prv_gex.raw_messages r WHERE ts >= '…' AND ts < '…'" > x.jsonl` — `\\copy` 는 텍스트 형식이
  역슬래시를 겹쳐 JSON 이 깨진다).
  옮기기 전 가린다(`sanitize`): 웹소켓 체결통보 암호문(`1|TR|건수|` 뒤)과 제어 프레임 body.output
  의 복호화 키·IV, 체결통보 TR(`NOTICE_TR`)의 구독 키(HTS ID — 행 key·header.tr_key, 평문 `0|…`
  프레임 본문) → `REDACTED`. 가릴 모양이 아니면(JSON 이 아닌 제어 프레임, 객체가 아닌 header·body·
  body.output) 쓰지 않는다(fail closed). 가린 뒤에도 비밀 같은 문자열(`SECRET_PATTERNS`)이나
  REDACTED 아닌 `"key"`·`"iv"` 값이 본문에 남으면 쓰지 않는다. 골든 한 벌은 `--max-rows`(기본
  2000) 이하. 비교는 tests/golden/gex/test_raw_golden.py
- raw-parse: 이미 있는 녹화(tests/golden/gex/raw/*.jsonl)의 파서 출력 스냅샷만 다시 만든다 — 파서를
  일부러 바꿨을 때
- 라이브 녹화가 아직 없어(GX Phase 1 소크 전) 녹화 골든은 합성 한 벌(synthetic_20260928 —
  tests/golden/gex/synthetic.py)뿐이다. 라이브 녹화는 로그인 등급 원문이라 공개 레포에 넣지 않는다
  (CLAUDE.md §2) — 키를 받은 뒤 운영 VM 에서 `raw --from-db` 로 잘라 비공개로만 비교한다
  [확인 필요].
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal, cast

import psycopg
from psycopg import sql
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    ValidationError,
    model_validator,
)

from kbj.data.private.kis.derivatives import (
    TR_CALLPUT,
    TR_FUT_BOARD,
    TR_INVESTOR,
    TR_OPTION_LIST,
    TR_PRICE,
    TR_TOP,
    CallPutRow,
    FuturesBoardRow,
    InvestorRow,
    MinuteBar,
    OptionListRow,
    PriceOutput,
    output_rows,
    parse_rows,
)
from kbj.data.private.kis.rest import MINUTE_TR
from kbj.data.private.kis.ws import (
    FuturesTick,
    OptionTick,
    WsControl,
    WsEncrypted,
    WsParseError,
    load_fields,
    parse_frame,
    ticks_from,
)
from kbj.services.engine.gex_golden import (
    ROOT,
    GoldenSchemaError,
    InputError,
    check_schema,
    compare_golden,
    failure_report,
)
from kbj.services.engine.gex_golden import check_or_write as _check_or_write
from kbj.services.engine.gex_golden import load_json as _load_json
from kbj.services.engine.gex_golden import rel as _rel
from kbj.services.engine.gex_golden import write_golden as _check_golden
from kbj.services.ws_gateway.service import tick_time

# ── raw: raw_messages 창 → 녹화 골든 + 파서 출력 스냅샷 ───────────────────────

RAW_DIR = ROOT / "tests" / "golden" / "gex" / "raw"
RAW_MAX_ROWS = 2000  # 골든 한 벌의 상한 — 넘으면 창을 좁히거나 거른다
REDACTED = "REDACTED"
# 체결통보 구독 응답(제어 프레임 body.output)의 AES 복호화 키·IV — 녹화 골든에 옮기지 않는다
WS_SECRET_OUTPUT: tuple[str, ...] = ("key", "iv")
# 체결통보 TR(주간 H0IFCNI0·야간 H0MFCNI0·H0EUCNI0, 모의투자 …CNI9 가정) — 구독 키(tr_key)가
# HTS ID 다. 행 key·제어 프레임 header.tr_key 를 가리고, 평문 데이터 프레임(`0|…`)이면 본문(고객
# ID·계좌)도 가린다. Phase 8 전엔 구독하지 않는다(config/ws_budget.yaml — 자리만)
NOTICE_TR = re.compile(r"CNI[0-9]$")
# 비밀 같은 문자열 — 하나라도 있으면 쓰지 않는다. 합성 fixture 검사 목록(GX tests/unit/
# test_fixtures_clean.py `PATTERNS` — KBJ 사본은 tests/golden/gex/synthetic.py)을 모두 담는다
# (시험이 확인한다)
SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}"),  # JWT
    re.compile(r"Bearer\s", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"app_?key", re.IGNORECASE),
    re.compile(r"app_?secret", re.IGNORECASE),
    re.compile(r"access_token", re.IGNORECASE),
    re.compile(r"approval_key", re.IGNORECASE),
)
# REST TR → (응답 output 키, 검증 모델) — 수집기(poller·scheduler 분봉)가 쓰는 그 모델
REST_PARSERS: Mapping[str, tuple[tuple[str, type[BaseModel]], ...]] = {
    TR_CALLPUT: (("output1", CallPutRow), ("output2", CallPutRow)),
    TR_PRICE: (("output1", PriceOutput),),
    TR_FUT_BOARD: (("output", FuturesBoardRow),),
    TR_INVESTOR: (("output", InvestorRow),),
    TR_OPTION_LIST: (("output", OptionListRow),),
    MINUTE_TR: (("output2", MinuteBar),),
}
# 기초자산 시세 — 응답 필드가 미실측이라 수집기도 원문만 남긴다(poller `_on_underlying`)
RAW_ONLY_TRS: frozenset[str] = frozenset({TR_TOP})


class SecretFound(InputError):
    """자른 녹화에 비밀 같은 문자열이 있다 — 아무것도 쓰지 않는다."""


class RawRow(BaseModel):
    """raw_messages 한 행 = 녹화 JSONL 한 줄. DB 열 그대로(digest 는 다시 계산할 수 있어 뺀다).

    DB 내보내기(`SELECT row_to_json(r) FROM raw_messages r …`)의 줄도 그대로 읽는다 — 모르는 열은
    버린다. 시각은 aware 만.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    ts: AwareDatetime
    trade_date: date | None = None
    session: Literal["day", "night"] | None = None
    source: Literal["kis_ws", "kis_rest", "krx"]
    tr_id: str
    key: str = ""
    payload: dict[str, Any] | None = None
    payload_text: str | None = None
    lossy: bool = False

    @model_validator(mode="after")
    def _shape(self) -> RawRow:
        if (self.payload is None) == (self.payload_text is None):
            raise ValueError("payload·payload_text 중 하나만 있어야 한다")
        if (self.trade_date is None) != (self.session is None):
            raise ValueError("trade_date·session 은 둘 다 있거나 둘 다 없다")
        return self

    def body_text(self) -> str:
        if self.payload_text is not None:
            return self.payload_text
        return json.dumps(self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def to_line(self) -> str:
        """정규화한 JSONL 한 줄 — 키 정렬·UTC 시각·공백 없음(다시 잘라도 같다)."""
        d: dict[str, Any] = {
            "ts": self.ts.astimezone(UTC).isoformat(),
            "trade_date": None if self.trade_date is None else self.trade_date.isoformat(),
            "session": self.session,
            "source": self.source,
            "tr_id": self.tr_id,
            "key": self.key,
        }
        if self.payload is not None:
            d["payload"] = self.payload
        else:
            d["payload_text"] = self.payload_text
        if self.lossy:
            d["lossy"] = True
        return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class RawWindow:
    """자를 창 [start, end) 과 거르기(비면 전부)."""

    start: datetime
    end: datetime
    sources: tuple[str, ...] = ()
    tr_ids: tuple[str, ...] = ()
    max_rows: int = RAW_MAX_ROWS

    def __post_init__(self) -> None:
        for name, t in (("start", self.start), ("end", self.end)):
            if t.tzinfo is None or t.utcoffset() is None:
                raise InputError(f"{name} 는 시간대가 있는 시각이어야 한다(naive 금지)")
        if not self.start < self.end:
            raise InputError("start < end 여야 한다")
        if self.max_rows < 1:
            raise InputError("max_rows 는 1 이상")

    def admits(self, row: RawRow) -> bool:
        return (
            self.start <= row.ts < self.end
            and (not self.sources or row.source in self.sources)
            and (not self.tr_ids or row.tr_id in self.tr_ids)
        )

    def meta(self, origin: str) -> dict[str, Any]:
        return {
            "from": origin,
            "start": self.start.astimezone(UTC).isoformat(),
            "end": self.end.astimezone(UTC).isoformat(),
            "sources": list(self.sources),
            "tr_ids": list(self.tr_ids),
        }


def read_jsonl(path: Path) -> list[RawRow]:
    """JSONL(한 줄에 raw_messages 한 행)을 모두 읽는다. 틀린 줄은 줄 번호로 InputError(값은 싣지
    않는다), 빈 줄은 건너뛴다."""
    try:
        # "\n" 으로만 가른다 — splitlines 는 JSON 이 이스케이프하지 않는 U+2028 등에서도 가른다
        lines = path.read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise InputError(f"{path.name}: 읽지 못했다 ({type(e).__name__})") from None
    rows: list[RawRow] = []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            rows.append(RawRow.model_validate_json(line))
        except ValidationError as e:
            where = sorted({".".join(map(str, x["loc"])) or "(행)" for x in e.errors()})
            raise InputError(f"{path.name}:{n}: raw_messages 행이 아니다 — {where}") from None
    return rows


class DbRawSource:
    """`prv_gex.raw_messages` 를 DB 에서 읽는다(`KBJ_DATABASE_URL` — 출력·오류 문구에 싣지 않는다).

    connect 는 시험이 넣는다. 오류는 종류 이름만 남긴다 — psycopg 문구에 접속 정보가 섞일 수 있다.
    """

    def __init__(self, connect: Callable[[], psycopg.Connection[Any]]) -> None:
        self._connect = connect

    @classmethod
    def from_url(cls, dsn: str) -> DbRawSource:
        def connect() -> psycopg.Connection[Any]:
            return psycopg.connect(dsn, connect_timeout=5, application_name="kbj-recorder-golden")

        return cls(connect)

    @classmethod
    def from_settings(cls) -> DbRawSource:
        from kbj.config.settings import Settings

        url = Settings().database_url
        if url is None:
            raise InputError("KBJ_DATABASE_URL 이 없다(.env·환경변수)")
        return cls.from_url(url.get_secret_value())

    def read(self, window: RawWindow) -> list[RawRow]:
        conds = [sql.SQL("ts >= %s"), sql.SQL("ts < %s")]
        params: list[Any] = [window.start, window.end]
        if window.sources:
            conds.append(sql.SQL("source = ANY(%s)"))
            params.append(list(window.sources))
        if window.tr_ids:
            conds.append(sql.SQL("tr_id = ANY(%s)"))
            params.append(list(window.tr_ids))
        query = sql.SQL(
            "SELECT ts, trade_date, session, source, tr_id, key, payload, payload_text, lossy "
            "FROM prv_gex.raw_messages WHERE {} ORDER BY ts, digest LIMIT %s"
        ).format(sql.SQL(" AND ").join(conds))
        params.append(window.max_rows + 1)  # 넘쳤는지 알 만큼만
        cols = ("ts", "trade_date", "session", "source", "tr_id", "key")
        cols += ("payload", "payload_text", "lossy")
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute("SET TIME ZONE 'UTC'")
                cur.execute(query, params)
                fetched = cur.fetchall()
        except psycopg.Error as e:
            raise InputError(f"raw_messages 를 읽지 못했다: {type(e).__name__}") from None
        try:
            return [RawRow.model_validate(dict(zip(cols, r, strict=True))) for r in fetched]
        except ValidationError:
            raise InputError("raw_messages 행이 녹화 형식이 아니다") from None


def is_notice_tr(tr_id: str) -> bool:
    return NOTICE_TR.search(tr_id) is not None


def _sanitize_control(text: str, notice: bool, tr_id: str) -> tuple[str, bool]:
    """JSON 제어 프레임 가리기 → (본문, 체결통보인가). 가릴 모양이 아니면 SecretFound."""
    where = f"웹소켓 제어 프레임({tr_id or '?'})"
    try:
        obj = json.loads(text)
    except ValueError:
        raise SecretFound(
            f"{where}이 JSON 이 아니다 — 복호화 키를 가릴 수 없어 옮기지 않는다"
        ) from None
    doc = cast(dict[str, Any], obj)  # '{' 로 시작한 JSON 은 늘 객체
    header, body = doc.get("header"), doc.get("body")
    if (header is not None and not isinstance(header, dict)) or (
        body is not None and not isinstance(body, dict)
    ):
        raise SecretFound(f"{where}의 header·body 가 객체가 아니다 — 가릴 수 없어 옮기지 않는다")
    out = cast(dict[str, Any], body).get("output") if body is not None else None
    if out is not None and not isinstance(out, dict):
        raise SecretFound(f"{where}의 body.output 이 객체가 아니다 — 가릴 수 없어 옮기지 않는다")
    changed = False
    if out is not None:
        secret = cast(dict[str, Any], out)
        for k in WS_SECRET_OUTPUT:
            if k in secret and secret[k] != REDACTED:
                secret[k] = REDACTED
                changed = True
    if header is not None:
        head = cast(dict[str, Any], header)
        frame_tr = head.get("tr_id")
        notice = notice or (isinstance(frame_tr, str) and is_notice_tr(frame_tr))
        if notice and head.get("tr_key") not in (None, "", REDACTED):
            head["tr_key"] = REDACTED
            changed = True
    if not changed:
        return text, notice
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")), notice


def sanitize(row: RawRow) -> RawRow:
    """녹화 골든에 옮기기 전 가리기 — 웹소켓 행만(나머지는 원문 그대로). 가릴 모양이 아니면
    SecretFound(fail closed — 아무것도 쓰지 않는다).

    - 암호화 프레임(`1|TR|건수|…`) 본문 → REDACTED
    - 제어 프레임(JSON) body.output 의 복호화 키·IV → REDACTED. JSON 이 아니거나 header·body·
      body.output 이 객체가 아니면 SecretFound
    - 체결통보 TR(`NOTICE_TR` — 행 tr_id 나 프레임의 TR): 행 key·header.tr_key(HTS ID) → REDACTED,
      평문 데이터 프레임(`0|…`) 본문도 → REDACTED
    - 본문이 JSON 열(payload)인 웹소켓 행은 SecretFound — 수집기는 웹소켓 원문을 텍스트로만 남긴다
    """
    if row.source != "kis_ws":
        return row
    text = row.payload_text
    if text is None:
        raise SecretFound(f"웹소켓 행({row.tr_id or '?'})의 본문이 JSON 열이다 — 가리는 규칙 밖")
    notice = is_notice_tr(row.tr_id)
    if text.startswith(("0|", "1|")):
        parts = text.split("|", 3)
        notice = notice or is_notice_tr(parts[1])
        if len(parts) == 4 and (parts[0] == "1" or notice) and parts[3] != REDACTED:
            text = "|".join([*parts[:3], REDACTED])
    elif text.lstrip().startswith("{"):
        text, notice = _sanitize_control(text, notice, row.tr_id)
    update: dict[str, Any] = {}
    if text != row.payload_text:
        update["payload_text"] = text
    if notice and row.key not in ("", REDACTED):
        update["key"] = REDACTED
    return row.model_copy(update=update) if update else row


# 가린 뒤 본문에 남으면 안 되는 복호화 키·IV 모양 — 값이 REDACTED(또는 빈 문자열)가 아니면 거부
_KEY_IV = re.compile(r'"(?:iv|key)"\s*:\s*"(?!REDACTED"|")')


def unredacted_key_iv(text: str) -> bool:
    """본문에 `"key":"…"`·`"iv":"…"` 가 REDACTED 아닌 값으로 남았나(JSON 공백 표기 포함)."""
    return _KEY_IV.search(text) is not None


def secret_hits(text: str) -> list[str]:
    """비밀 같은 문자열 패턴(`SECRET_PATTERNS`) 중 걸린 것."""
    return [p.pattern for p in SECRET_PATTERNS if p.search(text)]


def _order(row: RawRow) -> tuple[datetime, str, str, str, str]:
    return (row.ts, row.source, row.tr_id, row.key, row.body_text())


def cut_raw(rows: Iterable[RawRow], window: RawWindow) -> list[RawRow]:
    """창 안 행을 가려(`sanitize`) 시각 순으로. 비었거나 상한을 넘거나, 가릴 수 없거나, 비밀 같은
    문자열(`SECRET_PATTERNS`)·REDACTED 아닌 `"key"`·`"iv"` 값(`unredacted_key_iv`, 본문)이 남으면
    InputError(SecretFound) — 그때는 아무것도 쓰지 않는다."""
    picked = sorted((sanitize(r) for r in rows if window.admits(r)), key=_order)
    if not picked:
        raise InputError("창 안에 행이 없다")
    if len(picked) > window.max_rows:
        raise InputError(
            f"창 안 행이 {window.max_rows}개를 넘는다 — 창을 좁히거나 --source·--tr-id 로 거른다"
        )
    for i, r in enumerate(picked):
        hits = secret_hits(r.to_line())
        if unredacted_key_iv(r.body_text()):  # 가리기 규칙이 못 본 자리의 복호화 키·IV
            hits.append('"key"·"iv" 값')
        if hits:
            raise SecretFound(f"{i}번째 행({r.source} {r.tr_id}): 비밀 같은 문자열 {hits}")
    return picked


def _short(e: BaseException) -> str:
    if isinstance(e, ValidationError):
        return str(e.errors(include_input=False, include_url=False))[:300]
    return f"{type(e).__name__}: {e}"[:200]


def _tick_golden(tick: FuturesTick | OptionTick, received: datetime) -> dict[str, Any]:
    d = tick.model_dump(mode="json")
    try:
        d["ts"] = tick_time(tick.hhmmss, received).isoformat()
    except ValueError as e:
        d["ts"] = None
        d["ts_error"] = str(e)[:100]
    return d


def _parse_ws(row: RawRow) -> dict[str, Any]:
    if row.payload_text is None:
        return {"kind": "unexpected_payload"}
    try:
        frame = parse_frame(row.payload_text)
    except WsParseError as e:
        return {"kind": "error", "error": str(e)[:200]}
    if isinstance(frame, WsControl):
        return {
            "kind": "control",
            "frame_tr_id": frame.tr_id,
            "tr_key": frame.tr_key,
            "rt_cd": frame.rt_cd,
            "msg_cd": frame.msg_cd,
            "msg1": frame.msg1,
            "pingpong": frame.is_pingpong,
            "ok": frame.ok,
            "output_keys": sorted(frame.output),  # 값은 싣지 않는다(복호화 키)
        }
    if isinstance(frame, WsEncrypted):
        return {"kind": "encrypted", "frame_tr_id": frame.tr_id, "count": frame.count}
    head: dict[str, Any] = {
        "kind": "data",
        "frame_tr_id": frame.tr_id,
        "count": frame.count,
        "width": frame.width,
        "width_mismatch": frame.width_mismatch,
    }
    if frame.tr_id not in load_fields():
        return head  # 컬럼 설정이 없는 TR — 수집기도 원문만
    try:
        ticks = ticks_from(frame)
    except (WsParseError, ValidationError) as e:
        return head | {"kind": "data_error", "error": _short(e)}
    return head | {"kind": "ticks", "ticks": [_tick_golden(t, row.ts) for t in ticks]}


def _parse_rest(row: RawRow) -> dict[str, Any]:
    body = row.payload
    if body is None:
        return {"kind": "unexpected_payload"}
    head = {k: body.get(k) for k in ("rt_cd", "msg_cd", "msg1")}
    if row.tr_id in RAW_ONLY_TRS:
        return {"kind": "raw_only", **head, "keys": sorted(body)}
    spec = REST_PARSERS.get(row.tr_id)
    if spec is None:
        return {"kind": "unparsed", **head}
    outputs: dict[str, Any] = {}
    for key, model in spec:
        rows, bad = parse_rows(model, output_rows(body, key))
        outputs[key] = {
            "rows": [r.model_dump(mode="json") for r in rows],
            "bad": [{"index": b.index, "error": b.error[:300]} for b in bad],
        }
    return {"kind": "rest", **head, "outputs": outputs}


def parse_message(row: RawRow) -> dict[str, Any]:
    """녹화 한 행의 파서 출력 — 웹소켓은 `kbj.data.private.kis.ws`(프레임 분류·체결 틱·체결 시각
    변환), REST 는 `kbj.data.private.kis.derivatives`(수집기와 같은 모델·행 단위 검증). 메시지
    하나의 예외는 그 메시지만 `parser_exception` 으로 남긴다(격리)."""
    head: dict[str, Any] = {
        "ts": row.ts.astimezone(UTC).isoformat(),
        "source": row.source,
        "tr_id": row.tr_id,
        "key": row.key,
    }
    try:
        if row.source == "kis_ws":
            return head | _parse_ws(row)
        if row.source == "kis_rest":
            return head | _parse_rest(row)
        return head | {
            "kind": "unparsed",
            "why": "KRX 원문 파서 없음(krx 원문은 아직 녹화하지 않는다)",
        }
    except Exception as e:  # 메시지 단위 격리
        return head | {"kind": "parser_exception", "error": _short(e)}


def recording_text(rows: Sequence[RawRow]) -> str:
    return "".join(r.to_line() + "\n" for r in rows)


def raw_regenerate(name: str) -> str:
    return f"uv run python -m kbj.services.recorder.golden raw-parse --name {name} --write"


def raw_snapshot(name: str, rows: Sequence[RawRow], cut: Mapping[str, Any]) -> dict[str, Any]:
    """녹화의 파서 출력 스냅샷(`<name>.parsed.json` 내용)."""
    messages: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for i, r in enumerate(rows):
        m = parse_message(r)
        kind = str(m["kind"])
        counts[kind] = counts.get(kind, 0) + 1
        messages.append({"i": i} | m)
    return {
        "_about": "raw 녹화 골든의 파서 출력 스냅샷(PLAN §6.3) — 비교 "
        "tests/golden/gex/test_raw_golden.py. 손으로 고치지 않는다",
        "_regenerate": raw_regenerate(name),
        "recording": f"{name}.jsonl",
        "sha256": hashlib.sha256(recording_text(rows).encode("utf-8")).hexdigest(),
        "cut": dict(cut),
        "counts": dict(sorted(counts.items())),
        "messages": messages,
    }


_NAME = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")


def _raw_paths(out_dir: Path, name: str) -> tuple[Path, Path]:
    if not _NAME.fullmatch(name):
        raise InputError(f"--name 은 소문자·숫자·밑줄(64자 이하): {name!r}")
    return out_dir / f"{name}.jsonl", out_dir / f"{name}.parsed.json"


def _aware_arg(s: str) -> datetime:
    try:
        t = datetime.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"ISO 시각이 아니다: {s!r}") from e
    if t.tzinfo is None or t.utcoffset() is None:
        raise argparse.ArgumentTypeError(f"시간대가 없다(+09:00·Z 를 붙인다): {s!r}")
    return t


def _main_raw(args: argparse.Namespace) -> int:
    rec_path, parsed_path = _raw_paths(args.out_dir, args.name)
    window = RawWindow(args.start, args.end, tuple(args.source), tuple(args.tr_id), args.max_rows)
    if args.from_jsonl is not None:
        rows, origin = read_jsonl(args.from_jsonl), "jsonl"
    else:
        rows, origin = DbRawSource.from_settings().read(window), "db"
    picked = cut_raw(rows, window)
    snapshot = raw_snapshot(args.name, picked, window.meta(origin))
    check_schema(snapshot)  # 녹화를 쓰기 전에 — 스냅샷을 못 쓰면 둘 다 쓰지 않는다
    rc = _check_or_write(rec_path, recording_text(picked), args.write)
    return max(rc, _check_golden(parsed_path, snapshot, raw_regenerate(args.name), args.write))


def _main_raw_parse(args: argparse.Namespace) -> int:
    names = args.name or sorted(p.stem for p in args.out_dir.glob("*.jsonl"))
    if not names:
        print(f"녹화가 없다: {_rel(args.out_dir)}")
        return 1
    rc = 0
    for name in names:
        rec_path, parsed_path = _raw_paths(args.out_dir, name)
        rows = read_jsonl(rec_path)
        old = _load_json(parsed_path) if parsed_path.exists() else {}
        cut = old.get("cut", {}) if isinstance(old, dict) else {}
        snapshot = raw_snapshot(name, rows, cast(dict[str, Any], cut))
        rc = max(rc, _check_golden(parsed_path, snapshot, raw_regenerate(name), args.write))
    return rc


# ── CLI ──────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="python -m kbj.services.recorder.golden",
        description="raw 녹화 골든 — --write 가 없으면 비교만(다르면 1)",
    )
    sub = ap.add_subparsers(dest="mode", required=True)
    rw = sub.add_parser("raw", help="raw_messages 창 → 녹화 골든 + 파서 출력 스냅샷")
    src = rw.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-jsonl", type=Path, help="raw_messages 내보내기(JSONL — 한 줄에 한 행)")
    src.add_argument(
        "--from-db", action="store_true", help="KBJ_DATABASE_URL 의 prv_gex.raw_messages"
    )
    rw.add_argument("--start", type=_aware_arg, required=True, help="창 시작(포함, ISO+시간대)")
    rw.add_argument("--end", type=_aware_arg, required=True, help="창 끝(제외, ISO+시간대)")
    rw.add_argument("--name", required=True, help="골든 이름(tests/golden/gex/raw/<name>.*)")
    rw.add_argument("--source", action="append", default=[], choices=["kis_ws", "kis_rest", "krx"])
    rw.add_argument("--tr-id", action="append", default=[], help="이 TR 만(반복 가능)")
    rw.add_argument("--max-rows", type=int, default=RAW_MAX_ROWS, help="창 안 행 상한")
    rw.add_argument("--out-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    rw.add_argument("--write", action="store_true", help="파일을 쓴다(없으면 비교만)")
    rp = sub.add_parser("raw-parse", help="있는 녹화 골든의 파서 출력 스냅샷만 다시")
    rp.add_argument("--name", action="append", default=[], help="이 녹화만(없으면 전부)")
    rp.add_argument("--out-dir", type=Path, default=RAW_DIR, help=argparse.SUPPRESS)
    rp.add_argument("--write", action="store_true", help="스냅샷을 다시 쓴다(없으면 비교만)")
    return ap


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.mode == "raw":
            return _main_raw(args)
        if args.mode == "raw-parse":
            return _main_raw_parse(args)
    except GoldenSchemaError as e:
        print(f"골든 스키마 오류: {e}", file=sys.stderr)
        return 2
    except InputError as e:
        print(f"입력 오류: {e}", file=sys.stderr)
        return 2
    raise AssertionError(f"모르는 모드: {args.mode}")  # argparse 가 막는다


__all__ = [
    "RAW_DIR",
    "RAW_MAX_ROWS",
    "REDACTED",
    "REST_PARSERS",
    "SECRET_PATTERNS",
    "DbRawSource",
    "GoldenSchemaError",
    "InputError",
    "RawRow",
    "RawWindow",
    "SecretFound",
    "compare_golden",
    "cut_raw",
    "failure_report",
    "main",
    "parse_message",
    "raw_regenerate",
    "raw_snapshot",
    "read_jsonl",
    "recording_text",
    "sanitize",
    "secret_hits",
    "unredacted_key_iv",
]


if __name__ == "__main__":
    raise SystemExit(main())
