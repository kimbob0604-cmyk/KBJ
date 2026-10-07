"""텔레그램 명령 분배(설계 §5.7) — SD `server.py:_handle_telegram_command`(:5823)·`_tg_help` 승격.

- 명령은 `/이름 인자`. 그룹 대화의 `@봇이름` 접미사는 뗀다(SD). 대소문자는 가리지 않는다.
- P2 에서 동작하는 것은 `/도움`(help·start·명령) 하나다. 나머지는 '준비 중(Pn)' 으로 답한다 — 연결
  단계(P3·P7·P8)에서 처리기를 `register(..., handler=…)` 로 끼운다.
- 모르는 명령은 '알 수 없는 명령' + 도움말(SD). 인자가 필요한 명령에 인자가 없으면 사용법.
- 처리기 예외는 삼키지 않는다: 종류를 로그에 남기고 '처리 중 오류' 로 답한다(본문·키는 싣지 않는다).
- 답은 `Reply` 로 돌려줄 뿐 보내지 않는다 — 보내는 것은 notify(`cmd.reply`, 받은 대화·스레드)다.
- 종목명 → 코드는 SD `_resolve_kr_code`(네이버 유니버스)가 아니라 `prv_market.universe` 로(U4, P3).
"""

from __future__ import annotations

import html
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

log = logging.getLogger(__name__)

PHASE_NOW: Final = "P2"


@dataclass(frozen=True)
class Reply:
    """명령 응답 — 보낼 본문(HTML)."""

    text: str
    parse_mode: str | None = "HTML"


@dataclass(frozen=True)
class CommandContext:
    name: str  # 정식 이름(별칭으로 불렀어도)
    called: str  # 실제로 친 이름
    arg: str
    chat_id: int | str | None
    thread_id: int | None
    update_id: int | None


Handler = Callable[[CommandContext], Reply]


@dataclass(frozen=True)
class Command:
    name: str
    aliases: tuple[str, ...]
    handler: Handler | None  # None 이면 아직 연결 전 — '준비 중(phase)'
    phase: str
    summary: str
    usage: str | None = None  # 인자가 필요하면 '사용법' 문구(예: '/수급 삼성전자')


def parse_command(text: str | None) -> tuple[str, str] | None:
    """'/수급@kbj_bot 삼성전자' → ('수급', '삼성전자'). 명령이 아니면 None."""
    t = (text or "").strip()
    if not t.startswith("/"):
        return None
    parts = t[1:].split(maxsplit=1)
    if not parts:
        return None
    cmd = parts[0].lower().lstrip("/").split("@")[0]
    if not cmd:
        return None
    return cmd, (parts[1].strip() if len(parts) > 1 else "")


@dataclass
class CommandRegistry:
    _commands: dict[str, Command] = field(default_factory=dict[str, Command])
    _alias: dict[str, str] = field(default_factory=dict[str, str])

    def register(
        self,
        name: str,
        aliases: tuple[str, ...] = (),
        handler: Handler | None = None,
        *,
        phase: str = PHASE_NOW,
        summary: str = "",
        usage: str | None = None,
    ) -> Command:
        """명령 하나를 등록. 같은 이름을 다시 등록하면 바꿔 끼운다(연결 단계가 처리기를 넣는다)."""
        key = name.lower()
        names = (key, *(a.lower() for a in aliases))
        for n in names:
            owner = self._alias.get(n)
            if owner is not None and owner != key:
                raise ValueError(f"명령 이름 /{n} 이 이미 /{owner} 의 것이다")
        cmd = Command(key, tuple(a.lower() for a in aliases), handler, phase, summary, usage)
        self._commands[key] = cmd
        for n in names:
            self._alias[n] = key
        return cmd

    def get(self, name: str) -> Command | None:
        key = self._alias.get(name.lower())
        return self._commands.get(key) if key is not None else None

    @property
    def commands(self) -> list[Command]:
        return list(self._commands.values())

    def help_text(self) -> str:
        lines = ["🤖 <b>명령어</b>"]
        for c in self._commands.values():
            head = html.escape(c.usage or f"/{c.name}")
            note = "" if c.handler is not None else f" <i>(준비 중 — {html.escape(c.phase)})</i>"
            lines.append(f"{head} — {html.escape(c.summary)}{note}")
        return "\n".join(lines)

    def dispatch(
        self,
        text: str | None,
        chat_id: int | str | None,
        thread_id: int | None = None,
        *,
        update_id: int | None = None,
    ) -> Reply | None:
        """명령이면 답(Reply), 아니면 None."""
        parsed = parse_command(text)
        if parsed is None:
            return None
        called, arg = parsed
        cmd = self.get(called)
        if cmd is None:
            return Reply(f"❓ 알 수 없는 명령: /{html.escape(called)}\n" + self.help_text())
        if cmd.handler is None:
            return Reply(f"⏳ /{html.escape(cmd.name)} 은 준비 중입니다({html.escape(cmd.phase)}).")
        if cmd.usage is not None and not arg:
            return Reply(f"사용법: {html.escape(cmd.usage)}")
        ctx = CommandContext(cmd.name, called, arg, chat_id, thread_id, update_id)
        try:
            return cmd.handler(ctx)
        except Exception as e:  # 명령 하나의 실패가 수신을 멈추지 않는다 — 종류는 남긴다
            log.error("명령 /%s 처리 실패: %s", cmd.name, type(e).__name__)
            return Reply(f"⚠️ /{html.escape(cmd.name)} 처리 중 오류: {type(e).__name__}")


def default_registry() -> CommandRegistry:
    """§5.7 표 그대로. `/도움` 만 P2 에서 동작한다."""
    reg = CommandRegistry()
    reg.register("도움", ("help", "start", "명령"), summary="이 안내")
    reg.register("시황", ("summary",), phase="P3", summary="오늘 장마감 요약")
    reg.register("수급", ("flow",), phase="P3", summary="종목 최근 5일 수급", usage="/수급 <종목>")
    reg.register("가격", ("시세", "price"), phase="P3", summary="현재가", usage="/가격 <종목>")
    reg.register("신고가", ("newhigh",), phase="P3", summary="오늘 52주·역사적 신고가")
    reg.register("gex", (), phase="P7", summary="최신 GEX 레벨")
    reg.register("시그널", ("수급시그널", "signals"), phase="P8", summary="수급 시그널")

    def _help(_: CommandContext) -> Reply:
        return Reply(reg.help_text())

    reg.register("도움", ("help", "start", "명령"), _help, summary="이 안내")
    return reg
