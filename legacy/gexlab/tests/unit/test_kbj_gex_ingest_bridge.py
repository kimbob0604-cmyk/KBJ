"""KBJ P7 ING 다리 시험 — GX 웹소켓·녹화·REST 파생 모델은 KBJ 정본을 다시 내보낸다(docs/p7_design.md §1.4).

- `services.ws_gateway.*`·`services.recorder.*`·`data.kis.{ws,models}` shim 은 KBJ 모듈의 공개 이름을
  **같은 객체로** 모두 내보낸다(두 벌 금지 — 남은 legacy poller·scheduler·통합 시험이 그대로 돈다)
- 설정 `config/{kis_ws,kis_ws_fields,ws_budget}.yaml` 은 KBJ `config/` 한 벌이고(legacy 폴더에 없다),
  웹소켓 호스트는 KBJ `kbj/services/ws_gateway/endpoints.py` 상수 하나다(D-P7-10)
- `scripts.make_golden` 의 raw·raw-parse 이름은 KBJ `kbj.services.recorder.golden` 의 것이고 모드도
  넘긴다. 합성 KIS fixture 의 legacy 사본은 KBJ 사본과 바이트까지 같다
- poller 는 시장별 투자자(FHPTJ04030000)를 부르지 않는다(D-P7-5 — 쓰기 주인은 KBJ flows.market_minute)
"""

from __future__ import annotations

import importlib
from pathlib import Path

import kbj.services.recorder.golden
import kbj.services.ws_gateway.client

import scripts.make_golden as mg
from services.poller.config import PollerConfig

GX = Path(__file__).resolve().parents[2]
KBJ = GX.parents[1]
SHIMS = {
    "data.kis.ws": "kbj.data.private.kis.ws",
    "data.kis.models": "kbj.data.private.kis.derivatives",
    "services.recorder.envelope": "kbj.services.recorder.envelope",
    "services.recorder.service": "kbj.services.recorder.service",
    **{
        f"services.ws_gateway.{m}": f"kbj.services.ws_gateway.{m}"
        for m in ("backoff", "budget", "client", "service", "session", "subscriptions", "watch")
    },
}


def _public(mod: object) -> dict[str, object]:
    """`import *` 가 가져오는 이름 — `__all__` 이 있으면 그것, 없으면 `_` 로 시작하지 않는 이름."""
    names = getattr(mod, "__all__", None)
    if names is not None:
        return {k: getattr(mod, k) for k in names}
    return {k: v for k, v in vars(mod).items() if not k.startswith("_")}


def test_gexlab_ingest_reexports_kbj_ws_gateway_and_recorder() -> None:
    for old, new in SHIMS.items():
        shim, canon = importlib.import_module(old), importlib.import_module(new)
        public = _public(canon)
        assert public, new
        for name, obj in public.items():
            if name == "annotations":  # `from __future__ import annotations` 의 이름
                continue
            assert getattr(shim, name) is obj, (old, name)  # 같은 객체 — 사본이 아니다
        shim_src = (GX / (old.replace(".", "/") + ".py")).read_text(encoding="utf-8")
        assert "def " not in shim_src and "class " not in shim_src, old  # shim 에는 정의가 없다

    # 설정은 KBJ config/ 한 벌, 호스트는 endpoints 상수(설정 파일에 없다)
    for name in ("kis_ws.yaml", "kis_ws_fields.yaml", "ws_budget.yaml"):
        assert not (GX / "config" / name).exists() and (KBJ / "config" / name).is_file(), name
    cfg = kbj.services.ws_gateway.client.load_config()
    assert cfg.url_for("real").startswith("ws://") and "url" not in type(cfg).model_fields
    assert "ops.koreainvestment" not in (KBJ / "config" / "kis_ws.yaml").read_text(encoding="utf-8")

    # raw 녹화 골든: 이름은 KBJ 것, 모드는 KBJ CLI 로(녹화는 KBJ tests/golden/gex/raw/)
    golden = kbj.services.recorder.golden
    for name in ("RAW_DIR", "SECRET_PATTERNS", "sanitize", "cut_raw", "raw_snapshot", "RawRow"):
        assert getattr(mg, name) is getattr(golden, name), name
    assert mg.RAW_DIR.is_relative_to(KBJ / "tests" / "golden" / "gex")
    assert mg.main(["raw-parse"]) == 0  # 커밋된 합성 녹화의 파서 스냅샷과 같다(쓰지 않는다)

    # 합성 KIS fixture 사본 — 바이트까지 같다
    for name in ("futures_board", "investor", "option_list", "price_options", "minute_day"):
        legacy = GX / "tests" / "fixtures" / "kis" / f"{name}.json"
        kbj_copy = KBJ / "tests" / "fixtures" / "synthetic" / "kis" / f"{name}.json"
        assert legacy.read_bytes() == kbj_copy.read_bytes(), name

    # poller 투자자별 폴링은 지웠다(설정 이름도 없다)
    assert not {"night_investor", "investor_period_s", "investor_offset_s"} & set(
        PollerConfig.model_fields
    )
    poller = (GX / "services" / "poller").glob("*.py")
    assert not any("FHPTJ04030000" in p.read_text(encoding="utf-8") for p in poller)
