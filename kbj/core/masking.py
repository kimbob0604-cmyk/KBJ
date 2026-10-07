"""키·토큰·계좌번호 마스킹 공용 함수 (CLAUDE.md 절대 규칙 5).

로그·화면·오류 문구·알림·응답에 문자열을 내보내기 전에 여기를 거친다. 원칙:

- 가린 자리에는 `MASK`(``***``)만 남는다. 앞자리·뒷자리·길이를 드러내지 않는다
  (``PS12…`` 처럼 앞 몇 자를 보여 주는 방식도 쓰지 않는다).
- 아는 값(설정의 비밀값)은 `redact` 로 통째로 바꾼다. 긴 값부터 — 한 값이 다른 값의 일부여도
  조각이 남지 않게(GEXLAB `services/auth` 의 교훈).
- 값을 몰라도 형태로 알아볼 수 있는 것(봇 토큰·JWT·앱키 형태·계좌번호·``key=value`` 꼴)은
  `redact_patterns` 가 가린다. `mask_text` 는 둘을 차례로 한다.
- 길이를 자를 때는 반드시 가린 뒤에 자른다(`safe_snippet`) — 자른 뒤 가리면 경계에 걸친
  앞부분이 샌다.
  남이 이미 잘라 준 문구의 끝에 걸친 비밀값 앞조각은 `hide_cut_tail` 이 가린다.

`SECRET_SHAPES` 는 비밀값의 형태 목록이다. scripts/check_public_safety.py 도 같은 목록을
쓴다(한 벌).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from pydantic import SecretStr

MASK: Final = "***"
# 잘린 꼬리를 비밀값 조각으로 볼 최소 길이(GEXLAB services/auth 와 같은 값)
CUT_TAIL_MIN: Final = 3

type SecretLike = str | SecretStr | None

# 비밀값의 형태. 이름은 공개 안전 검사 보고서의 규칙 이름으로도 쓰인다.
# 찾은 값 자체는 어디에도 출력하지 않는다.
SECRET_SHAPES: Final[Mapping[str, re.Pattern[str]]] = MappingProxyType(
    {
        # 텔레그램 봇 토큰 `<봇 번호>:<35자>` — URL 경로(`/bot<토큰>/`) 안에서도 잡히게
        # 앞은 숫자 경계만 본다
        "telegram_bot_token": re.compile(r"(?<![0-9])[0-9]{6,12}:[A-Za-z0-9_-]{30,}"),
        "anthropic_key": re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
        "github_token": re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{22,})"
        ),
        "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
        "aws_secret_access_key": re.compile(
            r"(?i)aws_secret_access_key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+]{40}"
        ),
        # JWT(KIS 접근토큰 포함): 헤더·본문 모두 JSON 이라 base64url 이 eyJ 로 시작한다
        "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
        "private_key_pem": re.compile(r"-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-{5}"),
        # KIS 앱키: PS 로 시작하는 36자. 앱시크릿: base64 180자
        "kis_app_key": re.compile(r"\bPS[A-Za-z0-9]{34}\b"),
        "kis_app_secret": re.compile(
            r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{178,180}={0,2}(?![A-Za-z0-9+/=])"
        ),
        # 계좌번호: 8자리-2자리(KIS 종합계좌번호-상품코드)
        "account_number": re.compile(r"(?<![0-9-])[0-9]{8}-[0-9]{2}(?![0-9-])"),
    }
)

# PEM 은 머리줄만이 아니라 블록 전체를 가린다(끝 줄이 없으면 문자열 끝까지)
_PEM_BLOCK = re.compile(
    r"-{5}BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-{5}[\s\S]*?(?:-{5}END (?:[A-Z0-9]+ )*PRIVATE KEY-{5}|\Z)"
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
# `이름=값`·`"이름": "값"`·`이름: 값` 꼴에서 이름이 비밀 이름이면 값을 가린다
_MASKED_FIELD_NAMES = (
    r"app_?key|app_?secret|secret_?key|access_?token|refresh_?token|approval_?key|token|"
    r"password|passwd|pwd|secret|client_?secret|api_?key|service_?key|crtfc_key|"
    r"aws_secret_access_key|cano|acnt_prdt_cd|account(?:_?no)?"
)
_KEY_VALUE = re.compile(
    rf"(?i)(?P<head>(?<![A-Za-z0-9_])(?:[A-Za-z0-9]+_)*(?:{_MASKED_FIELD_NAMES})[\"']?\s*[:=]\s*[\"']?)"
    r"(?P<value>[^\s\"'&,;}<>]+)"
)


def _plain(value: SecretLike) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value


def mask(value: SecretLike) -> str:
    """값 하나를 통째로 가린다. 값이 있으면 `MASK`, 없으면(None·빈 문자열) 빈 문자열.

    앞자리·길이를 드러내지 않는다 — 있는지 없는지만 알 수 있다.
    """
    return MASK if _plain(value) else ""


def redact(text: str, secrets: Iterable[SecretLike]) -> str:
    """text 안의 아는 비밀값을 모두 `MASK` 로 바꾼다. 긴 값부터 바꿔 조각이 남지 않게 한다."""
    values = {v for v in (_plain(s) for s in secrets) if v}
    for v in sorted(values, key=len, reverse=True):
        text = text.replace(v, MASK)
    return text


def hide_cut_tail(text: str, secrets: Iterable[SecretLike], min_len: int = CUT_TAIL_MIN) -> str:
    """text 끝이 어떤 비밀값의 앞부분(`min_len` 자 이상)이면 그 꼬리를 `MASK` 로 바꾼다.

    남이 이미 잘라서 준 문구(예: 발급자 오류 응답)는 비밀값이 통째로 들어 있지 않아 `redact` 로
    못 가린다 — 그 잘린 앞조각용. 값 전체가 들어 있는 경우는 `redact` 가 먼저 처리한다.
    """
    cut = 0
    for v in (_plain(s) for s in secrets):
        for j in range(min(len(text), len(v) - 1), cut, -1):
            if text.endswith(v[:j]):
                cut = j
                break
    return text[:-cut] + MASK if cut >= min_len else text


def redact_patterns(text: str) -> str:
    """값을 몰라도 형태로 알아볼 수 있는 비밀을 가린다(`SECRET_SHAPES`·Bearer·비밀 이름의 값)."""
    text = _PEM_BLOCK.sub(MASK, text)
    text = _BEARER.sub(lambda m: m.group(1) + MASK, text)
    for name, pattern in SECRET_SHAPES.items():
        if name != "private_key_pem":
            text = pattern.sub(MASK, text)
    return _KEY_VALUE.sub(
        lambda m: m.group("head") + (m.group("value") if m.group("value") == MASK else MASK),
        text,
    )


def mask_text(text: str, secrets: Iterable[SecretLike] = ()) -> str:
    """아는 값(`redact`)을 먼저, 그다음 형태(`redact_patterns`)로 가린다."""
    return redact_patterns(redact(text, secrets))


def safe_snippet(text: str, limit: int, secrets: Iterable[SecretLike] = ()) -> str:
    """가린 뒤에 `limit` 자로 자른다(잘렸으면 끝에 ``…``).

    오류 문구·응답 본문을 로그에 남길 때 쓴다.
    """
    if limit < 1:
        raise ValueError("limit 는 1 이상이어야 한다")
    masked = mask_text(text, secrets)
    return masked if len(masked) <= limit else masked[: limit - 1] + "…"
