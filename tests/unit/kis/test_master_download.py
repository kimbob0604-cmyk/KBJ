"""KIS 마스터 내려받기 — 시간 제한·크기 상한·HTTP 오류(가짜 transport).

GEXLAB `tests/unit/test_scheduler_service.py` 의 내려받기 시험 3개(:487·:498·:511)를 옮겼다(import
경로만 바꿈). 시험용 zip 은 GX 가짜 체인(`tests/fakes/kis_server.default_chain`) 대신 합성 마스터 줄
fixture(`tests/fixtures/synthetic/kis/master_lines.json`)로 만든다 — 시험은 바이트를 그대로
돌려받는지만 본다. 새로: `download_fo_master` 가 같은 길로 받는다.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest

from kbj.data.private.kis.master import (
    FO_MASTER_FILE,
    download_fo_master,
    http_master_downloader,
    parse_master_zip,
)

FIX = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "synthetic" / "kis"
MASTER_TEXT = (
    "\n".join(json.loads((FIX / "master_lines.json").read_text(encoding="utf-8"))["lines"]) + "\n"
)


def master_zip(text: str = MASTER_TEXT) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo_idx_code_mts.mst", text.encode("cp949"))
    return buf.getvalue()


class Chunks(httpx.SyncByteStream):
    def __init__(self, parts: list[bytes]) -> None:
        self.parts = parts

    def __iter__(self) -> Any:
        yield from self.parts


def _transport(status: int, parts: list[bytes], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(status, stream=Chunks(parts))

    return httpx.MockTransport(handle)


def test_http_master_downloader_reads_the_zip_through_the_transport() -> None:
    seen: list[httpx.Request] = []
    data = master_zip()
    half = len(data) // 2
    get = http_master_downloader(transport=_transport(200, [data[:half], data[half:]], seen))
    assert get() == data
    (req,) = seen
    assert req.method == "GET" and req.url.path.endswith("fo_idx_code_mts.mst.zip")
    assert req.extensions["timeout"] == {"connect": 5.0, "read": 15.0, "write": 15.0, "pool": 15.0}


def test_http_master_downloader_gives_up_after_the_total_deadline() -> None:
    t = [0.0]

    def clock() -> float:
        t[0] += 11.0  # 조각마다 11초 — 30초를 넘는다
        return t[0]

    parts = [b"x" * 10] * 10
    get = http_master_downloader(transport=_transport(200, parts, []), total_s=30.0, clock=clock)
    with pytest.raises(TimeoutError, match="30초"):
        get()


def test_http_master_downloader_refuses_errors_and_oversized_files() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        http_master_downloader(transport=_transport(404, [b"nope"], []))()
    big = http_master_downloader(transport=_transport(200, [b"x" * 60] * 2, []), max_bytes=100)
    with pytest.raises(ValueError, match="100B"):
        big()


# ── 새로 ──


def test_download_fo_master_takes_the_same_path_and_parses() -> None:
    seen: list[httpx.Request] = []
    data = master_zip()
    got = download_fo_master(transport=_transport(200, [data], seen))
    assert got == data
    (req,) = seen
    assert req.url.scheme == "https" and req.url.path.endswith("/" + FO_MASTER_FILE)
    assert not req.url.query  # 키·토큰을 싣지 않는다(공개 배포 파일)
    assert "authorization" not in req.headers and "appkey" not in req.headers
    assert len(parse_master_zip(got)) == len(MASTER_TEXT.splitlines())
