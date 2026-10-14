"""P7 compose — GEX 상시 프로세스 셋(정적, 데몬 없이). docs/p7_design.md §1.7-6·D-P7-10.

- ws-gateway·recorder·gex-engine: 같은 KBJ 이미지·profile app, 명령은 각 모듈 진입점, 하트비트
  healthcheck 이름은 모듈의 `SERVICE` 와 같다(등록부 services 의 gex.* 와 짝).
- KIS 키: ws-gateway 만 앱키(접속키 캐시 이름) — 시크릿은 어디에도 없다(발급은 auth 만, ADR 0004).
  녹화·엔진은 KIS 를 부르지 않아 KIS 이름이 없다. 텔레그램 토큰 없음.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from kbj.services.engine.gex import SERVICE as ENGINE
from kbj.services.recorder.service import SERVICE as RECORDER
from kbj.services.ws_gateway.client import SERVICE as WS

ROOT = Path(__file__).resolve().parent.parent
GEX = {
    "ws-gateway": (["python", "-m", "kbj.services.ws_gateway"], WS),
    "recorder": (["python", "-m", "kbj.services.recorder"], RECORDER),
    "gex-engine": (["python", "-m", "kbj.services.engine.gex"], ENGINE),
}


def _services() -> dict[str, Any]:
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]


def test_gex_services_shape_and_heartbeat_names() -> None:
    services = _services()
    for name, (cmd, beat) in GEX.items():
        s = services[name]
        assert s["image"] == "kbj-app:local" and s["profiles"] == ["app"], name
        assert s["command"] == cmd, name
        hc = s["healthcheck"]["test"]
        assert hc[:4] == ["CMD", "python", "-m", "kbj.services.runtime.healthcheck"], name
        assert hc[4] == beat and hc[5] == "60", name  # 등록부 heartbeat_s 60
        env = s["environment"]
        assert env["KBJ_SERVICE"] == beat and env["TZ"] == "UTC", name
        assert env["KBJ_DATABASE_URL"].endswith("@db:5432/kbj"), name
        assert env["KBJ_REDIS_URL"].endswith("@redis:6379/0"), name
        assert "migrate" in s["depends_on"], name


def test_only_ws_gateway_knows_the_kis_app_key_and_nobody_the_secret() -> None:
    services = _services()
    for name in GEX:
        env = services[name]["environment"]
        assert "KBJ_KIS_APP_SECRET" not in env, name
        assert "KBJ_TELEGRAM_BOT_TOKEN" not in env, name
        assert ("KBJ_KIS_APP_KEY" in env) == (name == "ws-gateway"), name
    for name in ("ws-gateway", "recorder"):  # DB 장애 동안 디스크 큐(GX 규칙)
        assert "state:/data" in services[name]["volumes"]
        assert services[name]["environment"]["KBJ_SPOOL_DIR"] == "/data/spool"
