"""가짜 OpenDART(`httpx.MockTransport`) — 공시 목록·고유번호 zip·재무·기업개황. 합성 데이터만.

DART 는 공개 등급이지만(DATA_TIERS §1) 시뮬레이션은 키 없이 돌아야 하므로 합성 응답을 쓴다.
응답 모양은 DART 개발가이드(상태 `000` 정상·`013` 데이터 없음·`020` 요청 제한 초과)대로다.

- `list.json`: 분마다 합성 공시 0~2건(고정 시드 — 분 문자열 sha256). 접수번호는 `2026…` 14자리 합성.
- `corpCode.xml`: 합성 고유번호 zip(`corps` 개 — 상장 절반, 비상장은 stock_code 공백 한 칸).
- `fnlttSinglAcntAll.json`·`fnlttMultiAcnt.json`·`company.json`: 상태 000 + 합성 한 행.
- `inject(status, count)`: 다음 count 번에 그 상태(`020`·`013` 등)를 준다.
- 인증키(`crtfc_key`)가 다르면 상태 `010`(등록되지 않은 키). 기록 `seen` 에는 키를 빼고 남긴다.
"""

from __future__ import annotations

import hashlib
import io
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final
from zoneinfo import ZoneInfo

import httpx

KST: Final = ZoneInfo("Asia/Seoul")
KEY: Final = "dart-sim-fake-key-" + "0" * 22  # 40자 — 길이 검사만 통과하는 가짜
API_PREFIX: Final = "/api/"
STATUS_TEXT: Final[dict[str, str]] = {
    "000": "정상",
    "010": "등록되지 않은 키입니다.",
    "013": "조회된 데이타가 없습니다.",
    "020": "요청 제한을 초과하였습니다.",
}


@dataclass(frozen=True)
class DartSeen:
    at: datetime
    consumer: str
    endpoint: str
    params: dict[str, str]
    status: str


def _seed(*parts: object) -> int:
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def corp_zip(n: int) -> bytes:
    """합성 corpCode.xml zip(DART 원본 모양 `<result><list>…</list></result>`)."""
    rows = []
    for i in range(n):
        listed = i % 2 == 0
        stock = f"99{i:04d}" if listed else " "
        rows.append(
            f"<list><corp_code>{90000000 + i:08d}</corp_code><corp_name>합성회사{i}</corp_name>"
            f"<corp_eng_name>SYNTHETIC {i}</corp_eng_name><stock_code>{stock}</stock_code>"
            f"<modify_date>20260901</modify_date></list>"
        )
    xml = f'<?xml version="1.0" encoding="UTF-8"?><result>{"".join(rows)}</result>'.encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("CORPCODE.xml", xml)
    return buf.getvalue()


class FakeDart:
    def __init__(self, now: Callable[[], datetime], *, key: str = KEY, corps: int = 40) -> None:
        self._now = now
        self.key = key
        self.corps = corps
        self.seen: list[DartSeen] = []
        self._inject: list[list[Any]] = []  # [status, count]
        self._lock = threading.Lock()

    def transport(self, consumer: str = "unknown") -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            return self.handle(req, consumer)

        return httpx.MockTransport(handle)

    def inject(self, status: str, count: int = 1) -> None:
        with self._lock:
            self._inject.append([status, count])

    def _take(self) -> str | None:
        for rule in self._inject:
            if rule[1] > 0:
                rule[1] -= 1
                return str(rule[0])
        return None

    def handle(self, req: httpx.Request, consumer: str = "unknown") -> httpx.Response:
        now = self._now()
        path = req.url.path
        endpoint = path[len(API_PREFIX) :] if path.startswith(API_PREFIX) else path
        params = {k: v for k, v in req.url.params.items() if k != "crtfc_key"}
        with self._lock:

            def status_reply(code: str) -> httpx.Response:
                self.seen.append(DartSeen(now, consumer, endpoint, params, code))
                return httpx.Response(200, json={"status": code, "message": STATUS_TEXT[code]})

            if req.url.params.get("crtfc_key") != self.key:
                return status_reply("010")
            forced = self._take()
            if forced is not None:
                return status_reply(forced)
            if endpoint == "corpCode.xml":
                self.seen.append(DartSeen(now, consumer, endpoint, params, "000"))
                return httpx.Response(200, content=corp_zip(self.corps))
            if endpoint == "list.json":
                body = self._list(now)
            elif endpoint in ("fnlttSinglAcntAll.json", "fnlttMultiAcnt.json"):
                body = {
                    "status": "000",
                    "message": "정상",
                    "list": [
                        {
                            "rcept_no": "20260814000001",
                            "corp_code": params.get("corp_code", "90000000"),
                            "account_nm": "매출액",
                            "thstrm_amount": "1000000000",
                        }
                    ],
                }
            elif endpoint == "company.json":
                body = {
                    "status": "000",
                    "message": "정상",
                    "corp_code": params.get("corp_code", "90000000"),
                    "corp_name": "합성회사",
                    "induty_code": "264",
                }
            else:
                self.seen.append(DartSeen(now, consumer, endpoint, params, "404"))
                return httpx.Response(404, text="not found")
            self.seen.append(DartSeen(now, consumer, endpoint, params, str(body["status"])))
            return httpx.Response(200, json=body)

    def _list(self, now: datetime) -> dict[str, Any]:
        minute = now.astimezone(KST).strftime("%Y%m%d%H%M")
        n = _seed("list", minute) % 3
        items = [
            {
                "corp_code": f"{90000000 + (_seed(minute, i) % self.corps):08d}",
                "corp_name": "합성회사",
                "stock_code": "",
                "corp_cls": "Y",
                "report_nm": "합성 주요사항보고서",
                "rcept_no": f"{minute}{i:02d}",
                "flr_nm": "합성회사",
                "rcept_dt": minute[:8],
                "rm": "",
            }
            for i in range(n)
        ]
        return {
            "status": "000",
            "message": "정상",
            "page_no": 1,
            "page_count": 100,
            "total_count": n,
            "total_page": 1,
            "list": items,
        }

    # ── 조회(시험) ───────────────────────────────────────────────────────────────────
    def calls(self, endpoint: str | None = None) -> list[DartSeen]:
        with self._lock:
            return [s for s in self.seen if endpoint in (None, s.endpoint)]


__all__ = ["KEY", "DartSeen", "FakeDart", "corp_zip"]
