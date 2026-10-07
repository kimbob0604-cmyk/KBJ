"""DART 고유번호(corpCode.xml) 파서 — 공개 등급(DATA_TIERS §1).

승격 원본: ETF-Traker `board/ingest/dart.py:corp_codes`:75(zip → `{종목코드: corp_code}`),
dart-report `client.corp_code`. 내려받기는 `DartClient.corp_codes()`, 저장은
`pub_filings.corp_code`(작업 `filings.corp_code` — 묶음 F, 매일 갈아 넣기).

- zip 안 XML 하나(`CORPCODE.xml`)를 defusedxml 로 연다 — 외부 XML 을 표준 파서로 열지 않는다.
- 압축을 풀기 전에 크기를 본다(`max_xml_bytes`) — 큰 파일·압축 폭탄으로 메모리를 다 쓰지 않게.
- 형식이 어긋나면(고유번호 8자리 아님·종목코드 6자 아님·고유번호 중복·빈 목록) `CorpCodeFormatError`
  — 일부를 버리고 조용히 넘어가지 않는다(절대 규칙 4).
- 종목코드는 앞자리 0 을 지킨 문자열이다. 상장되지 않은 회사는 공백(`" "`)으로 온다 → None.
  KRX 새 종목코드는 영문을 섞을 수 있어(`0001A0` 꼴) 숫자·대문자 6자를 받는다.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Iterable
from datetime import date, datetime
from typing import Final

from defusedxml.ElementTree import fromstring as _xml_fromstring
from pydantic import BaseModel, ConfigDict

MAX_XML_BYTES: Final = 128 * 1024 * 1024
_CORP = re.compile(r"[0-9]{8}")
_STOCK = re.compile(r"[0-9A-Z]{6}")


class CorpCodeFormatError(ValueError):
    """corpCode.xml 모양이 기대와 다르다."""


class CorpCode(BaseModel):
    """회사 하나. `source` 는 늘 DART."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    corp_code: str  # 8자리
    stock_code: str | None  # 상장사만 6자
    corp_name: str
    modify_date: date | None
    source: str = "DART"


def _ymd(raw: str) -> date | None:
    s = raw.strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()  # noqa: DTZ007 — 날짜만 쓴다
    except ValueError:
        raise CorpCodeFormatError(f"modify_date 형식이 틀렸다: {s[:12]!r}") from None


def corp_xml_from_zip(zip_bytes: bytes, *, max_xml_bytes: int = MAX_XML_BYTES) -> bytes:
    """zip 에서 XML 하나를 꺼낸다. zip 이 아니거나 비었거나 너무 크면 `CorpCodeFormatError`."""
    if not zip_bytes.startswith(b"PK"):
        raise CorpCodeFormatError("zip 이 아니다")
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise CorpCodeFormatError("zip 을 열 수 없다") from None
    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) != 1:
            raise CorpCodeFormatError(f"zip 안 파일이 하나가 아니다({len(infos)}개)")
        info = infos[0]
        if info.file_size > max_xml_bytes:
            raise CorpCodeFormatError(f"XML 이 {max_xml_bytes}B 보다 크다")
        with zf.open(info) as fh:
            data = fh.read(max_xml_bytes + 1)
        if len(data) > max_xml_bytes:
            raise CorpCodeFormatError(f"XML 이 {max_xml_bytes}B 보다 크다")
        return data


def parse_corp_xml(xml: bytes) -> list[CorpCode]:
    """`<result><list><corp_code/><corp_name/><stock_code/><modify_date/></list>…</result>`."""
    try:
        root = _xml_fromstring(xml)
    except Exception as e:  # defusedxml 의 금지 구성·문법 오류 — 사유를 담아 올린다
        raise CorpCodeFormatError(f"XML 을 읽을 수 없다({type(e).__name__})") from None
    out: list[CorpCode] = []
    seen: set[str] = set()
    for el in root.iter("list"):
        corp = (el.findtext("corp_code") or "").strip()
        if not _CORP.fullmatch(corp):
            raise CorpCodeFormatError(f"corp_code 가 8자리 숫자가 아니다: {corp[:12]!r}")
        if corp in seen:
            raise CorpCodeFormatError(f"corp_code 가 겹친다: {corp}")
        seen.add(corp)
        stock = (el.findtext("stock_code") or "").strip() or None
        if stock is not None and not _STOCK.fullmatch(stock):
            raise CorpCodeFormatError(f"{corp} stock_code 형식이 틀렸다: {stock[:12]!r}")
        out.append(
            CorpCode(
                corp_code=corp,
                stock_code=stock,
                corp_name=(el.findtext("corp_name") or "").strip(),
                modify_date=_ymd(el.findtext("modify_date") or ""),
            )
        )
    if not out:
        raise CorpCodeFormatError("corpCode 에 회사가 없다")
    return out


def parse_corp_codes(zip_bytes: bytes, *, max_xml_bytes: int = MAX_XML_BYTES) -> list[CorpCode]:
    """corpCode.xml zip → 회사 목록(상장·비상장 전부)."""
    return parse_corp_xml(corp_xml_from_zip(zip_bytes, max_xml_bytes=max_xml_bytes))


def listed_map(codes: Iterable[CorpCode]) -> dict[str, str]:
    """`{종목코드: corp_code}` — 상장사만(ET `corp_codes` 반환 모양). 상장사가 없으면 실패.

    한 종목코드가 두 회사에 걸려 있으면(코드 재사용 [실측 필요]) 최근 `modify_date` 쪽을 쓴다.
    날짜로 가를 수 없으면 `CorpCodeFormatError`(어느 회사인지 짐작하지 않는다).
    """
    best: dict[str, CorpCode] = {}
    for c in codes:
        if c.stock_code is None:
            continue
        prev = best.get(c.stock_code)
        if prev is None:
            best[c.stock_code] = c
            continue
        a, b = prev.modify_date, c.modify_date
        if a is None or b is None or a == b:
            raise CorpCodeFormatError(f"종목코드 {c.stock_code} 가 두 회사에 있다(날짜로 못 가름)")
        if b > a:
            best[c.stock_code] = c
    if not best:
        raise CorpCodeFormatError("corpCode 에 상장 종목이 없다")
    return {k: v.corp_code for k, v in best.items()}
