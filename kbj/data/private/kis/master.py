"""KIS 지수선물옵션 마스터(`fo_idx_code_mts.mst`) — 파서와 내려받기(GX PLAN §4.4, #11a).

GEXLAB `data/kis/master.py` 전체 + `services/scheduler/service.py:http_master_downloader`:104 승격.
배포 주소는 이 모듈의 비공개 상수다 — legacy 는 논리 URL 브리지 `kis-master:` 로만 받는다(설계
§3.7).

한 줄 = `|` 로 나뉜 9필드 (cp949):

    종류|단축코드|표준코드|이름|ATM구분|행사가|(선물 순번)|기초자산코드|기초자산명
    5|B01610A51|KR4B016AA511|C 202610 1,125.0|1|01125.00| |2001|KOSPI200

- 다섯째 필드는 콜/풋이 아니라 **ATM 구분**(1 ATM · 2 ITM · 3 OTM, 선물·스프레드는 공백). 2026-09-28
  에 이를 콜/풋으로 읽어 ATM 행사가를 버린 적이 있다(GX scripts/probe_chain_fill.py).
- 콜/풋은 이름에서 만기 토큰 바로 앞 토큰의 끝 글자(C·P)로 읽는다 (`C`, `미니C`, `위클리M C`,
  `코스닥150C`).
- 행사가는 여섯째 필드(`01125.00`). 0 이면 옵션이 아니다.
- 마스터에는 상장 행사가가 전부 있다 — 2026-09-28 파일 기준 월물 202610 341개, 위클리(월) 2609W4
  111개.
- 내려받기는 접속·읽기 한 번마다 시간 제한, 전체는 조각 사이에서 `total_s` 로 끊고(TimeoutError),
  크기 상한을 넘으면 버린다(ValueError). HTTP 오류는 `httpx.HTTPStatusError`. KIS 앱키 한도와는
  무관한 배포 서버라 레이트리미터를 타지 않는다.
"""

from __future__ import annotations

import io
import re
import time
import zipfile
from collections.abc import Callable, Iterable
from decimal import Decimal, InvalidOperation
from typing import Final, Literal

import httpx
from pydantic import BaseModel, ConfigDict

from kbj.data.http import make_client

# 내려받기 — 확인 필요: KIS 배포 서버 응답·파일 크기 미실측 — 보수적으로(GX 값 그대로)
MASTER_CONNECT_S: Final = 5.0
MASTER_READ_S: Final = 15.0
MASTER_TOTAL_S: Final = 30.0
MASTER_MAX_BYTES: Final = 32 << 20
FO_MASTER_FILE: Final = "fo_idx_code_mts.mst.zip"
_MASTER_BASE: Final = "https://new.real.download.dws.co.kr"
_MASTER_DIR: Final = "/common/master/"

MasterDownloader = Callable[[], bytes]

Family = Literal[
    "kospi200",
    "mini_kospi200",
    "kospi200_weekly_thu",
    "kospi200_weekly_mon",
    "kosdaq150",
    "kosdaq150_weekly_thu",
    "kosdaq150_weekly_mon",
    "usd",
    "other",
]

# 이름 앞부분(콜/풋·선물 글자를 뗀 것) → 상품군. 마스터 표기 (2026-09-28 파일)
_MASTER_PREFIX: dict[str, Family] = {
    "": "kospi200",  # 'C 202610 1,125.0', 'F 202612'
    "미니": "mini_kospi200",
    "위클리": "kospi200_weekly_thu",  # 종류 L·M — 월물리스트 WKI (261001 = 2610W1)
    "위클리M": "kospi200_weekly_mon",  # 종류 N·O — 월물리스트 WKM (260904 = 2609W4)
    "코스닥150": "kosdaq150",
    "코스닥위클리": "kosdaq150_weekly_thu",
    "코스닥위클리M": "kosdaq150_weekly_mon",
}

# 월물리스트·전광판 시장분류(`FID_COND_MRKT_CLS_CODE`) → 상품군. 2026-09-28 전광판 WKM:260904
# 코드(BAFBZW…)·WKI:261001 ATM 코드(B09FFWA51)를 마스터와 대조해 확인
MRKT_CLS_FAMILY: dict[str, Family] = {
    "": "kospi200",
    "WKM": "kospi200_weekly_mon",
    "WKI": "kospi200_weekly_thu",
}

