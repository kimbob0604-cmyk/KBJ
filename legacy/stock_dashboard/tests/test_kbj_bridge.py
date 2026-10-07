"""KBJ P2 다리 시험 — SD 의 KIS·KRX·DART 호출이 KBJ 브리지로만 간다(설계 §1.9·§3.8 K1).

kis_api 는 토큰을 발급하지 않는다: KBJ auth 가 Redis 에 둔 토큰이 없으면 `_headers` 가 None 이라
호출자가 빈 결과로 끝난다(기존 동작). 주소는 논리 URL(`kis:`·`kis-master:`·`krx:`·`dart:`)이다.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture()
def bridge():
    from kbj.config.settings import Settings
    from kbj.data import legacy_bridge

    legacy_bridge.reset()
    legacy_bridge.configure(settings=Settings(_env_file=None))  # Redis·키 없음
    yield legacy_bridge
    legacy_bridge.reset()


def test_sd_reads_tokens_only_and_calls_logical_urls(bridge) -> None:
    import dart_collector
    import kis_api
    import krx_api

    assert kis_api.requests is bridge
    assert (kis_api.KIS_BASE, kis_api.FO_MASTER_URL) == ("kis:", "kis-master:fo_idx_code_mts.mst.zip")
    assert (krx_api.KRX_API_BASE, dart_collector.DART_BASE_URL) == ("krx:", "dart:")
    for gone in ("_TOKEN_FILE", "_token_cache", "_load_token_from_disk", "_rate_limit"):
        assert not hasattr(kis_api, gone), gone
    assert kis_api._get_token() is None  # 발급하지 않는다 — auth 대기
    assert kis_api._headers("FHKST01010100") is None
    with pytest.raises(ValueError):
        bridge.get("https://example.invalid/uapi/x")
