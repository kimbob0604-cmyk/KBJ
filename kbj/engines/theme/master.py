"""테마 마스터 해석·검증 — stock-dashboard `data/master/` 파일 일곱 → `ThemeMaster`(ADR 0039 §3).

순수 함수(계약 ⑨): 입력은 파일 이름 → 바이트, 설정(`MasterCfg`), 직전 적재 판의 행 수. 출력은 검증을
통과한 판 하나. 문제가 하나라도 있으면 `MasterInvalid`(문제 목록 — 값이 아니라 파일·칸·개수만)를
던진다 — 부르는 쪽(작업)은 적재하지 않고 실패로 알린다(옛 판 유지, 절대 규칙 4).

검증(거부)
- 파일 일곱이 다 있고 JSON·CSV 로 읽힌다. 필수 칸(머리 줄·키)이 있다.
- meta.json `built_at` 이 시간대 있는 ISO 시각. `total` 이 있으면 stocks.csv 행 수와 같다.
- 대테마 id 형식·중복 없음, 이름·섹션 있음. 네이버 테마 번호 중복 없음. 종목코드 형식·중복 없음.
- 편입 줄의 테마 번호는 네이버 테마 목록 안, 종목코드는 종목 마스터 안, (테마, 종목) 중복 없음.
- 개수 하한(대테마·네이버 테마·편입·종목 — config/theme.yaml)과 직전 판 대비 감소율 상한.

경고(적재는 한다 — `MasterLoad.warnings`)
- 대테마·제외 목록이 가리키는 네이버 테마 번호가 목록에 없다(네이버가 테마를 지웠다).
- 네이버 테마가 대테마·제외 어디에도 없다(새 테마 — stock-dashboard 에서 분류할 것).
- 대테마에 걸린 네이버 테마의 이름이 목록 이름과 다르다(네이버가 이름을 바꿨다).
"""

from __future__ import annotations

import csv
import hashlib
import io
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any, Final

from kbj.config.theme import MasterCfg
from kbj.core.quality import Quality
from kbj.core.theme_rows import (
    SOURCE_LOGIN,
    IndustryGroupRow,
    MasterLoad,
    MemberRow,
    NaverExcludedRow,
    NaverThemeRow,
    StockRow,
    ThemeLinkRow,
    ThemeMaster,
    ThemeRow,
    TypeOverrideRow,
    code_ok,
)
from kbj.engines.theme.classes import (
    META,
    THEMES,
    ClassesInvalid,
    built_at_of,
    check_count,
    load_json,
    theme_items,
    theme_row,
)

__all__ = [
    "COUNT_KEYS",
    "FILES",
    "META",
    "MasterInvalid",
    "built_at_of",
    "digest",
    "parse_master",
]

FILES: Final[tuple[str, ...]] = (
    META,
    THEMES,
    "naver_themes.json",
    "naver_theme_members.csv",
    "stocks.csv",
    "industry_groups.json",
    "type_overrides.csv",
)
# 감소율을 보는 행 수 키(MasterLoad.counts)
COUNT_KEYS: Final[tuple[str, ...]] = ("themes", "naver_themes", "members", "stocks")
_MEMBER_COLS: Final = ("theme_no", "code", "reason")
_STOCK_COLS: Final = (
    "code", "name", "market", "type", "parent_code", "industry", "industry_group", "tier",
    "former_names",
)  # fmt: skip
_OVERRIDE_COLS: Final = ("code", "type", "reason")
_MAX_WARN: Final = 20  # 경고는 앞 20개만 싣고 나머지는 개수로


# 검증 실패(공개 분류와 같은 예외 — 작업이 한 가지로 잡는다)
MasterInvalid = ClassesInvalid


def digest(files: Mapping[str, bytes]) -> str:
    """받은 파일들의 sha256(이름순으로 이름·길이·내용을 잇는다)."""
    h = hashlib.sha256()
    for name in sorted(files):
        body = files[name]
        h.update(name.encode("utf-8") + b"\0" + str(len(body)).encode() + b"\0" + body)
    return h.hexdigest()