_EXPIRY_TOKEN = re.compile(r"\d{6}|\d{4}W\d")

CallPut = Literal["C", "P", ""]
Moneyness = Literal["1", "2", "3", ""]


def expiry_code(token: str) -> str:
    """만기 표기를 전광판 `FID_MTRT_CNT` 6자리로 (#12).

    월물 `202610` 은 그대로, 위클리 `2609W4` → `260904`.
    """
    if re.fullmatch(r"\d{6}", token):
        return token
    m = re.fullmatch(r"(\d{4})W(\d)", token)
    if m is None:
        raise ValueError(f"만기 표기가 아니다: {token!r}")
    return f"{m.group(1)}0{m.group(2)}"


def _split_name(name: str) -> tuple[str, str, str | None]:
    """이름 → (상품 접두어, 종류 글자 C·P·F 등, 만기 토큰). 만기 토큰이 없으면 (이름, '', None)."""
    toks = name.split()
    for i, t in enumerate(toks):
        if i > 0 and _EXPIRY_TOKEN.fullmatch(t):
            head = toks[:i]
            letter = head[-1][-1]
            prefix = " ".join([*head[:-1], head[-1][:-1]]).strip()
            return prefix, letter, t
    return name, "", None


class MasterRow(BaseModel):
    """마스터 한 줄. 옵션이면 `cp` 가 'C'·'P', `strike` 가 채워진다."""

    model_config = ConfigDict(frozen=True)

    kind: str  # 첫 필드 (1 선물, 5·6 월물 콜·풋, L·M 위클리(목), N·O 위클리(월) …)
    code: str  # 단축코드 — 단건 현재가 `FID_INPUT_ISCD`
    isin: str
    name: str
    cp: CallPut
    strike: Decimal | None
    moneyness: Moneyness  # 1 ATM · 2 ITM · 3 OTM (마스터 작성 시점 기준)
    underlying: str  # 기초자산명 (KOSPI200, KSQ150 …)

    @property
    def is_option(self) -> bool:
        return self.strike is not None

    @property
    def series(self) -> str:
        """이름에서 행사가를 뗀 부분. 같은 만기·같은 콜/풋이면 같다.

        예: 'C 202610', '위클리M P 2609W4'.
        """
        if self.strike is None:
            return self.name
        return self.name.rsplit(maxsplit=1)[0].strip()

    @property
    def expiry_token(self) -> str | None:
        """이름의 만기 표기 ('202610', '2609W4'). 스프레드 등은 None."""
        return _split_name(self.name)[2]

    @property
    def expiry(self) -> str | None:
        """KIS 6자리 만기 ('202610', '260904')."""
        t = self.expiry_token
        return expiry_code(t) if t is not None else None

    @property
    def family(self) -> Family:
        prefix, letter, token = _split_name(self.name)
        if token is None or letter not in ("C", "P", "F"):
            return "other"
        return _MASTER_PREFIX.get(prefix, "other")


def parse_master_line(line: str) -> MasterRow | None:
    """한 줄 파싱. 빈 줄은 None, 형식이 틀리면 ValueError."""
    if not line.strip():
        return None
    f = line.split("|")
    if len(f) < 9 or not f[1].strip():
        raise ValueError(f"마스터 줄 형식이 아니다: {line[:80]!r}")
    name = f[3].strip()
    try:
        raw_strike = Decimal(f[5].strip() or "0")
    except InvalidOperation as e:
        raise ValueError(f"행사가가 숫자가 아니다: {f[5]!r}") from e
    _, letter, token = _split_name(name)
    is_option = token is not None and letter in ("C", "P") and raw_strike > 0
    return MasterRow.model_validate(
        {
            "kind": f[0].strip(),
            "code": f[1].strip(),
            "isin": f[2].strip(),
            "name": name,
            "cp": letter if is_option else "",
            "strike": raw_strike if is_option else None,
            "moneyness": f[4].strip(),
            "underlying": f[8].strip(),
        }
    )


