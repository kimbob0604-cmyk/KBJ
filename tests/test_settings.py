"""kbj.config.settings — 환경을 주입해서만 시험한다.

실제 `.env` 는 읽지 않는다(`_env_file=None`, 현재 디렉터리는 빈 임시 폴더).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import get_args

import pytest
from pydantic import SecretStr, ValidationError

from kbj.config.settings import _SECRET_FIELDS, Settings
from kbj.core.masking import MASK, redact

ROOT = Path(__file__).resolve().parent.parent
FAKE = "fake-" + "value-0123"  # 합성 값


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """바깥 환경의 KBJ_* 를 지우고, 현재 디렉터리를 빈 임시 폴더로 — 어떤 .env 도 닿지 않게."""
    for name in list(os.environ):
        if name.upper().startswith("KBJ_"):
            monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)


def load() -> Settings:
    return Settings(_env_file=None)  # pyright: ignore[reportCallIssue]


def test_defaults_without_environment() -> None:
    s = load()
    assert s.kis_app_key is None and s.telegram_bot_token is None and s.database_url is None
    assert s.kis_env == "real"
    assert s.live_trading is False  # 절대 규칙 6
    assert s.notify_enabled is False
    assert s.api_host == "127.0.0.1"
    assert s.secret_values() == []


def test_reads_kbj_prefixed_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBJ_KIS_APP_KEY", FAKE)
    monkeypatch.setenv("KBJ_KIS_ENV", "vts")
    monkeypatch.setenv("KBJ_SPOOL_MAX_MB", "64")
    s = load()
    assert isinstance(s.kis_app_key, SecretStr)
    assert s.kis_app_key.get_secret_value() == FAKE
    assert s.kis_env == "vts"
    assert s.spool_max_mb == 64


def test_old_unprefixed_names_are_not_read(monkeypatch: pytest.MonkeyPatch) -> None:
    for old in (
        "KIS_APP_KEY",
        "TELEGRAM_BOT_TOKEN",
        "DART_API_KEY",
        "DATABASE_URL",
        "LIVE_TRADING",
    ):
        monkeypatch.setenv(old, "true" if old == "LIVE_TRADING" else FAKE)
    s = load()
    assert s.kis_app_key is None and s.telegram_bot_token is None and s.dart_api_key is None
    assert s.database_url is None and s.live_trading is False


def test_blank_values_fall_back_to_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example 을 복사만 한 상태
    for name in ("KBJ_KIS_APP_KEY", "KBJ_KIS_ENV", "KBJ_NOTIFY_ENABLED", "KBJ_SPOOL_MAX_MB"):
        monkeypatch.setenv(name, "")
    monkeypatch.setenv("KBJ_DART_API_KEY", "   ")
    s = load()
    assert s.kis_app_key is None and s.dart_api_key is None
    assert s.kis_env == "real" and s.notify_enabled is False and s.spool_max_mb == 1024


def test_dotenv_file_is_read_from_given_path(tmp_path: Path) -> None:
    """`.env` 읽기 경로 자체도 시험한다 — 레포의 실제 .env 가 아니라 임시 폴더의 합성 파일로."""
    env_file = tmp_path / "synthetic.env"
    env_file.write_text(
        f"KBJ_ECOS_KEY={FAKE}\nKBJ_KIS_APP_KEY=\nKBJ_API_PORT=8123\n", encoding="utf-8"
    )
    s = Settings(_env_file=env_file)  # pyright: ignore[reportCallIssue]
    assert s.ecos_key is not None and s.ecos_key.get_secret_value() == FAKE
    assert s.kis_app_key is None and s.api_port == 8123


def test_environment_overrides_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    env_file = tmp_path / "synthetic.env"
    env_file.write_text("KBJ_KIS_ENV=vts\n", encoding="utf-8")
    monkeypatch.setenv("KBJ_KIS_ENV", "real")
    assert Settings(_env_file=env_file).kis_env == "real"  # pyright: ignore[reportCallIssue]


def test_invalid_values_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBJ_KIS_ENV", "paper")
    with pytest.raises(ValidationError):
        load()


def test_secrets_never_appear_in_repr_or_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SECRET_FIELDS:
        monkeypatch.setenv(f"KBJ_{name.upper()}", f"{FAKE}-{name}")
    s = load()
    shown = "\n".join([repr(s), str(s), s.model_dump_json(), str(s.model_dump())])
    assert FAKE not in shown
    assert len(s.secret_values()) == len(_SECRET_FIELDS)
    log_line = f"접속 실패 url={s.database_url.get_secret_value() if s.database_url else ''}"
    assert redact(log_line, s.secret_values()) == f"접속 실패 url={MASK}"


def test_secret_field_list_matches_secretstr_annotations() -> None:
    """_SECRET_FIELDS(빈 값 검증·secret_values 대상) == SecretStr 필드 전부 — 누락 방지."""
    secret_fields = {
        name for name, f in Settings.model_fields.items() if SecretStr in get_args(f.annotation)
    }
    assert set(_SECRET_FIELDS) == secret_fields


def test_env_example_lists_every_setting_with_empty_values() -> None:
    """.env.example 은 Settings 의 모든 필드를 KBJ_ 이름으로, 값 없이 담는다."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    names: dict[str, str] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"(KBJ_[A-Z0-9_]+)=(.*)", line.strip())
        if m:
            names[m[1]] = m[2]
    expected = {f"KBJ_{name.upper()}" for name in Settings.model_fields}
    assert expected <= set(names), sorted(expected - set(names))
    assert all(v == "" for v in names.values()), "값은 비워 둔다"