def _csv(name: str, raw: bytes, cols: tuple[str, ...], probs: list[str]) -> list[dict[str, str]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        probs.append(f"{name}: UTF-8 이 아니다")
        return []
    reader = csv.DictReader(io.StringIO(text))
    have = tuple(reader.fieldnames or ())
    missing = [c for c in cols if c not in have]
    if missing:
        probs.append(f"{name}: 머리 줄에 {missing} 없음")
        return []
    rows: list[dict[str, str]] = []
    for i, r in enumerate(reader, start=2):
        if None in r:  # 칸이 머리 줄보다 많다
            probs.append(f"{name}:{i}: 칸 수가 머리 줄과 다르다")
            continue
        rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows


def _opt(v: str) -> str | None:
    return v or None


def _int(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return None


def _items(name: str, doc: Any, key: str, probs: list[str]) -> list[dict[str, Any]]:
    seq = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(seq, list):
        probs.append(f"{name}: '{key}' 목록이 없다")
        return []
    out: list[dict[str, Any]] = []
    for i, x in enumerate(seq):
        if not isinstance(x, dict):
            probs.append(f"{name}: {key}[{i}] 가 객체가 아니다")
            continue
        out.append(x)
    return out


def _dupes[T](values: list[T]) -> list[T]:
    return [v for v, n in Counter(values).items() if n > 1]


def _warn(warnings: list[str], what: str, items: list[str]) -> None:
    if not items:
        return
    shown = ", ".join(items[:_MAX_WARN])
    more = f" 외 {len(items) - _MAX_WARN}개" if len(items) > _MAX_WARN else ""
    warnings.append(f"{what} {len(items)}개: {shown}{more}")


def _naver_ref(name: str, where: str, x: object, probs: list[str]) -> tuple[int, str] | None:
    no = _int(x.get("no")) if isinstance(x, dict) else None
    nm = x.get("name") if isinstance(x, dict) else None
    if no is None or no <= 0 or not isinstance(nm, str) or not nm.strip():
        probs.append(f"{name}: {where} 네이버 테마 칸(no·name) 형식")
        return None
    return no, nm.strip()


def parse_master(
    files: Mapping[str, bytes],
    *,
    cfg: MasterCfg,
    previous: Mapping[str, int] | None = None,
    quality: Quality = Quality.OK,
) -> ThemeMaster:
    """받은 파일 → 검증한 판. 문제가 있으면 `MasterInvalid`(모든 문제를 모아서)."""
    missing = [f for f in FILES if f not in files]
    if missing:
        raise MasterInvalid([f"파일 없음: {', '.join(missing)}"])
    built = built_at_of(files[META])
    probs: list[str] = []
    warnings: list[str] = []
    meta = load_json(META, files[META])
    th_doc = load_json("themes.json", files["themes.json"])
    nt_doc = load_json("naver_themes.json", files["naver_themes.json"])
    ig_doc = load_json("industry_groups.json", files["industry_groups.json"])
    prv: dict[str, Any] = {"source": SOURCE_LOGIN, "as_of": built, "quality": quality}

    def row[R](make: Callable[..., R], where: str, **kw: Any) -> R | None:
        try:
            return make(**kw)
        except ValueError as e:  # 행 자료형 검증(형식) — 값은 싣지 않고 이유만
            probs.append(f"{where}: {str(e).split(':', 1)[0]}")
            return None

    # ── 네이버 테마 ──
    naver: list[NaverThemeRow] = []
    for i, x in enumerate(_items("naver_themes.json", nt_doc, "themes", probs)):
        no = _int(x.get("no"))
        desc = x.get("description")
        r = row(
            NaverThemeRow, f"naver_themes.json themes[{i}]",
            no=no if no is not None else 0, name=x.get("name"),
            description=desc.strip() if isinstance(desc, str) and desc.strip() else None,
            count=_int(x.get("count")), in_master=_int(x.get("in_master")), **prv,
        )  # fmt: skip
        if r is not None:
            naver.append(r)
    if d := _dupes([n.no for n in naver]):
        probs.append(f"naver_themes.json: 번호 중복 {len(d)}개")
    naver_names = {n.no: n.name for n in naver}

    # ── 대테마·연결·제외 ──
    themes: list[ThemeRow] = []
    links: list[ThemeLinkRow] = []
    renamed: list[str] = []
    gone: list[str] = []
    for i, x in enumerate(theme_items(th_doc, probs)):
        t = theme_row(x, i, as_of=built, quality=quality, probs=probs) if x else None
        if t is None:
            continue
        themes.append(t)
        tid = t.id
        refs = x.get("naver")
        if not isinstance(refs, list):
            probs.append(f"themes.json {tid}: naver 목록이 없다")
            continue
        for j, ref in enumerate(refs):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            got = _naver_ref("themes.json", f"{tid}.naver[{j}]", ref, probs)
            if got is None:
                continue
            no, nm = got
            if no not in naver_names:
                gone.append(f"{tid}:{no}")
            elif naver_names[no] != nm:
                renamed.append(str(no))
            lk = row(ThemeLinkRow, f"themes.json {tid}.naver[{j}]",
                     theme_id=tid, naver_no=no, naver_name=nm, ord=j, **prv)  # fmt: skip
            if lk is not None:
                links.append(lk)
    if d := _dupes([t.id for t in themes]):
        probs.append(f"themes.json: id 중복 {len(d)}개")
    if d := _dupes([(lk.theme_id, lk.naver_no) for lk in links]):
        probs.append(f"themes.json: 같은 대테마에 같은 네이버 테마 {len(d)}건")
    excluded: list[NaverExcludedRow] = []
    ex_raw = th_doc.get("excluded_naver", []) if isinstance(th_doc, dict) else []
    if not isinstance(ex_raw, list):
        probs.append("themes.json: excluded_naver 가 목록이 아니다")
        ex_raw = []
    for j, ref in enumerate(ex_raw):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        got = _naver_ref("themes.json", f"excluded_naver[{j}]", ref, probs)
        if got is None:
            continue
        if got[0] not in naver_names:
            gone.append(f"excluded:{got[0]}")
        ex = row(NaverExcludedRow, f"themes.json excluded_naver[{j}]",
                 naver_no=got[0], naver_name=got[1], **prv)  # fmt: skip
        if ex is not None:
            excluded.append(ex)
    if d := _dupes([e.naver_no for e in excluded]):
        probs.append(f"themes.json: excluded_naver 번호 중복 {len(d)}개")
    placed = {lk.naver_no for lk in links} | {e.naver_no for e in excluded}
    _warn(warnings, "대테마·제외가 가리키는 없는 네이버 테마", gone)
    _warn(warnings, "이름이 바뀐 네이버 테마", sorted(set(renamed), key=int))
    _warn(warnings, "대테마·제외 어디에도 없는 네이버 테마",
          [str(n) for n in sorted(set(naver_names) - placed)])  # fmt: skip

    # ── 종목 마스터·편입 ──
    stocks: list[StockRow] = []
    for i, r in enumerate(_csv("stocks.csv", files["stocks.csv"], _STOCK_COLS, probs), start=2):
        s = row(
            StockRow, f"stocks.csv:{i}",
            code=r["code"], name=r["name"], market=r["market"], type=r["type"],
            parent_code=_opt(r["parent_code"]), industry=_opt(r["industry"]),
            industry_group=_opt(r["industry_group"]), tier=_opt(r["tier"]),
            former_names=_opt(r["former_names"]), **prv,
        )  # fmt: skip
        if s is not None:
            stocks.append(s)
    if d := _dupes([s.code for s in stocks]):
        probs.append(f"stocks.csv: 종목코드 중복 {len(d)}개")
    total = _int(meta.get("total")) if isinstance(meta, dict) else None
    if total is not None and total != len(stocks):
        probs.append(f"{META}: total({total}) 이 stocks.csv 행 수({len(stocks)})와 다르다")
    codes = {s.code for s in stocks}
    members: list[MemberRow] = []
    bad_theme = bad_code = 0
    mem_rows = _csv(
        "naver_theme_members.csv", files["naver_theme_members.csv"], _MEMBER_COLS, probs
    )
    for i, r in enumerate(mem_rows, start=2):
        no = _int(r["theme_no"])
        if no is None or not code_ok(r["code"]):
            probs.append(f"naver_theme_members.csv:{i}: theme_no·code 형식")
            continue
        if no not in naver_names:
            bad_theme += 1
            continue
        if r["code"] not in codes:
            bad_code += 1
            continue
        m = row(MemberRow, f"naver_theme_members.csv:{i}",
                naver_no=no, code=r["code"], reason=_opt(r["reason"]), **prv)  # fmt: skip
        if m is not None:
            members.append(m)
    if bad_theme:
        probs.append(f"naver_theme_members.csv: 목록에 없는 네이버 테마 번호 {bad_theme}줄")
    if bad_code:
        probs.append(f"naver_theme_members.csv: 종목 마스터에 없는 종목 {bad_code}줄")
    if d := _dupes([(m.naver_no, m.code) for m in members]):
        probs.append(f"naver_theme_members.csv: (테마, 종목) 중복 {len(d)}건")

    # ── 업종 대분류·종류 손 지정 ──
    groups: list[IndustryGroupRow] = []
    for i, x in enumerate(_items("industry_groups.json", ig_doc, "groups", probs)):
        inds = x.get("industries")
        if not isinstance(inds, list) or not all(isinstance(v, str) for v in inds):  # pyright: ignore[reportUnknownVariableType]
            probs.append(f"industry_groups.json: groups[{i}] industries 형식")
            continue
        names: list[Any] = list(inds)  # pyright: ignore[reportUnknownArgumentType]
        g = row(IndustryGroupRow, f"industry_groups.json groups[{i}]",
                id=x.get("id"), name=x.get("name"),
                industries=tuple(str(v) for v in names), **prv)  # fmt: skip
        if g is not None:
            groups.append(g)
    if d := _dupes([g.id for g in groups]):
        probs.append(f"industry_groups.json: id 중복 {len(d)}개")
    overrides: list[TypeOverrideRow] = []
    ov_rows = _csv("type_overrides.csv", files["type_overrides.csv"], _OVERRIDE_COLS, probs)
    for i, r in enumerate(ov_rows, start=2):
        o = row(TypeOverrideRow, f"type_overrides.csv:{i}",
                code=r["code"], type=r["type"], reason=_opt(r["reason"]), **prv)  # fmt: skip
        if o is not None:
            overrides.append(o)
    if d := _dupes([o.code for o in overrides]):
        probs.append(f"type_overrides.csv: 종목코드 중복 {len(d)}개")

    # ── 개수 하한·감소율 ──
    counts = {
        "themes": len(themes), "links": len(links), "excluded": len(excluded),
        "naver_themes": len(naver), "members": len(members), "stocks": len(stocks),
        "groups": len(groups), "overrides": len(overrides),
    }  # fmt: skip
    floors = {
        "themes": cfg.min_themes, "naver_themes": cfg.min_naver_themes,
        "members": cfg.min_members, "stocks": cfg.min_stocks,
    }  # fmt: skip
    for k in COUNT_KEYS:
        before = (previous or {}).get(k)
        check_count(probs, k, counts[k], floors[k], before, cfg.max_drop_ratio)
    if probs:
        raise MasterInvalid(probs)
    load = MasterLoad(
        built_at=built, digest=digest(files), counts=counts, warnings=tuple(warnings), **prv
    )
    return ThemeMaster(
        load=load,
        themes=tuple(themes),
        links=tuple(links),
        excluded=tuple(excluded),
        naver_themes=tuple(naver),
        members=tuple(members),
        stocks=tuple(stocks),
        groups=tuple(groups),
        overrides=tuple(overrides),
    )