def parse_master(text: str) -> list[MasterRow]:
    """마스터 전체. 한 줄이라도 형식이 틀리면 ValueError (형식 변경을 조용히 넘기지 않는다 —
    호출자는 직전 마스터를 계속 쓴다)."""
    out: list[MasterRow] = []
    for n, line in enumerate(text.splitlines(), start=1):
        try:
            row = parse_master_line(line)
        except ValueError as e:
            raise ValueError(f"{n}번째 줄: {e}") from e
        if row is not None:
            out.append(row)
    return out


def master_text_from_zip(data: bytes) -> str:
    """KIS 가 배포하는 zip(안에 `.mst` 한 개, cp949) → 글. zip·인코딩이 틀리면 ValueError."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if len(names) != 1:
                raise ValueError(f"zip 안 파일이 한 개가 아니다: {names}")
            return z.read(names[0]).decode("cp949")
    except zipfile.BadZipFile as e:
        raise ValueError(f"zip 이 아니다: {e}") from e


def parse_master_zip(data: bytes) -> list[MasterRow]:
    """KIS 가 배포하는 zip(안에 `.mst` 한 개, cp949)."""
    return parse_master(master_text_from_zip(data))


def rows_by_series(rows: Iterable[MasterRow]) -> dict[str, list[MasterRow]]:
    """옵션만, 시리즈별로 행사가 오름차순."""
    out: dict[str, list[MasterRow]] = {}
    for r in rows:
        if r.strike is not None:
            out.setdefault(r.series, []).append(r)
    for rs in out.values():
        rs.sort(key=lambda m: m.strike or Decimal(0))
    return out


def series_of_codes(rows: Iterable[MasterRow], codes: Iterable[str]) -> list[str]:
    """주어진 종목코드(예: 전광판이 준 코드)가 속한 옵션 시리즈 이름들 (정렬).

    모르는 코드는 건너뛴다.
    """
    by_code = {r.code: r for r in rows if r.strike is not None}
    return sorted({by_code[c].series for c in codes if c in by_code})


def series_for(
    rows: Iterable[MasterRow], family: Family, expiry: str, cp: Literal["C", "P"]
) -> list[MasterRow]:
    """상품군·6자리 만기·콜/풋으로 고른 옵션 전 행사가 (오름차순). 전광판에 안 온 행사가 보강용."""
    out = [
        r
        for r in rows
        if r.strike is not None and r.cp == cp and r.family == family and r.expiry == expiry
    ]
    return sorted(out, key=lambda m: m.strike or Decimal(0))


# ---- 내려받기 -----------------------------------------------------------------------------------


def http_master_downloader(
    *,
    connect_s: float = MASTER_CONNECT_S,
    read_s: float = MASTER_READ_S,
    total_s: float = MASTER_TOTAL_S,
    max_bytes: int = MASTER_MAX_BYTES,
    transport: httpx.BaseTransport | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> MasterDownloader:
    """KIS 마스터 zip 을 받는 함수(GX 그대로 — legacy GX scheduler 가 이 모양을 쓴다).

    접속·읽기 한 번마다 시간 제한, 전체는 조각 사이에서 total_s 로 끊는다(TimeoutError) — 느리게
    흘러와도 끝이 있다. 크기가 max_bytes 를 넘으면 ValueError. 시험은 transport(가짜)를 넣는다.
    """

    def get() -> bytes:
        deadline = clock() + total_s
        buf = bytearray()
        with (
            make_client(
                _MASTER_BASE, connect_s=connect_s, read_s=read_s, transport=transport
            ) as client,
            client.stream("GET", _MASTER_DIR + FO_MASTER_FILE) as r,
        ):
            r.raise_for_status()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > max_bytes:
                    raise ValueError(f"마스터가 {max_bytes}B 보다 크다")
                if clock() > deadline:
                    raise TimeoutError(f"마스터 내려받기 {total_s:g}초 초과")
        return bytes(buf)

    return get


def download_fo_master(
    *,
    transport: httpx.BaseTransport | None = None,
    connect_s: float = MASTER_CONNECT_S,
    read_s: float = MASTER_READ_S,
    total_s: float = MASTER_TOTAL_S,
    max_bytes: int = MASTER_MAX_BYTES,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """지수선물옵션 마스터 zip 한 번 받기(오류는 `http_master_downloader` 와 같다)."""
    return http_master_downloader(
        connect_s=connect_s,
        read_s=read_s,
        total_s=total_s,
        max_bytes=max_bytes,
        transport=transport,
        clock=clock,
    )()