def test_secrets_doc_lists_every_setting() -> None:
    text = (ROOT / "docs" / "secrets.md").read_text(encoding="utf-8")
    missing = [n for n in Settings.model_fields if f"KBJ_{n.upper()}" not in text]
    assert missing == []


# ── P3 로그인 웹·공개 내보내기(docs/p3_design.md §5.4·§7, D-P3-4) ─────────────────────────────

SYN_HASH = (
    "scrypt$n=1024$r=8$p=1$" + "c3ludGhldGlj" + "$" + "ZmFrZS1kay0wMTIz"
)  # 합성 — 실제 해시 아님


def test_p3_defaults_have_no_login_and_push_off() -> None:
    s = load()
    assert s.web_user is None and s.web_password_hash is None  # 기본 사용자·비밀번호 없음
    assert s.web_session_ttl_h == 12 and s.web_cookie_secure is True
    assert s.web_dist_dir == Path("web/dist-login")
    assert s.public_export_database_url is None
    assert s.public_push_enabled is False  # [사용자 승인 필요] — 기본 꺼짐
    assert s.public_deploy_key_path is None and s.github_dispatch_token is None


def test_p3_values_are_read_and_secrets_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBJ_WEB_USER", "synthetic-user")
    monkeypatch.setenv("KBJ_WEB_PASSWORD_HASH", SYN_HASH)
    monkeypatch.setenv("KBJ_PUBLIC_EXPORT_DATABASE_URL", f"postgresql://x:{FAKE}@h/db")
    monkeypatch.setenv("KBJ_GITHUB_DISPATCH_TOKEN", FAKE)
    monkeypatch.setenv("KBJ_WEB_SESSION_TTL_H", "6")
    monkeypatch.setenv("KBJ_PUBLIC_DEPLOY_KEY_PATH", "/srv/keys/public_data")
    s = load()
    assert s.web_user == "synthetic-user" and s.web_session_ttl_h == 6
    assert s.web_password_hash is not None and s.web_password_hash.get_secret_value() == SYN_HASH
    assert s.public_deploy_key_path == Path("/srv/keys/public_data")
    shown = "\n".join([repr(s), s.model_dump_json()])
    assert SYN_HASH not in shown and FAKE not in shown
    assert {v.get_secret_value() for v in s.secret_values()} >= {SYN_HASH, FAKE}


def test_password_hash_is_not_format_checked_here(monkeypatch: pytest.MonkeyPatch) -> None:
    """설정은 해시 형식을 검사하지 않는다 — pydantic 검증 오류는 입력값을 문구에 싣기 때문(값 유출).
    형식이 틀리면 쓰는 곳(services.api.auth)이 값 없이 503 으로 실패시킨다."""
    monkeypatch.setenv("KBJ_WEB_PASSWORD_HASH", "plain-" + FAKE)
    s = load()
    assert s.web_password_hash is not None and FAKE not in repr(s)


def test_insecure_cookie_only_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KBJ_WEB_COOKIE_SECURE", "false")
    assert load().web_cookie_secure is False  # 기본 api_host 127.0.0.1
    monkeypatch.setenv("KBJ_API_HOST", "0.0.0.0")  # noqa: S104 — 거부되는지 본다
    with pytest.raises(ValidationError, match="루프백"):
        load()
    monkeypatch.setenv("KBJ_WEB_COOKIE_SECURE", "true")
    assert load().api_host == "0.0.0.0"  # noqa: S104


@pytest.mark.parametrize("value", ["0", "169"])
def test_session_ttl_bounds(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("KBJ_WEB_SESSION_TTL_H", value)
    with pytest.raises(ValidationError):
        load()


def test_validation_errors_do_not_echo_secret_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    """모델 검증기 오류는 입력 dict 전체를 문구에 싣는다 — hide_input_in_errors 로 막는다."""
    monkeypatch.setenv("KBJ_FRED_KEY", FAKE)  # 짧은 이름이 dict 앞에 오면 값 앞부분이 보였다
    monkeypatch.setenv("KBJ_DATABASE_URL", f"postgresql://x:{FAKE}@h/db")
    monkeypatch.setenv("KBJ_WEB_COOKIE_SECURE", "false")
    monkeypatch.setenv("KBJ_API_HOST", "0.0.0.0")  # noqa: S104 — 거부되는지 본다
    with pytest.raises(ValidationError) as ei:
        load()
    text = str(ei.value)
    assert "루프백" in text and "input_value" not in text
    assert FAKE[:6] not in text and "postgre" not in text
    monkeypatch.setenv("KBJ_WEB_SESSION_TTL_H", f"x{FAKE}")
    monkeypatch.setenv("KBJ_WEB_COOKIE_SECURE", "true")
    with pytest.raises(ValidationError) as ei:
        load()
    assert FAKE[:6] not in str(ei.value)
