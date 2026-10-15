"""테마 마스터 해석·검증(엔진 — ADR 0039, metrics §31). 합성
파일만(tests/fixtures/synthetic/theme_master.py)."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from kbj.config.theme import load_theme
from kbj.core.quality import Quality
from kbj.core.theme_rows import SOURCE_LOGIN, SOURCE_MASTER
from kbj.engines.theme.classes import ClassesInvalid, parse_classes
from kbj.engines.theme.master import FILES, MasterInvalid, digest, parse_master
from tests.fixtures.synthetic.theme_master import BUILT_AT, build_files, theme_ids

CFG = load_theme().master


def _edit_json(files: dict[str, bytes], name: str, fn: object) -> dict[str, bytes]:
    doc = json.loads(files[name])
    fn(doc)  # pyright: ignore[reportCallIssue]
    return {**files, name: json.dumps(doc, ensure_ascii=False).encode()}


def test_parses_all_tables_with_sources_and_as_of() -> None:
    m = parse_master(build_files(), cfg=CFG)
    assert m.load.built_at == BUILT_AT and m.load.as_of == BUILT_AT
    assert m.load.counts["themes"] == 36 and m.load.counts["naver_themes"] == 180
    assert m.load.counts["stocks"] == 2100 and m.load.counts["members"] >= CFG.min_members
    assert [t.id for t in m.themes] == theme_ids()
    assert {t.source for t in m.themes} == {SOURCE_MASTER}  # 공개 행 — 네이버 이름 없음
    rows = (*m.links, *m.naver_themes, *m.members, *m.stocks, *m.groups, *m.excluded)
    assert {r.source for r in rows} == {SOURCE_LOGIN}
    assert all(r.as_of == BUILT_AT and r.quality is Quality.OK for r in rows)
    assert any("어디에도 없는" in w for w in m.load.warnings)  # 새 원본 테마는 경고만
    assert len(m.load.digest) == 64 and m.load.digest == digest(build_files())


def test_same_seed_same_bytes() -> None:
    assert build_files() == build_files()
    assert build_files(seed=1) != build_files()


def test_missing_file_and_bad_built_at_are_refused() -> None:
    files = build_files()
    for name in FILES:
        with pytest.raises(MasterInvalid, match="파일 없음"):
            parse_master({k: v for k, v in files.items() if k != name}, cfg=CFG)
    bad = _edit_json(files, "meta.json", lambda d: d.update(built_at="2026-10-11 06:00"))
    with pytest.raises(MasterInvalid, match="built_at"):
        parse_master(bad, cfg=CFG)


def test_floors_and_drop_ratio() -> None:
    files = build_files()
    m = parse_master(files, cfg=CFG)
    # 하한: 편입 ≥ 3,000 — 절반으로 줄면 하한·감소율 둘 다
    half = build_files(drop_members=0.5)
    with pytest.raises(MasterInvalid) as e:
        parse_master(half, cfg=CFG, previous=m.load.counts)
    assert any("하한" in p for p in e.value.problems)
    assert any("넘게 줄었다" in p for p in e.value.problems)
    # 하한은 넘지만 직전 판보다 25% 줄면 감소율로만 거부
    big = dict(m.load.counts, members=int(m.load.counts["members"] / 0.7))
    with pytest.raises(MasterInvalid, match="넘게 줄었다"):
        parse_master(files, cfg=CFG, previous=big)
    # 10% 감소는 통과
    ok = dict(m.load.counts, members=int(m.load.counts["members"] * 1.1))
    assert parse_master(files, cfg=CFG, previous=ok).load.counts == m.load.counts


def test_reference_and_shape_errors_are_collected() -> None:
    files = build_files()
    text = files["naver_theme_members.csv"].decode()
    broken = text + "999,930001,x\n1,999999,없는 종목\n1,ABC,형식\n"
    files2 = {**files, "naver_theme_members.csv": broken.encode()}
    with pytest.raises(MasterInvalid) as e:
        parse_master(files2, cfg=CFG)
    probs = " ".join(e.value.problems)
    assert "목록에 없는 네이버 테마 번호" in probs and "종목 마스터에 없는 종목" in probs
    assert "theme_no·code 형식" in probs
    dup = _edit_json(files, "themes.json", lambda d: d["themes"].append(dict(d["themes"][0])))
    with pytest.raises(MasterInvalid, match="id 중복"):
        parse_master(dup, cfg=CFG)
    nohead = {**files, "stocks.csv": b"code,name\n000001,x\n"}
    with pytest.raises(MasterInvalid, match="머리 줄"):
        parse_master(nohead, cfg=CFG)
    total = _edit_json(files, "meta.json", lambda d: d.update(total=1))
    with pytest.raises(MasterInvalid, match="total"):
        parse_master(total, cfg=CFG)


def test_classes_reads_only_user_fields() -> None:
    files = build_files()
    c = parse_classes(files, cfg=CFG)
    assert c.built_at == BUILT_AT and len(c.themes) == 36
    assert {t.section for t in c.themes} == {"기술", "에너지", "소비", "산업", "의료", "금융"}
    assert any(t.valuechain for t in c.themes) and any(t.valuechain is None for t in c.themes)
    # 네이버 목록이 깨져 있어도 분류는 읽는다(그 칸을 보지 않는다)
    broken = _edit_json(files, "themes.json", lambda d: [t.update(naver="x") for t in d["themes"]])
    assert len(parse_classes(broken, cfg=CFG).themes) == 36
    few = _edit_json(files, "themes.json", lambda d: d.update(themes=d["themes"][:10]))
    with pytest.raises(ClassesInvalid, match="하한"):
        parse_classes(few, cfg=CFG)
    with pytest.raises(ClassesInvalid, match="넘게 줄었다"):
        parse_classes(files, cfg=CFG, previous=60)
    bad = _edit_json(files, "themes.json", lambda d: d["themes"][0].update(id="Bad Id"))
    with pytest.raises(ClassesInvalid, match="id 형식"):
        parse_classes(bad, cfg=CFG)


def test_newer_built_at_changes_digest() -> None:
    a = build_files()
    b = build_files(built_at=BUILT_AT + timedelta(days=7))
    assert digest(a) != digest(b)
