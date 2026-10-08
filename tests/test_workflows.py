"""워크플로 YAML 정적 시험 — 예약 실행 없음·권한 최소·비밀 없음(docs/p3_design.md §1.7·§7.3).

- 모든 워크플로: `schedule` 트리거가 없다(CI 머리말 규칙 — 예약 실행은 두지 않는다).
- `pages.yml`: 트리거는 `workflow_dispatch` 하나, 전체 권한은 `contents: read` 만,
  `pages: write`·`id-token: write` 는 배포 잡에만, 배포는 main 에서만, `secrets.` 참조 없음,
  checkout 은 자격 증명을 남기지 않음, 빌드 순서는 `scripts/build_public_site.sh` 한 벌
  (그 스크립트가
  `npm ci --ignore-scripts`·merge·check-bundle 을 부른다), 데이터는 `public-data` 브랜치에서.
PyYAML 은 키 `on` 을 True 로 읽는다 — 둘 다 본다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows"
PAGES = WF / "pages.yml"
SCRIPT = ROOT / "scripts" / "build_public_site.sh"


def _load(p: Path) -> dict[Any, Any]:
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(wf: dict[Any, Any]) -> dict[str, Any]:
    trig = wf.get("on", wf.get(True))
    if isinstance(trig, str):
        return {trig: None}
    if isinstance(trig, list):
        return dict.fromkeys(trig)
    assert isinstance(trig, dict)
    return trig


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps", []))


@pytest.mark.parametrize("path", sorted(WF.glob("*.y*ml")), ids=lambda p: p.name)
def test_no_scheduled_triggers(path: Path) -> None:
    assert "schedule" not in _on(_load(path))
    assert "cron:" not in path.read_text(encoding="utf-8")


def test_pages_trigger_is_manual_only() -> None:
    assert set(_on(_load(PAGES))) == {"workflow_dispatch"}


def test_pages_permissions_minimal() -> None:
    wf = _load(PAGES)
    assert wf["permissions"] == {"contents": "read"}
    jobs = wf["jobs"]
    assert set(jobs) == {"build", "deploy"}
    assert "permissions" not in jobs["build"]  # 빌드는 읽기만(전체 권한 상속)
    assert jobs["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}
    assert jobs["deploy"]["needs"] == "build"
    assert jobs["deploy"]["if"] == "github.ref == 'refs/heads/main'"
    assert jobs["deploy"]["environment"]["name"] == "github-pages"


def test_pages_uses_no_secrets_and_no_persisted_credentials() -> None:
    text = PAGES.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "GITHUB_TOKEN" not in text
    for step in _steps(_load(PAGES)["jobs"]["build"]):
        if str(step.get("uses", "")).startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_pages_build_steps() -> None:
    steps = _steps(_load(PAGES)["jobs"]["build"])
    uses = [str(s.get("uses", "")) for s in steps]
    assert [u.split("@")[0] for u in uses if u] == [
        "actions/checkout",
        "actions/checkout",
        "actions/setup-node",
        "actions/upload-pages-artifact",
    ]
    data = next(s for s in steps if s.get("with", {}).get("ref") == "public-data")
    assert data["with"]["path"] == "public-data"
    node = next(s for s in steps if str(s.get("uses", "")).startswith("actions/setup-node"))
    assert str(node["with"]["node-version"]) == "22"
    runs = [s["run"] for s in steps if "run" in s]
    assert runs == ["bash scripts/build_public_site.sh --data public-data"]
    upload = next(
        s for s in steps if str(s.get("uses", "")).startswith("actions/upload-pages-artifact")
    )
    assert upload["with"]["path"] == "web/dist-public"
    deploy = _steps(_load(PAGES)["jobs"]["deploy"])
    assert [str(s.get("uses", "")).split("@")[0] for s in deploy] == ["actions/deploy-pages"]


def test_actions_pinned_to_major() -> None:
    for path in sorted(WF.glob("*.y*ml")):
        for job in _load(path)["jobs"].values():
            for s in _steps(job):
                u = s.get("uses")
                if u:
                    assert "@" in u and not u.endswith(("@main", "@master")), f"{path.name}: {u}"


def test_build_script_order() -> None:
    """빌드 순서(W 약속, web/ 안에서): npm ci --ignore-scripts → build:public → merge → check."""
    text = SCRIPT.read_text(encoding="utf-8")
    order = [
        '"$NPM" ci --ignore-scripts',
        '"$NPM" run build:public',
        '"$NODE" scripts/merge-public-data.mjs "$DATA" dist-public',
        '"$NODE" scripts/check-bundle.mjs dist-public',
    ]
    pos = [text.find(o) for o in order]
    assert all(p >= 0 for p in pos), pos
    assert pos == sorted(pos)
    assert 'cd "$WEB"' in text
    assert "set -euo pipefail" in text
    assert "git push" not in text  # 스크립트는 배포하지 않는다
