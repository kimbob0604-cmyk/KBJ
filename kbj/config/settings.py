"""KBJ 설정 — 환경변수 `KBJ_*` 와 로컬 `.env` 에서만 읽는다(docs/secrets.md, inventory.md (f)).

- 비밀값은 전부 `SecretStr` — `repr`·`model_dump_json` 에 원문이 나오지 않는다. 쓰는 곳에서만
  `get_secret_value()` 로 꺼내고, 로그·오류 문구에는 kbj.core.masking 을 거친다(절대 규칙 5).
- 빈 값(`.env.example` 을 복사만 한 `KBJ_KIS_APP_KEY=`)은 기본값(비밀은 None)으로 본다.
- 비밀이 아닌 튜닝값(호출 상한·주기 등)은 환경변수가 아니라 `config/*.yaml` 로 둔다
  (inventory (f) 원칙).
- 테스트는 `Settings(_env_file=None)` 으로 실제 `.env` 를 읽지 않는다.
- KIS 앱키·시크릿은 KIS REST 를 부르는 프로세스에도 들어간다(모든 요청 헤더에 필요 — ADR 0004).
  **발급**(접근토큰·웹소켓 접속키)은 `service == "auth"` 인 프로세스만 한다: 발급 클래스는
  kbj/services/auth/issuer.py 에만 있고, 그 생성자가 `service` 를 본다(런타임 가드).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = (
    "kis_app_key",
    "kis_app_secret",
    "krx_api_key",
    "dart_api_key",
    "datago_key",
    "kosis_key",
    "ecos_key",
    "fred_key",
    "finnhub_key",
    "naver_search_id",
    "naver_search_secret",
    "anthropic_api_key",
    "telegram_bot_token",
    "telegram_chat_id",
    "telegram_inbox_chat_ids",
    "telegram_webhook_secret",
    "postgres_password",
    "redis_password",
    "database_url",
    "redis_url",
    "healthcheck_url",
)


class Settings(BaseSettings):
    """`KBJ_` + 필드 이름(대문자)이 환경변수 이름이다. 예: `kis_app_key` ← `KBJ_KIS_APP_KEY`."""

    model_config = SettingsConfigDict(
        env_prefix="KBJ_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        env_ignore_empty=True,  # `.env.example` 을 복사만 한 빈 값은 기본값으로
    )

    # ── KIS — 발급은 services.auth 만, 요청 헤더는 KIS REST 를 부르는 프로세스(ADR 0004) ──────────
    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_env: Literal["real", "vts"] = "real"

    # ── 시세·공시·통계 API 키 ─────────────────────────────────────────────────────────
    krx_api_key: SecretStr | None = None  # 로그인 등급. 하루 호출 한도를 모든 작업이 나눠 쓴다
    dart_api_key: SecretStr | None = None  # 공개 등급
    datago_key: SecretStr | None = None  # 공공데이터포털(관세청·금투협 종합통계 같은 키)
    kosis_key: SecretStr | None = None
    ecos_key: SecretStr | None = None
    fred_key: SecretStr | None = None
    finnhub_key: SecretStr | None = None  # 로그인 등급
    naver_search_id: SecretStr | None = None  # 네이버 검색 API(로그인 전용, U4 — 대체 후 삭제 후보)
    naver_search_secret: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None  # 요약·문장화에만(절대 규칙 3). SDK 에 명시 전달

    # ── 텔레그램 — services.notifier 만 읽는다(ADR 0001 U1) ────────────────────────────────
    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: SecretStr | None = None
    telegram_inbox_chat_ids: SecretStr | None = None  # 쉼표 구분. 인박스 명령을 받을 대화
    telegram_webhook_secret: SecretStr | None = None
    notify_enabled: bool = False  # [확인 필요] 기본은 발송하지 않음 — 운영 VM 에서만 켠다

    # ── 저장소 ────────────────────────────────────────────────────────────────────────
    postgres_password: SecretStr | None = None  # docker-compose.yml db 컨테이너
    redis_password: SecretStr | None = None  # docker-compose.yml redis 컨테이너
    database_url: SecretStr | None = None  # 접속 문자열에 비밀번호가 들어 있다
    redis_url: SecretStr | None = None  # 같음
    data_dir: Path = Path("state")  # [확인 필요] 실행 산출물 루트(gitignore: /state/)
    spool_dir: Path = Path("state/spool")  # DB 장애 동안 쓰기 묶음 디스크 큐
    spool_max_mb: int = Field(default=1024, gt=0)

    # ── API·운영 ─────────────────────────────────────────────────────────────────────
    api_host: str = "127.0.0.1"
    api_port: int = Field(default=8000, gt=0, lt=65536)
    public_base_url: str | None = None  # 텔레그램 웹훅 주소의 앞부분. 값은 운영 환경에만 둔다
    git_commit: str | None = None
    healthcheck_url: SecretStr | None = None  # 외부 하트비트 주소(주소 자체가 비밀)
    probe_out_dir: Path = Path("probe_out")
    test_timescale_image: str | None = None

    # ── 프로세스·설정 파일 ─────────────────────────────────────────────────────────────
    # 이 프로세스가 어느 서비스인가(compose 가 서비스마다 넣는다: auth·scheduler·notifier …).
    # 비어 있으면 None(시험·스크립트). KIS 발급자는 "auth" 일 때만 만들어진다(ADR 0004 런타임 가드)
    service: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_.-]*$")
    # config/*.yaml(작업 등록부·호출 상한·발송 규칙·휴장 덮어쓰기)이 있는 폴더. 상대 경로는
    # 레포 루트 기준(kbj/config/files.py — 작업 디렉터리와 무관). 설치 이미지에서는 절대 경로로
    config_dir: Path = Path("config")

    # ── 실전 주문 스위치 — 기본 false, 사용자 승인 전엔 바꾸지 않는다(절대 규칙 6) ──────────────
    live_trading: bool = False

    @field_validator(*_SECRET_FIELDS, "public_base_url", "git_commit", "service", mode="before")
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    def secret_values(self) -> list[SecretStr]:
        """설정된 비밀값 전부 — kbj.core.masking.redact(text, settings.secret_values()) 용."""
        found: list[SecretStr] = []
        for name in _SECRET_FIELDS:
            value = getattr(self, name)
            if isinstance(value, SecretStr) and value.get_secret_value():
                found.append(value)
        return found
