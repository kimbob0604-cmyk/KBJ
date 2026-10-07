"""kbj.core.masking — 키·토큰·계좌번호 마스킹 (CLAUDE.md 절대 규칙 5).

시험 값은 전부 합성이고, 실행할 때 이어 붙여 만든다 — 이 파일 자체가 공개 안전 검사
(scripts/check_public_safety.py)에 걸리지 않게 하려는 것이다.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr

from kbj.core.masking import (
    MASK,
    SECRET_SHAPES,
    hide_cut_tail,
    mask,
    mask_text,
    redact,
    redact_patterns,
    safe_snippet,
)

# ── 합성 비밀값 (형태만 같다) ───────────────────────────────────────────────────────────
TG_TOKEN = "7012345" + "678:" + "AAF" + "q7Zx2" * 6 + "w9"  # 봇 번호 10자리 : 35자
JWT = ".".join(["eyJ" + "hbGciOiJSUzI1NiJ9" * 2, "eyJ" + "zdWIiOiJ0ZXN0In0" * 2, "Sig_n-" * 6])
KIS_APP_KEY = "P" + "S" + "k7Qm2" * 6 + "Zx9a"  # 36자
KIS_APP_SECRET = "Qm9" * 60  # base64 180자
ACCOUNT = "5012" + "3456-" + "01"
ANTHROPIC = "sk-" + "ant-" + "api03-" + "x7Y" * 10
GITHUB = "gh" + "p_" + "aB3dE" * 8
AWS_ID = "AK" + "IA" + "Q7Z2" * 4
PEM = (
    "-" * 5
    + "BEGIN RSA PRIVATE KEY"
    + "-" * 5
    + "\nMIIEow\nabc\n"
    + "-" * 5
    + "END RSA PRIVATE KEY"
    + "-" * 5
)

SHAPED = {
    "telegram_bot_token": TG_TOKEN,
    "jwt": JWT,
    "kis_app_key": KIS_APP_KEY,
    "kis_app_secret": KIS_APP_SECRET,
    "account_number": ACCOUNT,
    "anthropic_key": ANTHROPIC,
    "github_token": GITHUB,
    "aws_access_key_id": AWS_ID,
}


class TestMask:
    @pytest.mark.parametrize("value", ["a", KIS_APP_KEY, KIS_APP_SECRET, SecretStr(ACCOUNT)])
    def test_whole_value_is_hidden_without_prefix_or_length(self, value: str | SecretStr) -> None:
        assert mask(value) == MASK  # 길이와 무관하게 같은 결과 — 앞자리·길이를 드러내지 않는다

    @pytest.mark.parametrize("value", [None, "", SecretStr("")])
    def test_missing_value_is_empty(self, value: str | SecretStr | None) -> None:
        assert mask(value) == ""


class TestRedact:
    def test_known_values_are_replaced(self) -> None:
        text = f"appkey {KIS_APP_KEY} 계좌 {ACCOUNT} 끝"
        assert redact(text, [KIS_APP_KEY, SecretStr(ACCOUNT)]) == f"appkey {MASK} 계좌 {MASK} 끝"

    def test_longest_first_leaves_no_fragment(self) -> None:
        short, long_ = "Zx9a", KIS_APP_KEY  # short 는 long_ 의 끝부분
        out = redact(f"[{long_}] [{short}]", [short, long_])
        assert out == f"[{MASK}] [{MASK}]"

    def test_empty_and_none_secrets_are_ignored(self) -> None:
        assert redact("변화 없음", ["", None, SecretStr("")]) == "변화 없음"

    @given(
        secret=st.text(alphabet="abcXYZ019", min_size=4, max_size=40),
        left=st.text(alphabet="abcXYZ019 -:", max_size=40),
        right=st.text(alphabet="abcXYZ019 -:", max_size=40),
    )
    def test_property_secret_never_survives(self, secret: str, left: str, right: str) -> None:
        out = redact(left + secret + right + secret, [secret])
        assert secret not in out


class TestHideCutTail:
    def test_cut_prefix_at_end_is_hidden(self) -> None:
        text = "발급 실패: appsecret=" + KIS_APP_SECRET[:20]
        assert hide_cut_tail(text, [KIS_APP_SECRET]) == "발급 실패: appsecret=" + MASK

    def test_too_short_tail_is_kept(self) -> None:
        text = "끝 " + KIS_APP_KEY[:2]
        assert hide_cut_tail(text, [KIS_APP_KEY]) == text

    def test_no_secret_no_change(self) -> None:
        assert hide_cut_tail("평범한 문장", [None, ""]) == "평범한 문장"


class TestRedactPatterns:
    @pytest.mark.parametrize("rule", sorted(SHAPED))
    def test_each_shape_is_masked_without_any_prefix(self, rule: str) -> None:
        secret = SHAPED[rule]
        assert SECRET_SHAPES[rule].search(secret), f"합성 값이 {rule} 형태가 아니다"
        out = redact_patterns(f"오류 내용: {secret} (재시도)")
        assert MASK in out
        assert secret[:6] not in out
        assert secret[-6:] not in out

    def test_telegram_token_inside_url_path(self) -> None:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        out = redact_patterns(url)
        assert TG_TOKEN not in out and out.endswith(f"/bot{MASK}/sendMessage")

    def test_pem_block_is_masked_whole(self) -> None:
        out = redact_patterns(f"키:\n{PEM}\n끝")
        assert out == f"키:\n{MASK}\n끝"

    def test_bearer_header(self) -> None:
        out = redact_patterns("Authorization: Bearer abc.DEF-ghi_123")
        assert out == f"Authorization: Bearer {MASK}"

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("appkey=abc123&appsecret=def456", f"appkey={MASK}&appsecret={MASK}"),
            ('{"access_token": "tok.en-1", "n": 3}', f'{{"access_token": "{MASK}", "n": 3}}'),
            ("CANO=50123456&ACNT_PRDT_CD=01", f"CANO={MASK}&ACNT_PRDT_CD={MASK}"),
            ("KBJ_TELEGRAM_BOT_TOKEN=xyz", f"KBJ_TELEGRAM_BOT_TOKEN={MASK}"),
            ("crtfc_key=abcdef&corp_code=00126380", f"crtfc_key={MASK}&corp_code=00126380"),
            ("password: hunter2", f"password: {MASK}"),
        ],
    )
    def test_secret_names_mask_their_values(self, text: str, expected: str) -> None:
        assert redact_patterns(text) == expected

    @pytest.mark.parametrize(
        "text",
        [
            "2026-10-06 15:30:00 KOSPI 2,612.35 (+0.8%) 거래대금 12,345억",
            "max_tokens=1000 token_count=3 tokens: 5",
            "trade_date=20261006 run=20261006-153000 code=005930",
            "전화 02-1234-5678, 사업자 123-45-67890",
            "sha256=" + "0123456789abcdef" * 4,
        ],
    )
    def test_ordinary_text_is_untouched(self, text: str) -> None:
        assert redact_patterns(text) == text


class TestMaskText:
    def test_known_values_then_shapes(self) -> None:
        custom = "my-ecos-key-zz9"  # 형태로는 알 수 없는 키 — 아는 값으로만 가린다
        text = f"ecos {custom} / kis {KIS_APP_KEY} / acct {ACCOUNT}"
        out = mask_text(text, [SecretStr(custom)])
        assert out == f"ecos {MASK} / kis {MASK} / acct {MASK}"

    def test_default_has_no_known_values(self) -> None:
        assert mask_text(f"x {JWT}") == f"x {MASK}"


class TestSafeSnippet:
    def test_masks_before_cutting(self) -> None:
        secret = "s3cr3tVALUEzz"
        text = "x" * 10 + secret + " 뒤"
        out = safe_snippet(text, 12, [secret])
        assert len(out) == 12 and out.endswith("…")
        assert "s3" not in out  # 자른 뒤 가렸다면 경계에 걸친 앞부분이 샌다

    def test_short_text_is_not_cut(self) -> None:
        assert safe_snippet("짧다", 10) == "짧다"

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            safe_snippet("x", 0)
