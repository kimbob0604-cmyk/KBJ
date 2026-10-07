"""KIS 자격 — 설정에서 읽기, 주소(real·vts), GX 와 같은 owner, 값이 새지 않음(설계 §1.2)."""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import SecretStr, ValidationError

from kbj.config.settings import Settings
from kbj.data.private.kis.credentials import KisCredentials, base_url, token_owner
from kbj.data.private.kis.errors import TokenUnavailable

APP_KEY = "PSappKEY0123456789abcdef"
APP_SECRET = "SECRETvalue9876543210zyx"


def settings(**kw: object) -> Settings:
    return Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        kis_app_key=SecretStr(APP_KEY),
        kis_app_secret=SecretStr(APP_SECRET),
        **kw,  # pyright: ignore[reportArgumentType]
    )


def test_from_settings_reads_key_secret_and_env() -> None:
    c = KisCredentials.from_settings(settings())
    assert c.app_key.get_secret_value() == APP_KEY
    assert c.app_secret.get_secret_value() == APP_SECRET
    assert c.env == "real"
    v = KisCredentials.from_settings(settings(kis_env="vts"))
    assert v.env == "vts" and v.base_url != c.base_url
    assert c.secrets() == [APP_KEY, APP_SECRET]


@pytest.mark.parametrize(
    "missing", [{"kis_app_key": None}, {"kis_app_secret": None}, {"kis_app_key": ""}]
)
def test_missing_key_or_secret_is_token_unavailable(missing: dict[str, object]) -> None:
    s = Settings(
        _env_file=None,  # pyright: ignore[reportCallIssue]
        **{"kis_app_key": SecretStr(APP_KEY), "kis_app_secret": SecretStr(APP_SECRET), **missing},  # pyright: ignore[reportArgumentType]
    )
    with pytest.raises(TokenUnavailable, match="KBJ_KIS_APP_KEY"):
        KisCredentials.from_settings(s)


def test_base_urls_are_https_kis_hosts() -> None:
    real, vts = base_url("real"), base_url("vts")
    assert real.startswith("https://openapi.") and real.endswith(":9443")
    assert vts.startswith("https://openapivts.") and vts.endswith(":29443")
    with pytest.raises(ValueError, match="real"):
        base_url("paper")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]


def test_owner_is_the_gexlab_formula() -> None:
    c = KisCredentials.from_settings(settings())
    expected = hashlib.sha256(f"{c.base_url}\n{APP_KEY}".encode()).hexdigest()[:16]
    assert c.owner == expected == token_owner(c.base_url, APP_KEY)
    other_env = KisCredentials.from_settings(settings(kis_env="vts"))
    assert other_env.owner != c.owner  # 같은 앱키라도 real·vts 토큰은 섞지 않는다
    assert APP_KEY not in c.owner


def test_values_never_in_repr_or_dump() -> None:
    c = KisCredentials.from_settings(settings())
    text = repr(c) + str(c) + json.dumps(c.model_dump(mode="json"))
    assert APP_KEY not in text and APP_SECRET not in text


def test_credentials_are_frozen_and_strict() -> None:
    c = KisCredentials.from_settings(settings())
    with pytest.raises(ValidationError):
        c.env = "vts"  # type: ignore[misc]  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(ValidationError):
        KisCredentials(app_key=SecretStr("k"), app_secret=SecretStr("s"), env="paper")  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
