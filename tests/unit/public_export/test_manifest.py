"""내용 검사 — 공개 산출물에 로그인 등급 출처 이름·로그인 API 흔적이 하나라도 있으면 실패(§7.4).

검사가 빈말이 아님을 보이려고 위반을 하나씩 심어 잡히는지 본다. 문제 문구에는 값이 아니라 규칙·
JSON 경로만 담긴다.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from kbj.core.time import KST
from kbj.services.public_export.manifest import (
    DART_ONLY_NAMES,
    DART_SUBDIRS,
    DIR_CAPS,
    FILE_CAPS,
    LOGIN_SOURCES,
    MANIFEST_NAME,
    NAME_RE,
    P5_FLAT_NAMES,
    SUBDIR_JSON_RE,
    SUBDIR_XLSX_RE,
    SUBDIRS,
    BlobFile,
    PublicFile,
    check_payload,
    check_tree,
    dir_digest,
    forbidden_hits,
    listed_paths,
    login_source_paths,
    non_dart_source_paths,
    sha256_bytes,
    sha256_text,
    watch_trace_paths,
)

ROOT = Path(__file__).resolve().parents[3]

NOW = datetime(2026, 10, 7, 5, 30, tzinfo=KST)


def _env(**over: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "source": "KBJ 거래 캘린더(코드 계산)",
        "as_of": "2026-10-07T05:30:00+09:00",
        "quality": "ok",
        "notes": [],
        "generated_at": "2026-10-07T05:30:02+09:00",
        "data": {"days": []},
    }
    env.update(over)
    return env


def test_clean_envelope_passes() -> None:
    assert check_payload("calendar.json", json.dumps(_env(), ensure_ascii=False)) == []


@pytest.mark.parametrize(
    "src",
    [
        "KIS",
        "KRX",
        "krx+kis",
        "KIS(잠정)",
        "ETF_ISSUERS:삼성",
        "Yahoo",
        "NAVER",
        "KRX가 공표",
        "한국거래소",
        "네이버 금융",
    ],
)
def test_login_source_names_rejected_at_any_depth(src: str) -> None:
    top = check_payload("x.json", json.dumps(_env(source=src), ensure_ascii=False))
    assert any("로그인 등급 출처" in p for p in top)
    deep = _env(data={"rows": [{"v": 1, "source": src}]})
    probs = check_payload("x.json", json.dumps(deep, ensure_ascii=False))
    assert any("$.data.rows[0].source" in p for p in probs)
    assert all(src not in p for p in probs)  # 값은 문구에 싣지 않는다


@pytest.mark.parametrize(
    "src", ["XKRX 달력", "KBJ 거래 캘린더(코드 계산)", "한국은행 공표 일정", "DART", "KOSIS"]
)
def test_public_names_allowed(src: str) -> None:
    assert not LOGIN_SOURCES.search(src)


@pytest.mark.parametrize(
    "needle",
    [
        "/api/market/summary",
        "/telegram/webhook",
        "auth/login",
        "X-KBJ-CSRF",
        "KBJ_WEB_USER",
        "__Host-kbj_session",
    ],
)
def test_login_api_traces_rejected(needle: str) -> None:
    text = json.dumps(_env(notes=[f"see {needle}"]), ensure_ascii=False)
    probs = check_payload("x.json", text)
    assert any("로그인 API 흔적" in p for p in probs)
    assert forbidden_hits(text)


@pytest.mark.parametrize(
    ("over", "word"),
    [
        ({"as_of": "2026-10-07T05:30:00"}, "as_of"),
        ({"generated_at": "2026-10-07"}, "generated_at"),
        ({"quality": "good"}, "quality"),
        ({"source": ""}, "source"),
    ],
)
def test_envelope_fields_required(over: dict[str, Any], word: str) -> None:
    probs = check_payload("x.json", json.dumps(_env(**over)))
    assert any(word in p for p in probs)


def test_not_json_and_not_envelope() -> None:
    assert check_payload("x.json", "{") == ["x.json: JSON 이 아님"]
    assert any("봉투가 아님" in p for p in check_payload("x.json", "[1, 2]"))
    no_data = {k: v for k, v in _env().items() if k != "data"}
    assert any("봉투 필드 없음" in p for p in check_payload("x.json", json.dumps(no_data)))


def test_login_source_paths_only_source_keys() -> None:
    v = {"name": "KRX", "rows": [{"source": "KIS"}, {"source": "DART"}]}
    assert login_source_paths(v) == ["$.rows[0].source"]


def test_public_file_validates() -> None:
    with pytest.raises(ValueError):
        PublicFile(name="../x.json", source="s", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name=MANIFEST_NAME, source="s", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name="x.json", source=" ", as_of=NOW, quality="ok", data={})
    with pytest.raises(ValueError):
        PublicFile(name="x.json", source="s", as_of=datetime(2026, 10, 7), quality="ok", data={})  # noqa: DTZ001


def _tree(tmp_path: Path) -> Path:
    from kbj.services.public_export.export import write_files

    f = PublicFile(
        name="calendar.json",
        source="KBJ 거래 캘린더(코드 계산)",
        as_of=NOW,
        quality="ok",
        data={"days": []},
    )
    write_files([f], tmp_path, now=NOW)
    return tmp_path


def test_check_tree_clean(tmp_path: Path) -> None:
    assert check_tree(_tree(tmp_path)) == []


def test_check_tree_detects_tamper_extra_and_missing(tmp_path: Path) -> None:
    d = _tree(tmp_path)
    cal = d / "calendar.json"
    env = json.loads(cal.read_text(encoding="utf-8"))
    env["data"]["rows"] = [{"source": "KRX"}]
    cal.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    (d / "stray.json").write_text("{}", encoding="utf-8")
    probs = check_tree(d)
    assert any("sha256 불일치" in p for p in probs)
    assert any("로그인 등급 출처" in p for p in probs)
    assert any("stray.json" in p and "manifest 에 없는" in p for p in probs)
    cal.unlink()
    assert any("파일이 없음" in p for p in check_tree(d))


def test_check_tree_meta_mismatch_and_no_manifest(tmp_path: Path) -> None:
    d = _tree(tmp_path)
    m = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    m["files"][0]["quality"] = "stale"
    (d / MANIFEST_NAME).write_text(json.dumps(m), encoding="utf-8")
    assert any("quality" in p and "다름" in p for p in check_tree(d))
    assert check_tree(tmp_path / "nothing") == ["manifest.json: 없음"]


# ── P4 — 하위 폴더·DART 전용 파일(docs/p4_design.md §1.9·§7) ─────────────────────────────────


@pytest.mark.parametrize(
    "name", ["fin/Q00001.json", "fin/005930.json", "reports/A12345.json", "corp_index.json"]
)
def test_subdir_names_allowed(name: str) -> None:
    PublicFile(name=name, source="DART", as_of=NOW, quality="ok", data={})


@pytest.mark.parametrize(
    "name",
    [
        "fin/q00001.json",  # 소문자 코드
        "fin/Q0001.json",
        "fin/../x.json",
        "other/Q00001.json",
        "fin/Q00001/x.json",
        "reports/Q00001.xlsx",  # 바이너리는 BlobFile
        "fin/Q00001.JSON",
    ],
)
def test_bad_subdir_names_refused(name: str) -> None:
    with pytest.raises(ValueError):
        PublicFile(name=name, source="DART", as_of=NOW, quality="ok", data={})


def test_blob_names_and_size() -> None:
    BlobFile("reports/Q00001.xlsx", b"x")
    for bad in ("fin/Q00001.xlsx", "reports/q00001.xlsx", "Q00001.xlsx"):
        with pytest.raises(ValueError):
            BlobFile(bad, b"x")
    with pytest.raises(ValueError, match="초과"):
        BlobFile("reports/Q00001.xlsx", b"x" * (2 * 1024 * 1024 + 1))


def test_subdir_render_is_compact() -> None:
    f = PublicFile(name="fin/Q00001.json", source="DART", as_of=NOW, quality="ok", data={"a": [1]})
    text = f.render(NOW)
    assert "\n " not in text and text.endswith("}\n")
    flat = PublicFile(name="x.json", source="DART", as_of=NOW, quality="ok", data={"a": [1]})
    assert "\n " in flat.render(NOW)  # P3 파일은 그대로(들여쓰기)


@pytest.mark.parametrize("name", [*sorted(DART_ONLY_NAMES), "fin/Q00001.json"])
def test_dart_only_files_reject_other_sources_watch_and_prv(name: str) -> None:
    ok = _env(source="DART", data={"rows": [{"source": "DART"}]})
    assert check_payload(name, json.dumps(ok)) == []
    other = _env(source="DART", data={"rows": [{"source": "한국은행"}]})
    assert any(
        "DART 가 아님($.data.rows[0].source)" in p for p in check_payload(name, json.dumps(other))
    )
    top = _env(source="KOSIS")
    assert any("DART 가 아님($.source)" in p for p in check_payload(name, json.dumps(top)))
    watch = _env(source="DART", data={"rows": [{"is_watch": False}]})
    assert any("관심종목 흔적" in p for p in check_payload(name, json.dumps(watch)))
    prv = _env(source="DART", notes=["prv_journal.watchlist"])
    assert any("스키마 이름" in p for p in check_payload(name, json.dumps(prv)))


def test_p3_files_keep_their_own_sources() -> None:
    env = _env(source="KBJ 거래 캘린더(코드 계산)", data={"watchers": 1})
    assert check_payload("calendar.json", json.dumps(env, ensure_ascii=False)) == []


def test_watch_and_source_path_helpers() -> None:
    v = {"a": [{"watchlist": 1, "source": "DART"}, {"x": {"source": "X"}}]}
    assert watch_trace_paths(v) == ["$.a[0].watchlist"]
    assert non_dart_source_paths(v) == ["$.a[1].x.source"]


def test_dir_digest_order_independent() -> None:
    a = [("fin/A00001.json", "1" * 64), ("fin/A00002.json", "2" * 64)]
    assert dir_digest(a) == dir_digest(reversed(a))
    assert dir_digest(a) != dir_digest(a[:1])
    lines = "fin/A00001.json\t" + "1" * 64 + "\nfin/A00002.json\t" + "2" * 64 + "\n"
    assert dir_digest(a) == sha256_text(lines)


def _p4_tree(tmp_path: Path) -> Path:
    from kbj.services.public_export.export import write_files

    files = [
        PublicFile(name="fin/Q00001.json", source="DART", as_of=NOW, quality="ok", data={"a": 1}),
        PublicFile(name="fin/Q00002.json", source="DART", as_of=NOW, quality="estimated", data={}),
        PublicFile(
            name="reports/Q00001.json",
            source="DART",
            as_of=NOW,
            quality="ok",
            data={"xlsx_sha256": sha256_bytes(b"xl")},
        ),
    ]
    write_files(files, tmp_path, now=NOW, blobs=[BlobFile("reports/Q00001.xlsx", b"xl")])
    return tmp_path


def test_check_tree_subdirs_clean_and_listed_paths(tmp_path: Path) -> None:
    d = _p4_tree(tmp_path)
    assert check_tree(d) == []
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [(x["name"], x["count"], x["quality"]) for x in man["dirs"]] == [
        ("fin", 2, "estimated"),
        ("reports", 2, "ok"),
    ]
    assert listed_paths(d) == [
        "fin/Q00001.json",
        "fin/Q00002.json",
        "reports/Q00001.json",
        "reports/Q00001.xlsx",
    ]


def test_check_tree_subdir_tamper_extra_bad_name(tmp_path: Path) -> None:
    d = _p4_tree(tmp_path)
    (d / "fin" / "Q00003.json").write_text("{}", encoding="utf-8")
    (d / "fin" / "notes.txt").write_text("x", encoding="utf-8")
    probs = check_tree(d)
    assert any("fin/: manifest 개수와 다름" in p for p in probs)
    assert any("fin/: manifest 요약 해시와 다름" in p for p in probs)
    assert any("fin/notes.txt: 허용하지 않는 이름" in p for p in probs)
    assert any("fin/Q00003.json: 봉투" in p for p in probs)


def test_check_tree_xlsx_pairing(tmp_path: Path) -> None:
    d = _p4_tree(tmp_path)
    (d / "reports" / "Q00001.xlsx").write_bytes(b"changed")
    assert any("xlsx_sha256 과 다름" in p for p in check_tree(d))
    (d / "reports" / "Q00001.xlsx").unlink()
    assert any("파일이 없음" in p for p in check_tree(d))


def test_check_tree_unlisted_or_unknown_subdir(tmp_path: Path) -> None:
    d = _tree(tmp_path)  # P3 모양(하위 폴더 없음)
    (d / "fin").mkdir()
    (d / "fin" / "Q00001.json").write_text("{}", encoding="utf-8")
    assert any("manifest 에 없는 하위 폴더" in p for p in check_tree(d))
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    man["dirs"] = [{"name": "secret", "count": 0}]
    (d / MANIFEST_NAME).write_text(json.dumps(man), encoding="utf-8")
    assert any("secret/: 허용하지 않는" in p for p in check_tree(d))


def test_p3_manifest_without_dirs_still_reads(tmp_path: Path) -> None:
    d = _tree(tmp_path)
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    del man["dirs"]
    (d / MANIFEST_NAME).write_text(json.dumps(man), encoding="utf-8")
    assert check_tree(d) == []
    assert listed_paths(d) == ["calendar.json"]


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node 가 없다")
def test_js_merge_rules_match_python() -> None:
    """merge-public-data.mjs 의 이름 규칙·DART 전용 목록이 이 모듈과 같다(두 검사기가 같은 규칙)."""
    script = (
        "import * as m from './web/scripts/merge-public-data.mjs';\n"
        "console.log(JSON.stringify({sj: m.SUBDIR_JSON_RE.source, sx: m.SUBDIR_XLSX_RE.source,"
        " subdirs: m.SUBDIRS, dart: [...m.DART_ONLY].sort(), name: m.NAME_RE.source,"
        " dsub: m.DART_SUBDIRS, p5: [...m.P5_FLAT_NAMES].sort(), dcaps: m.DIR_CAPS,"
        " fcaps: m.FILE_CAPS,"
        " digest: m.dirDigest([['fin/A00002.json', '2'.repeat(64)],"
        " ['fin/A00001.json', '1'.repeat(64)]])}));\n"
    )
    assert NODE is not None
    p = subprocess.run(  # noqa: S603
        [NODE, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    js = json.loads(p.stdout)
    assert js["sj"] == "^" + SUBDIR_JSON_RE.pattern.replace("/", "\\/") + "$"
    assert js["sx"] == "^" + SUBDIR_XLSX_RE.pattern.replace("/", "\\/") + "$"
    assert js["name"] == "^" + NAME_RE.pattern + "$"
    assert tuple(js["subdirs"]) == SUBDIRS
    assert js["dart"] == sorted(DART_ONLY_NAMES)
    assert tuple(js["dsub"]) == DART_SUBDIRS
    assert js["p5"] == sorted(P5_FLAT_NAMES)
    assert js["dcaps"] == DIR_CAPS
    assert js["fcaps"] == FILE_CAPS
    assert js["digest"] == dir_digest(
        [("fin/A00001.json", "1" * 64), ("fin/A00002.json", "2" * 64)]
    )


# ── P5 공개 파일(docs/p5_design.md §7): macro/·credit.json(테마 themes/ 는 P5 제외) ──


@pytest.mark.parametrize(
    "name", ["macro/tabs.json", "macro/rates.json", "macro/calendar.json", "macro/us_2.json"]
)
def test_p5_subdir_names_allowed(name: str) -> None:
    PublicFile(name=name, source="ECOS", as_of=NOW, quality="ok", data={})


@pytest.mark.parametrize(
    "name",
    [
        "macro/Rates.json",
        "macro/a-b.json",
        "macro/x/y.json",
        "macro/../x.json",
        "macro/.json",
        "themes/index.json",
    ],
)
def test_p5_bad_subdir_names_refused(name: str) -> None:
    with pytest.raises(ValueError):
        PublicFile(name=name, source="ECOS", as_of=NOW, quality="ok", data={})


def test_p5_subdirs_and_caps_declared() -> None:
    assert set(DART_SUBDIRS) < set(SUBDIRS)
    assert set(SUBDIRS) - set(DART_SUBDIRS) == {"macro", "trade", "diag"}  # trade P6·diag P7(아래)
    assert DIR_CAPS["macro"] == 3 * 1024 * 1024
    assert {k: v for k, v in FILE_CAPS.items() if not k.startswith(("trade/", "diag/"))} == {
        "credit.json": 100 * 1024,
        "macro/calendar.json": 200 * 1024,
    }
    assert "credit.json" in P5_FLAT_NAMES and not DART_ONLY_NAMES & P5_FLAT_NAMES


@pytest.mark.parametrize("name", ["credit.json", "macro/rates.json", "macro/calendar.json"])
def test_p5_files_not_dart_only_but_no_watch_prv_or_login(name: str) -> None:
    ok = _env(source="ECOS", data={"rows": [{"source": "KOSIS"}, {"source": "DATAGO"}]})
    assert check_payload(name, json.dumps(ok)) == []
    watch = _env(source="ECOS", data={"rows": [{"watchlist": True}]})
    assert any("관심종목 흔적" in p for p in check_payload(name, json.dumps(watch)))
    prv = _env(source="ECOS", notes=["prv_macro.series"])
    assert any("스키마 이름" in p for p in check_payload(name, json.dumps(prv)))
    login = _env(source="ECOS", data={"rows": [{"source": "YAHOO"}]})
    assert any("로그인 등급 출처" in p for p in check_payload(name, json.dumps(login)))


def _p5_tree(tmp_path: Path, *, calendar_bytes: int = 10) -> Path:
    from kbj.services.public_export.export import write_files

    files = [
        PublicFile(name="credit.json", source="DATAGO", as_of=NOW, quality="ok", data={"a": 1}),
        PublicFile(name="macro/rates.json", source="ECOS", as_of=NOW, quality="ok", data={}),
        PublicFile(
            name="macro/calendar.json",
            source="KBJ",
            as_of=NOW,
            quality="stale",
            data={"pad": "x" * calendar_bytes},
        ),
        PublicFile(name="macro/us.json", source="FRED", as_of=NOW, quality="ok", data={}),
    ]
    write_files(files, tmp_path, now=NOW)
    return tmp_path


def test_check_tree_p5_dirs_clean(tmp_path: Path) -> None:
    d = _p5_tree(tmp_path)
    assert check_tree(d) == []
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [(x["name"], x["source"], x["quality"]) for x in man["dirs"]] == [
        ("macro", "ECOS,FRED,KBJ", "stale"),
    ]
    assert [e["name"] for e in man["files"]] == ["credit.json"]
    assert sorted(listed_paths(d)) == [
        "credit.json",
        "macro/calendar.json",
        "macro/rates.json",
        "macro/us.json",
    ]


def test_check_tree_p5_caps_and_dir_source(tmp_path: Path) -> None:
    d = _p5_tree(tmp_path)
    cal = d / "macro" / "calendar.json"
    cal.write_text(cal.read_text(encoding="utf-8") + " " * (200 * 1024), encoding="utf-8")
    assert any("macro/calendar.json: 크기 상한" in p for p in check_tree(d))
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    man["dirs"][0]["source"] = ""
    (d / MANIFEST_NAME).write_text(json.dumps(man), encoding="utf-8")
    assert any("macro/: source 없음" in p for p in check_tree(d))


def test_write_files_refuses_p5_over_cap(tmp_path: Path) -> None:
    from kbj.services.public_export.export import PublicExportError

    with pytest.raises(PublicExportError, match="상한"):
        _p5_tree(tmp_path, calendar_bytes=200 * 1024)
    assert not (tmp_path / MANIFEST_NAME).exists()


# ── P6 공개 파일(docs/p6_design.md §7): trade/ (평평한 + 코드 파일 한 단계 더) ──


@pytest.mark.parametrize(
    ("name", "ok"),
    [
        ("trade/flash.json", True),
        ("trade/hs2.json", True),
        ("trade/hs/85.json", True),
        ("trade/hs/8542.json", True),
        ("trade/country/US.json", True),
        ("trade/region/854232.json", True),
        ("trade/stock/000660.json", True),
        ("trade/hs/8.json", False),
        ("trade/Flash.json", False),
        ("trade/x/85.json", False),
        ("trade/hs/85/x.json", False),
        ("trade/stock/00066!.json", False),
    ],
)
def test_p6_trade_names(name: str, ok: bool) -> None:
    assert bool(SUBDIR_JSON_RE.fullmatch(name)) is ok


def test_p6_caps_match_trade_yaml() -> None:
    from kbj.config.trade import load_trade
    from kbj.services.public_export.manifest import PREFIX_CAPS, file_cap

    pe = load_trade().public_export
    kb = 1024
    assert "trade" in SUBDIRS and "trade" not in DART_SUBDIRS
    assert DIR_CAPS["trade"] == pe.dir_cap_mb * kb * kb
    assert FILE_CAPS["trade/flash.json"] == pe.flash_cap_kb * kb
    assert FILE_CAPS["trade/ribbon.json"] == 4 * kb  # 띠 칩(FLASH — WRAP 이 상한을 더했다)
    for n in ("hs2", "countries", "surge", "items104", "stocks"):
        assert FILE_CAPS[f"trade/{n}.json"] == pe.file_cap_kb * kb, n
    assert PREFIX_CAPS["trade/region/"] == pe.region_cap_kb * kb
    assert PREFIX_CAPS["trade/stock/"] == pe.stock_cap_kb * kb
    assert PREFIX_CAPS["trade/hs/"] == PREFIX_CAPS["trade/country/"] == pe.file_cap_kb * kb
    assert file_cap("trade/stock/000660.json") == pe.stock_cap_kb * kb
    assert file_cap("trade/unknown.json") is None and file_cap("credit.json") == 100 * kb


def test_p6_trade_payload_rules() -> None:
    from kbj.services.public_export.manifest import trade_problems

    ok = _env(
        source="DATAGO,DART", data={"lines": [{"origin": "public"}], "fx_note": "환율 미조정"}
    )
    assert check_payload("trade/stock/000660.json", json.dumps(ok)) == []
    local = _env(source="DATAGO", data={"rows": [{"code": "000660", "origin": "local"}]})
    assert any("origin local" in p for p in check_payload("trade/stocks.json", json.dumps(local)))
    price = _env(source="DATAGO", data={"monthly": [{"period": "2026-09", "close": 1}]})
    assert any("시세 칸" in p for p in check_payload("trade/stock/000660.json", json.dumps(price)))
    assert trade_problems("trade/flash.json", {"data": {"close": 1}}) == []  # 시세 칸은 stock 만
    assert trade_problems("macro/us.json", {"origin": "local"}) == []
    watch = _env(source="DATAGO", data={"rows": [{"is_watch": True}]})
    assert any("관심종목 흔적" in p for p in check_payload("trade/hs/85.json", json.dumps(watch)))


def _p6_tree(tmp_path: Path) -> Path:
    from kbj.services.public_export.export import write_files

    files = [
        PublicFile(name="trade/flash.json", source="DATAGO", as_of=NOW, quality="estimated",
                   data={"segments": []}),
        PublicFile(name="trade/hs/85.json", source="DATAGO", as_of=NOW, quality="ok", data={}),
        PublicFile(name="trade/stock/000660.json", source="DART,DATAGO", as_of=NOW, quality="ok",
                   data={"lines": []}),
    ]  # fmt: skip
    write_files(files, tmp_path, now=NOW)
    return tmp_path


def test_p6_nested_tree_is_written_checked_and_cleaned(tmp_path: Path) -> None:
    from kbj.services.public_export.export import write_files

    d = _p6_tree(tmp_path)
    assert check_tree(d) == []
    man = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert [(x["name"], x["count"]) for x in man["dirs"]] == [("trade", 3)]
    assert sorted(listed_paths(d)) == [
        "trade/flash.json", "trade/hs/85.json", "trade/stock/000660.json",
    ]  # fmt: skip
    # 다음 회차에 빠진 코드 파일·빈 폴더는 지운다(한 단계 더 깊은 곳까지)
    write_files(
        [PublicFile(name="trade/flash.json", source="DATAGO", as_of=NOW, quality="ok", data={})],
        d,
        now=NOW,
    )
    assert not (d / "trade" / "hs").exists() and not (d / "trade" / "stock").exists()
    assert check_tree(d) == []
    # 코드 파일 상한(PREFIX_CAPS)을 넘으면 실패 — 자르지 않는다
    big = PublicFile(name="trade/stock/000660.json", source="DATAGO", as_of=NOW, quality="ok",
                     data={"pad": "x" * (101 * 1024)})  # fmt: skip
    with pytest.raises(Exception, match="상한"):
        write_files([big], d, now=NOW)


# ── P7 diag/(docs/p7_design.md §7 — 공공지표 진단·게이지·이벤트, 로그인 판정 키 0) ──


def test_p7_diag_subdir_caps_and_login_keys() -> None:
    from kbj.services.public_export.manifest import check_payload, diag_problems

    assert "diag" in SUBDIRS and "diag" not in DART_SUBDIRS
    assert DIR_CAPS["diag"] == 1024 * 1024
    assert {k for k in FILE_CAPS if k.startswith("diag/")} == {
        "diag/summary.json", "diag/history.json", "diag/events.json",
    }  # fmt: skip
    f = PublicFile(name="diag/summary.json", source="ECOS,DATAGO", as_of=NOW, quality="ok",
                   data={"gauge": {"label": "neutral"}})  # fmt: skip
    assert f.name == "diag/summary.json"
    bad = {"data": {"fear_greed": 50, "rows": [{"gex_flip": 1}], "verdict": "risk_on"}}
    got = diag_problems("diag/summary.json", bad)
    assert len(got) == 3 and all("로그인 판정 키" in p for p in got)
    assert diag_problems("trade/flash.json", bad) == []
    text = (
        '{"source":"ECOS","as_of":"2026-10-13T00:00:00+00:00","quality":"ok",'
        '"generated_at":"2026-10-13T00:00:00+00:00","data":{"verdict":"x"}}'
    )
    assert any("로그인 판정 키" in p for p in check_payload("diag/summary.json", text))


# ── P9 원시세 칸(docs/p9_design.md §3.2 price_fields, ADR 0035) ──


def test_p9_price_fields_reject_raw_price_keys_anywhere() -> None:
    """공개 JSON 어디에도 원시세 키가 없어야 한다 — 평평한 파일·하위 폴더 모두.

    옛 trade/stock 규칙(D-P6-4)의 일반화."""
    from kbj.services.public_export.manifest import PRICE_KEYS, price_problems

    assert {"close", "open", "high", "low", "price", "volume", "turnover", "mktcap", "nav",
            "net_value", "chg_pct"} == PRICE_KEYS  # fmt: skip
    for key in sorted(PRICE_KEYS):
        doc = _env(source="DART", data={"rows": [{"code": "Q00001", key: 12345}]})
        got = check_payload("events.json", json.dumps(doc))
        assert f"events.json: 시세 칸($.data.rows[0].{key})" in got, key
        assert not any("12345" in p for p in got)  # 값은 싣지 않는다
    assert price_problems("trade/flash.json", {"data": {"turnover": 1}}) == [
        "trade/flash.json: 시세 칸($.data.turnover)"
    ]
    assert price_problems("macro/rates.json", {"data": {"series": [{"value": 3.1}]}}) == []


def test_p9_price_field_allow_needs_file_key_and_shape() -> None:
    from kbj.services.public_export.manifest import PRICE_FIELD_ALLOW, price_problems

    assert all(len(why) >= 4 for _f, _k, _kind, why in PRICE_FIELD_ALLOW)  # 사유 필수
    assert {kind for _f, _k, kind, _w in PRICE_FIELD_ALLOW} <= {"hhmm", "number"}
    ok = {"data": {"session": {"open": "09:00", "close": "15:30"}, "days": [{"open": None}]}}
    assert price_problems("calendar.json", ok) == []
    assert price_problems("calendar.json", {"data": {"open": 2650.5}}) == [
        "calendar.json: 시세 칸 예외의 값 모양이 다르다($.data.open — hhmm)"
    ]
    assert price_problems("calendar.json", {"data": {"nav": 1}}) == [
        "calendar.json: 시세 칸($.data.nav)"
    ]
    assert price_problems("overhang_recent.json", {"data": {"rows": [{"price": 5000.0}]}}) == []
    assert price_problems("overhang_recent.json", {"data": {"rows": [{"price": "5천"}]}}) != []
    assert price_problems("events.json", {"data": {"open": "09:00"}}) == [
        "events.json: 시세 칸($.data.open)"
    ]


def test_p9_price_fields_match_js() -> None:
    """merge-public-data.mjs 의 PRICE_KEYS·PRICE_FIELD_ALLOW·priceProblems 가 이 모듈과 같다."""
    from kbj.services.public_export.manifest import PRICE_FIELD_ALLOW, PRICE_KEYS, price_problems

    cases: list[tuple[str, object]] = [
        ("calendar.json", {"data": {"session": {"open": "09:00", "close": 1}}}),
        ("overhang_recent.json", {"data": {"rows": [{"price": 1.5, "volume": 2}]}}),
        ("trade/stock/000660.json", {"data": {"monthly": [{"close": 1, "chg_pct": 2}]}}),
        ("diag/summary.json", {"data": {"nav": None, "net_value": 3}}),
    ]
    script = (
        "import * as m from './web/scripts/merge-public-data.mjs';\n"
        f"const cases = {json.dumps(cases)};\n"
        "console.log(JSON.stringify({keys: [...m.PRICE_KEYS].sort(), allow: m.PRICE_FIELD_ALLOW,"
        " got: cases.map(([n, v]) => m.priceProblems(n, v))}));\n"
    )
    assert NODE is not None
    p = subprocess.run(  # noqa: S603
        [NODE, "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    js = json.loads(p.stdout)
    assert js["keys"] == sorted(PRICE_KEYS)
    assert [tuple(r) for r in js["allow"]] == list(PRICE_FIELD_ALLOW)
    assert js["got"] == [price_problems(n, v) for n, v in cases]
    assert all(js["got"])  # 네 경우 모두 무언가를 잡는다(빈 비교로 통과하지 않게)
