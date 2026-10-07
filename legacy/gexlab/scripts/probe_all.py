"""probe 일괄 실행. 각 probe 는 격리되어 하나가 실패해도 나머지는 돈다.

    uv run python -m scripts.probe_all                # 전부
    uv run python -m scripts.probe_all callput krx    # 골라서

결과: probe_out/<name>.json, probe_out/summary.md
"""

from __future__ import annotations

import sys
import traceback
from collections.abc import Callable
from types import ModuleType

from config.settings import Settings
from data.kis.rest import KisClient, redact
from scripts import (
    probe_callput,
    probe_chain_fill,
    probe_investor,
    probe_krx,
    probe_minute_history,
    probe_minute_paging,
    probe_minute_paging_followup,
    probe_night_board,
    probe_option_list,
    probe_rest_limit,
)
from scripts.probe_common import OUT_DIR, Ctx, now_kst, session_of, write_result

# 순서가 의미 있다: 한도 실측(rest_limit)은 다른 probe 를 방해하지 않게 KIS 중 마지막
PROBES: dict[str, ModuleType] = {
    "option_list": probe_option_list,
    "callput": probe_callput,
    "chain_fill": probe_chain_fill,
    "investor": probe_investor,
    "minute_history": probe_minute_history,
    "minute_paging": probe_minute_paging,
    "minute_paging_followup": probe_minute_paging_followup,
    "night_board": probe_night_board,
    "rest_limit": probe_rest_limit,
    "krx": probe_krx,
}
NEEDS_KIS = {n for n in PROBES if n != "krx"}


def main(argv: list[str]) -> int:
    names = argv or list(PROBES)
    unknown = [n for n in names if n not in PROBES]
    if unknown:
        print(f"모르는 probe: {unknown} (가능: {list(PROBES)})")
        return 2
    settings = Settings()
    kis = KisClient(settings)
    started = now_kst()
    lines = [f"# probe 결과 {started:%Y-%m-%d %H:%M} KST (세션: {session_of(started)})", ""]
    failed = 0
    shared = Ctx(settings, kis)  # 월물리스트 결과 등은 probe 사이에 공유
    for name in names:
        if name in NEEDS_KIS and settings.kis_app_key is None:
            lines += [f"## {name}", "- 건너뜀: KIS_APP_KEY 없음", ""]
            continue
        if name == "krx" and settings.krx_api_key is None:
            lines += [f"## {name}", "- 건너뜀: KRX_API_KEY 없음", ""]
            continue
        ctx = Ctx(settings, kis, findings={}, notes=[])
        if "option_list" in shared.findings:
            ctx.findings["option_list"] = shared.findings["option_list"]
        t0 = now_kst()
        run: Callable[[Ctx], None] = PROBES[name].run
        err: str | None = None
        try:
            run(ctx)
        except Exception as e:  # probe 는 서로 격리한다
            err = f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}"
            failed += 1
        if "option_list" in ctx.findings:
            shared.findings["option_list"] = ctx.findings["option_list"]
        write_result(name, ctx, t0, err)
        lines.append(f"## {name}")
        lines += [f"- {redact(n, kis)}" for n in ctx.notes]
        if err:
            lines += ["", "```", redact(err, kis), "```"]
        lines.append("")
    kis.close()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    (OUT_DIR / "summary.md").write_text(text, encoding="utf-8")
    print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
