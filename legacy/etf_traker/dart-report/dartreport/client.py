"""DART OpenAPI 클라이언트.

인증키는 환경변수 DART_API_KEY 에서 읽는다 (코드에 하드코딩 금지).
로컬 캐시(.cache/)를 두어 같은 호출을 반복하지 않는다 — 하루 20,000건 한도 보호.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

BASE = "https://opendart.fss.or.kr/api"

# 보고서 코드 → (분기 라벨, 누적 개월수)
REPRT = {
    "11013": ("1Q", 3),
    "11012": ("2Q", 6),
    "11014": ("3Q", 9),
    "11011": ("4Q", 12),
}

# DART 상태코드 중 "정상적인 빈 결과"로 취급할 것
EMPTY_STATUSES = {"013"}


class DartError(RuntimeError):
    def __init__(self, status: str, message: str, endpoint: str):
        super().__init__(f"[{endpoint}] DART {status}: {message}")
        self.status = status
        self.message = message


@dataclass
class DartClient:
    api_key: str | None = None
    cache_dir: Path = Path(".cache")
    throttle: float = 0.12  # 초, 연속 호출 간 최소 간격
    timeout: int = 30

    def __post_init__(self) -> None:
        self.api_key = self.api_key or os.environ.get("DART_API_KEY", "").strip()
        if not self.api_key:
            raise SystemExit(
                "DART_API_KEY 환경변수가 없습니다.\n"
                "  로컬:  export DART_API_KEY='발급받은40자리키'\n"
                "  Actions: repo Settings > Secrets > Actions 에 DART_API_KEY 추가"
            )
        if len(self.api_key) != 40:
            print(f"[warn] 인증키 길이가 40자가 아닙니다 ({len(self.api_key)}자). 오타 확인 필요.")
        self.cache_dir = Path(self.cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._session = requests.Session()
        self._last_call = 0.0

    # ------------------------------------------------------------------ 내부

    def _cache_path(self, endpoint: str, params: dict[str, Any], ext: str) -> Path:
        keyed = {k: v for k, v in sorted(params.items()) if k != "crtfc_key"}
        digest = hashlib.sha1(
            f"{endpoint}|{json.dumps(keyed, ensure_ascii=False)}".encode()
        ).hexdigest()[:16]
        return self.cache_dir / f"{endpoint}_{digest}.{ext}"

    def _wait(self) -> None:
        delta = time.monotonic() - self._last_call
        if delta < self.throttle:
            time.sleep(self.throttle - delta)
        self._last_call = time.monotonic()

    def _get(self, endpoint: str, params: dict[str, Any]) -> requests.Response:
        self._wait()
        resp = self._session.get(
            f"{BASE}/{endpoint}",
            params={**params, "crtfc_key": self.api_key},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp

    def get_json(
        self, endpoint: str, params: dict[str, Any], *, use_cache: bool = True
    ) -> dict[str, Any]:
        """JSON 엔드포인트 호출. 빈 결과(013)는 예외 대신 list=[] 로 돌려준다."""
        cache = self._cache_path(endpoint, params, "json")
        if use_cache and cache.exists():
            return json.loads(cache.read_text(encoding="utf-8"))

        data = self._get(endpoint, params).json()
        status = data.get("status")
        if status in EMPTY_STATUSES:
            data = {"status": status, "message": data.get("message", ""), "list": []}
        elif status != "000":
            raise DartError(status, data.get("message", ""), endpoint)

        cache.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def get_zip(
        self, endpoint: str, params: dict[str, Any], *, use_cache: bool = True
    ) -> zipfile.ZipFile | None:
        """ZIP 을 반환하는 엔드포인트(corpCode, document) 호출."""
        cache = self._cache_path(endpoint, params, "zip")
        if use_cache and cache.exists():
            return zipfile.ZipFile(cache)

        resp = self._get(endpoint, params)
        body = resp.content
        # 실패 시 XML 에러 본문이 온다
        if not body.startswith(b"PK"):
            head = body[:400].decode("utf-8", "replace")
            if "013" in head:
                return None
            raise DartError("?", head, endpoint)

        cache.write_bytes(body)
        return zipfile.ZipFile(io.BytesIO(body))

    # ------------------------------------------------------------------ 공개 API

    def corp_code(self, stock_code: str) -> tuple[str, str]:
        """종목코드(6자리) → (corp_code 8자리, 회사명)."""
        stock_code = str(stock_code).zfill(6)
        zf = self.get_zip("corpCode.xml", {})
        if zf is None:
            raise DartError("?", "고유번호 파일을 받지 못했습니다", "corpCode.xml")

        from lxml import etree

        with zf.open(zf.namelist()[0]) as fh:
            tree = etree.parse(fh)
        for node in tree.iter("list"):
            if (node.findtext("stock_code") or "").strip() == stock_code:
                return (
                    node.findtext("corp_code").strip(),
                    (node.findtext("corp_name") or "").strip(),
                )
        raise SystemExit(
            f"종목코드 {stock_code} 를 DART 고유번호 목록에서 찾지 못했습니다. "
            "상장 종목코드가 맞는지 확인하세요."
        )

    def financials(
        self, corp_code: str, year: int, reprt_code: str, fs_div: str = "CFS"
    ) -> list[dict[str, Any]]:
        """단일회사 전체 재무제표. fs_div: CFS(연결) | OFS(별도).

        연결 미작성 회사는 CFS 가 비므로 호출부에서 OFS 로 폴백한다.
        """
        data = self.get_json(
            "fnlttSinglAcntAll.json",
            {
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        return data.get("list", [])

    def disclosures(
        self,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        pblntf_ty: str | None = None,
        max_pages: int = 20,
    ) -> list[dict[str, Any]]:
        """공시목록 전체 페이지 수집. bgn_de/end_de: 'YYYYMMDD'."""
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            params = {
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page),
                "page_count": "100",
            }
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            data = self.get_json("list.json", params)
            rows = data.get("list", [])
            out.extend(rows)
            if page >= int(data.get("total_page", 1) or 1):
                break
        return out

    def document_texts(self, rcept_no: str) -> list[tuple[str, str]]:
        """공시원문 ZIP → [(파일명, 본문문자열)]. DART 원문은 EUC-KR 인 경우가 많다."""
        zf = self.get_zip("document.xml", {"rcept_no": rcept_no})
        if zf is None:
            return []
        out = []
        for name in zf.namelist():
            raw = zf.read(name)
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    out.append((name, raw.decode(enc)))
                    break
                except UnicodeDecodeError:
                    continue
            else:
                out.append((name, raw.decode("utf-8", "replace")))
        return out
