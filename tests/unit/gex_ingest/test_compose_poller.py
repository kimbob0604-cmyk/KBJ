"""P9 GXI compose — 서비스 `gex-poller`(정적, 데몬 없이). docs/p9_design.md §7.2 GXI-5, ADR 0036.

- 같은 KBJ 이미지·profile app, 명령은 `python -m kbj.services.gex_poller`, 하트비트 healthcheck
  이름은 모듈의 `HEARTBEAT`(등록부 서비스 gex.poller 의 heartbeat_key 와 짝).
- KIS REST 를 부르므로 앱키·시크릿 — scheduler 와 같은 이름(ADR 0004 기준, 발급은 auth 만).
  텔레그램·DART·KRX 키 없음. DB 장애 동안 디스크 큐(/data/spool).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kbj.services.gex_poller.service import HEARTBEAT

ROOT = Path(__file__).resolve().parents[3]


def _services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_gex_poller_service_shape_and_heartbeat() -> None:
    s = _services()["gex-poller"]
    assert s["image"] == "kbj-app:local" and s["profiles"] == ["app"]
    assert s["command"] == ["python", "-m", "kbj.services.gex_poller"]
    assert s["healthcheck"]["test"] == [
        "CMD",
        "python",
        "-m",
        "kbj.services.runtime.healthcheck",
        HEARTBEAT,
        "60",
    ]
    env = s["environment"]
    assert env["KBJ_SERVICE"] == HEARTBEAT == "gex-poller" and env["TZ"] == "UTC"
    assert env["KBJ_DATABASE_URL"].endswith("@db:5432/kbj")
    assert env["KBJ_REDIS_URL"].endswith("@redis:6379/0")
    assert {"db", "redis", "migrate", "auth"} <= set(s["depends_on"])
    assert "state:/data" in s["volumes"] and env["KBJ_SPOOL_DIR"] == "/data/spool"
    assert "env_file" not in s and "ports" not in s


def test_gex_poller_gets_the_same_kis_names_as_scheduler_and_no_other_secret() -> None:
    services = _services()
    env, sched = services["gex-poller"]["environment"], services["scheduler"]["environment"]
    for key in ("KBJ_KIS_APP_KEY", "KBJ_KIS_APP_SECRET", "KBJ_KIS_ENV"):
        assert env[key] == sched[key], key
    assert env["KBJ_KIS_APP_KEY"] == "${KBJ_KIS_APP_KEY:-}"  # 기본값 없음
    others = {
        k for k in env if k.startswith(("KBJ_TELEGRAM_", "KBJ_DART_", "KBJ_KRX_", "KBJ_PUBLIC_"))
    }
    assert others == set()
