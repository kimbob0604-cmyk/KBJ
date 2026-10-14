"""규칙 쓰기 모듈 — `prv_alerts.rule`·`rule_version` 의 쓰는 쪽 하나(P8 묶음 EVL, docs/p8_design.md
D-P8-15·16, §3.3·§5).

API 라우트(`/api/rules` POST·PUT·상태)와 CLI(`python -m kbj.services.personal
rules …`)가 같은 함수를
부른다. 저장은 `PersonalRepo.put_rule_personal`·`set_rule_state_personal`(한 트랜잭션), 읽기는
`RulesRepo`.

- 만들기: DSL 검사(`validate_rule` — 모양·의미·상한, 실패는 위치·사유만) → 정규화 → `dsl_hash` → 새
  규칙 id(`r_` + 12자 — 부르는 쪽이 준 무작위 바이트, 겹치면 다시) → 머리 + 1판.
- 새 판: 같은 규칙에 새 판(판은 바꾸지 않는다 — 과거 신호가 어느 판에서 났는지
  남는다). 정규화 해시가
  지금 판과 같으면 판을 늘리지 않는다(이름만 바뀌면 머리만). 보관된 규칙은 고치지 않는다.
- 상태: enable·disable·archive(보관 = 끄고 `archived_at` — 지우지 않는다).
  보관된 규칙은 켜지 않는다.
- 예시 가져오기: `config/rule_examples.yaml` 10개를 꺼진 채(`source=example`)
  넣는다. 같은 이름·해시가
  이미 있으면 건너뛴다(멱등).

시계·무작위 바이트는 주입한다(시험).
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, Final, Literal, Protocol

import yaml

from kbj.config.rules_cfg import RulesConfig, load_rules_cfg
from kbj.core.rules_rows import RuleRow, RuleVersion, new_rule_id
from kbj.core.time import utcnow
from kbj.engines.rules.dsl import (
    DslError,
    DslIssue,
    Rule,
    dsl_hash,
    limits_of,
    load_example_rules,
    validate_rule,
)
from kbj.store.repos.personal import PersonalRepo
from kbj.store.repos.rules import RulesRepo

__all__ = [
    "ACTIONS",
    "Action",
    "RuleNotFound",
    "RuleRepos",
    "RuleStateError",
    "WriteResult",
    "cli",
    "create_rule",
    "import_examples",
    "new_version",
    "set_state",
]

Action = Literal["enable", "disable", "archive"]
ACTIONS: Final[tuple[Action, ...]] = ("enable", "disable", "archive")
MAX_ID_TRIES: Final = 5


class RuleRepos(Protocol):
    @property
    def rules(self) -> RulesRepo: ...

    @property
    def personal(self) -> PersonalRepo: ...


class RuleNotFound(LookupError):
    """규칙이 없다(404)."""


class RuleStateError(ValueError):
    """지금 상태로는 할 수 없다(보관된 규칙 고치기·켜기 — 409)."""


@dataclass(frozen=True)
class WriteResult:
    rule_id: str
    version: int
    dsl_hash: str
    enabled: bool
    archived: bool
    created: bool  # 새 규칙·새 판을 만들었나(같은 해시면 False)


def _dump(rule: Rule) -> dict[str, Any]:
    return rule.model_dump(mode="json", exclude_none=True)


def _validate(dsl: Mapping[str, Any], cfg: RulesConfig) -> tuple[Rule, str]:
    rule = validate_rule(dict(dsl), limits_of(cfg))
    return rule, dsl_hash(rule)


def _result(head: RuleRow, ver: RuleVersion, *, created: bool) -> WriteResult:
    return WriteResult(
        head.rule_id, ver.version, ver.dsl_hash, head.enabled, head.archived_at is not None, created
    )


def create_rule(
    repos: RuleRepos,
    dsl: Mapping[str, Any],
    *,
    enabled: bool,
    source: str,
    now: datetime,
    loaded_by: str,
    cfg: RulesConfig,
    seed: Callable[[], bytes] = lambda: secrets.token_bytes(10),
) -> WriteResult:
    """새 규칙 + 1판. DSL 이 틀리면 `DslError`(위치·사유)."""
    rule, h = _validate(dsl, cfg)
    for _ in range(MAX_ID_TRIES):
        rid = new_rule_id(seed())
        if repos.rules.rule(rid) is None:
            break
    else:  # pragma: no cover — 36^12 공간에서 다섯 번 겹칠 일은 없다
        raise RuntimeError("규칙 id 를 만들지 못했다(겹침)")
    head = RuleRow(rid, rule.name, 1, enabled, None, source, now, now, loaded_by)
    ver = RuleVersion(rid, 1, _dump(rule), h, now)
    repos.personal.put_rule_personal(head, ver)
    return _result(head, ver, created=True)


def _head(repos: RuleRepos, rule_id: str) -> RuleRow:
    head = repos.rules.rule(rule_id)
    if head is None:
        raise RuleNotFound(rule_id)
    return head


def _current(repos: RuleRepos, head: RuleRow) -> RuleVersion:
    vers = [v for v in repos.rules.versions(head.rule_id) if v.version == head.current_version]
    if not vers:
        raise RuntimeError(f"현재 판이 없다: {head.rule_id} {head.current_version}판")
    return vers[0]


def new_version(
    repos: RuleRepos,
    rule_id: str,
    dsl: Mapping[str, Any],
    *,
    enabled: bool | None,
    now: datetime,
    cfg: RulesConfig,
) -> WriteResult:
    """새 판(같은 정규화 해시면 판을 늘리지 않고 이름·켜짐만)."""
    head = _head(repos, rule_id)
    if head.archived_at is not None:
        raise RuleStateError("보관된 규칙은 고치지 않는다")
    rule, h = _validate(dsl, cfg)
    cur = _current(repos, head)
    on = head.enabled if enabled is None else enabled
    if h == cur.dsl_hash:
        if rule.name == head.name and on == head.enabled:
            return _result(head, cur, created=False)
        new_head = RuleRow(
            head.rule_id,
            rule.name,
            head.current_version,
            on,
            None,
            head.source,
            head.created_at,
            now,
            head.loaded_by,
        )
        repos.personal.put_rule_personal(new_head, None)
        return _result(new_head, cur, created=False)
    v = head.current_version + 1
    new_head = RuleRow(
        head.rule_id, rule.name, v, on, None, head.source, head.created_at, now, head.loaded_by
    )
    ver = RuleVersion(head.rule_id, v, _dump(rule), h, now)
    repos.personal.put_rule_personal(new_head, ver)
    return _result(new_head, ver, created=True)


def set_state(repos: RuleRepos, rule_id: str, action: Action, *, now: datetime) -> WriteResult:
    head = _head(repos, rule_id)
    if action == "enable" and head.archived_at is not None:
        raise RuleStateError("보관된 규칙은 켜지 않는다")
    if action == "archive":
        ok = repos.personal.set_rule_state_personal(
            rule_id,
            updated_at=now,
            enabled=False,
            archived_at=head.archived_at or now,
        )
    else:
        ok = repos.personal.set_rule_state_personal(
            rule_id, updated_at=now, enabled=action == "enable"
        )
    if not ok:  # pragma: no cover — 위에서 읽었다(동시 삭제는 없다 — 보관만)
        raise RuleNotFound(rule_id)
    head = _head(repos, rule_id)
    return _result(head, _current(repos, head), created=False)


def import_examples(
    repos: RuleRepos,
    *,
    now: datetime,
    loaded_by: str,
    cfg: RulesConfig,
    examples: Mapping[str, Rule] | None = None,
    seed: Callable[[], bytes] = lambda: secrets.token_bytes(10),
) -> list[WriteResult]:
    """예시 규칙(꺼진 채, source=example). 같은 이름·해시가 있으면 건너뛴다."""
    ex = examples if examples is not None else load_example_rules()
    have: set[tuple[str, str]] = set()
    for head in repos.rules.rules(include_archived=True):
        for v in repos.rules.versions(head.rule_id):
            have.add((head.name, v.dsl_hash))
    out: list[WriteResult] = []
    for rule in ex.values():
        if (rule.name, dsl_hash(rule)) in have:
            continue
        out.append(
            create_rule(
                repos,
                _dump(rule),
                enabled=False,
                source="example",
                now=now,
                loaded_by=loaded_by,
                cfg=cfg,
                seed=seed,
            )
        )
    return out


# ── CLI(`python -m kbj.services.personal rules …`) ───────────────────────────────────────────


def _read_dsl(path: str) -> dict[str, Any]:
    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) if path.endswith((".yaml", ".yml")) else json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("규칙 파일은 JSON·YAML 객체 하나")
    return data  # pyright: ignore[reportUnknownVariableType]


def _issues(issues: Sequence[DslIssue]) -> str:
    return "; ".join(f"{i.pointer or '/'}: {i.reason}" for i in issues)


def cli(
    argv: Sequence[str],
    repos: RuleRepos,
    *,
    now: Callable[[], datetime] = utcnow,
    out: IO[str] | None = None,
    err: IO[str] | None = None,
    cfg: RulesConfig | None = None,
) -> int:
    """종료 코드: 0 성공, 1 검사 실패·없는 규칙·상태 거절, 2 파일·설정 오류. 출력은 id·이름·판·
    상태만(DSL 본문은 `show` 에서만)."""
    ap = argparse.ArgumentParser(prog="python -m kbj.services.personal rules")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="규칙 목록(보관 포함)")
    shw = sub.add_parser("show", help="규칙 판 이력")
    shw.add_argument("rule_id")
    add = sub.add_parser("add", help="규칙 만들기(JSON·YAML 파일)")
    add.add_argument("path")
    add.add_argument("--enable", action="store_true")
    upd = sub.add_parser("update", help="새 판")
    upd.add_argument("rule_id")
    upd.add_argument("path")
    for a in ACTIONS:
        p = sub.add_parser(a, help=f"규칙 {a}")
        p.add_argument("rule_id")
    sub.add_parser("examples", help="예시 10개 가져오기(꺼진 채)")
    args = ap.parse_args(list(argv))
    o = out or sys.stdout
    e = err or sys.stderr
    c = cfg if cfg is not None else load_rules_cfg()
    t = now()
    try:
        if args.cmd == "list":
            for h in repos.rules.rules(include_archived=True):
                st = "보관" if h.archived_at else ("켜짐" if h.enabled else "꺼짐")
                print(f"{h.rule_id} {h.current_version}판 {st} {h.name}", file=o)
            return 0
        if args.cmd == "show":
            head = _head(repos, args.rule_id)
            for v in repos.rules.versions(head.rule_id):
                body = json.dumps(dict(v.dsl), ensure_ascii=False)
                print(f"{v.version}판 {v.dsl_hash[:12]} {body}", file=o)
            return 0
        if args.cmd == "add":
            r = create_rule(
                repos,
                _read_dsl(args.path),
                enabled=args.enable,
                source="cli",
                now=t,
                loaded_by="cli",
                cfg=c,
            )
        elif args.cmd == "update":
            r = new_version(repos, args.rule_id, _read_dsl(args.path), enabled=None, now=t, cfg=c)
        elif args.cmd == "examples":
            got = import_examples(repos, now=t, loaded_by="cli", cfg=c)
            print(f"예시 {len(got)}개 넣음", file=o)
            return 0
        else:
            r = set_state(repos, args.rule_id, args.cmd, now=t)
        print(
            f"{r.rule_id} {r.version}판 {'켜짐' if r.enabled else '꺼짐'}"
            f"{' 보관' if r.archived else ''}{'' if r.created else ' (판 그대로)'}",
            file=o,
        )
        return 0
    except DslError as exc:
        print(f"규칙 검사 실패: {_issues(exc.issues)}", file=e)
        return 1
    except RuleNotFound as exc:
        print(f"없는 규칙: {exc}", file=e)
        return 1
    except RuleStateError as exc:
        print(f"거절: {exc}", file=e)
        return 1
    except (OSError, ValueError) as exc:
        print(f"실패: {type(exc).__name__}: {exc}", file=e)
        return 2
