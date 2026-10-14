"""환경 설정. 비밀정보는 `.env`(로컬) 또는 환경변수(CI)에서만 읽는다."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from kbj.data.private.kis.credentials import KisCredentials, base_url
from psycopg.conninfo import make_conninfo
from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# KBJ P2(설계 §3.8): KIS·KRX 주소 문자열은 KBJ 어댑터 안에만 둔다(check_canonical `kis_rest`·
# `krx_api`). `kis_base` 는 KBJ `credentials.base_url(env)` 로 얻는다
# (같은 값 — 토큰 소유 해시가 같다).


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    kis_app_key: SecretStr | None = None
    kis_app_secret: SecretStr | None = None
    kis_env: Literal["real", "vts"] = "real"

    krx_api_key: SecretStr | None = None
    # scheduler KRX 전일 적재의 KST 하루 호출 상한 (KRX 10,000/일, PLAN §3 #16 — 키를 다른 작업과
    # 나눠 쓴다). 확인 필요: 기본 200 — 평소엔 하루 2건(옵션·선물), 10분 재시도를 10:00 까지 다 써도
    # 24건(08:05~09:55 12회 × 2). 재기동을 되풀이하는 고장에서 KRX 를 지킨다
    krx_daily_call_cap: int = Field(default=200, gt=0)

    # KBJ P2(설계 §5.10 #42): telegram_bot_token·telegram_chat_id 필드 삭제 — GX 는 텔레그램을 쓰지
    # 않았고(읽는 곳 없음) 발송·수신은 KBJ notifier 한 곳이다(KBJ_TELEGRAM_*). extra="ignore" 라
    # .env 에 옛 이름이 남아 있어도 무시한다

    @field_validator(
        "kis_app_key",
        "kis_app_secret",
        "krx_api_key",
        "database_url",
        mode="before",
    )
    @classmethod
    def _blank_is_none(cls, v: object) -> object:
        """`.env.example` 을 복사만 한 빈 값(`KIS_APP_KEY=`)은 '없음'으로 본다."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # 공용 Redis (토큰 캐시·레이트리미터). 토큰은 KBJ auth 가 Redis 에만 둔다
    # (KBJ P2 — 파일 캐시 폐지)
    redis_url: str | None = None

    # PostgreSQL + TimescaleDB (PLAN §4.5). 비밀번호가 들어 있어 SecretStr — 쓰는 곳에서만
    # get_secret_value() 로 꺼내고 로그·오류 문구에 싣지 않는다
    database_url: SecretStr | None = None
    # DB 장애 동안 쓰기 묶음을 쌓는 로컬 디스크 큐 (설계 §2, data/spool.py). 서비스마다 하위 폴더.
    # 상한은 서비스 하나 기준 — 넘으면 오래된 것부터 버리고 health (확인 필요: 녹화량 실측 뒤 조정)
    # KBJ P7(D-P7-4): legacy 서비스(poller·scheduler)는 KBJ DB 에 붙는다 — 스키마 정본은 KBJ
    # 0006(`prv_gex`·`ops`), GX 자기 마이그레이션(db.migrate)은 돌리지 않는다. 값이 있으면 접속
    # 문자열에 `options=-csearch_path=<값>` 을 더해 GX SQL 의 표 이름(스키마 없음)이 KBJ 표로 간다.
    # 운영 값은 "prv_gex,ops". 비면(통합 시험의 자기 DB) 그대로. [확인 필요: 운영 VM 의 환경변수
    # 이름 — 지금 DB_SEARCH_PATH]
    db_search_path: str | None = Field(default=None, pattern=r"^[a-z_][a-z0-9_]*(,[a-z_][a-z0-9_]*)*$")
    spool_dir: Path = Path("state/spool")  # gitignore: state/
    spool_max_mb: int = Field(default=1024, gt=0)

    # 실전 주문 스위치. Phase 9 전까지 false 고정 (PLAN §0-6)
    live_trading: bool = False

    @model_validator(mode="after")
    def _kbj_search_path(self) -> Settings:
        """db_search_path 가 있으면 접속 문자열에 search_path 옵션을 붙인다(KBJ DB — D-P7-4)."""
        if self.db_search_path and self.database_url is not None:
            dsn = self.database_url.get_secret_value()
            opts = f"-csearch_path={self.db_search_path}"
            self.database_url = SecretStr(make_conninfo(dsn, options=opts))
        return self

    @property
    def kis_base(self) -> str:
        return base_url(self.kis_env)

    def kis_credentials(self) -> KisCredentials:
        """KBJ KIS 자격(앱키·시크릿·환경). 앱키가 없으면 ValueError.

        시크릿은 없으면 빈 값 — 읽기 전용 토큰 리더는 앱키만 쓴다."""
        if self.kis_app_key is None:
            raise ValueError("KIS_APP_KEY 가 없다")
        return KisCredentials(
            app_key=self.kis_app_key,
            app_secret=self.kis_app_secret or SecretStr(""),
            env=self.kis_env,
        )
