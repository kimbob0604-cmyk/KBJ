"""오라클 Cloud Shell 자동 생성 시험 — scripts/vm/oci_create.sh·oci_destroy.sh(ADR 0040 보충).

실제 오라클에 접속하지 않는다. PATH 앞에 둔 **가짜 oci**(셸 스크립트)가 호출 인자를
기록하고 정해진 값을 돌려준다(스크립트는 `--query … --raw-output` 으로 값을 받으므로 가짜는
쿼리 결과를 바로 낸다). 가짜 ssh-keygen 은 표시값이 든 개인키를 만든다 — 출력에 나오면 실패.
확인: ① 자원 생성 순서·멱등(두 번째 실행은 만들기 없음) ② 자리 없음 → 다른 AD 로 재시도 후
성공 ③ --dry-run 은 oci 를 부르지 않음 ④ 출력에 개인키·전체 OCID 없음
+ 무료 한도 초과 멈춤·지우기 확인 문구.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CREATE = ROOT / "scripts" / "vm" / "oci_create.sh"
DESTROY = ROOT / "scripts" / "vm" / "oci_destroy.sh"
GUIDE = ROOT / "docs" / "deploy_oracle.md"
TENANCY = "ocid1.tenancy.oc1..aaaafaketenancy0000tn1234"
KEY_MARK = "ZZPRIVATEKEYSENTINEL"

FAKE_OCI = r"""#!/usr/bin/env bash
# 가짜 oci — 인자를 calls.log 에 한 줄로 남기고 상태 파일로 정해진 값을 낸다
set -u
D="$FAKE_DIR"
printf '%s\n' "$*" >> "$D/calls.log"
args=("$@")
if [[ "${args[0]}" == "--region" ]]; then args=("${args[@]:2}"); fi
cmd="${args[0]} ${args[1]} ${args[2]:-}"
q=""; ud=""
for ((i = 0; i < ${#args[@]}; i++)); do
  case "${args[i]}" in
    --query) q="${args[i+1]}" ;;
    --user-data-file) ud="${args[i+1]}" ;;
  esac
done
st() { [[ -f "$D/$1" ]] && cat "$D/$1"; return 0; }
mk() { printf '%s' "$2" > "$D/$1"; printf '%s\n' "$2"; }
R="oc1.ap-chuncheon-1"
AD="Xyzw:AP-CHUNCHEON-1-AD"
RT="ocid1.routetable.$R.aaaafakert000rtb222"
case "$cmd" in
  "iam region-subscription list") echo "ap-chuncheon-1" ;;
  "iam availability-domain list") printf '[\n  "%s-1",\n  "%s-2"\n]\n' "$AD" "$AD" ;;
  "network vcn list") st vcn ;;
  "network vcn create") mk vcn "ocid1.vcn.$R.amaaaaaafakevcn000vcn111" ;;
  "network vcn get") echo "$RT" ;;
  "network internet-gateway list") st igw ;;
  "network internet-gateway create") mk igw "ocid1.internetgateway.$R.aaaafakeigw000igw333" ;;
  "network route-table get") if [[ -f "$D/rt" ]]; then echo 1; else echo 0; fi ;;
  "network route-table update") touch "$D/rt"; echo "$RT" ;;
  "network security-list list") st sl ;;
  "network security-list create") mk sl "ocid1.securitylist.$R.aaaafakesl000sel444" ;;
  "network security-list get") echo 3 ;;
  "network subnet list") st subnet ;;
  "network subnet create") mk subnet "ocid1.subnet.oc1.ap-chuncheon-1.aaaafakesub000sub555" ;;
  "compute image list") echo "ocid1.image.oc1.ap-chuncheon-1.aaaafakeimage00img666" ;;
  "compute instance list")
    case "$q" in
      *ocpus*) echo "${FAKE_OTHER_OCPUS:-0}" ;;
      *memory-in-gbs*) echo "${FAKE_OTHER_MEM:-0}" ;;
      *) st inst ;;
    esac ;;
  "compute instance get") echo "RUNNING" ;;
  "compute instance list-vnics") echo "203.0.113.10" ;;
  "compute instance launch")
    n="$(st launches)"; n=$(( ${n:-0} + 1 )); printf '%s' "$n" > "$D/launches"
    [[ -n "$ud" ]] && cp "$ud" "$D/user_data"
    if (( n <= ${FAKE_CAPACITY_FAILS:-0} )); then
      {
        printf 'ServiceError:\n{\n    "code": "InternalError",\n'
        printf '    "message": "Out of host capacity.",\n'
        printf '    "opc-request-id": "ocid1.fake.oc1..aaaarequestidzz9999",\n'
        printf '    "status": 500\n}\n'
      } >&2
      exit 1
    fi
    mk inst "ocid1.instance.oc1.ap-chuncheon-1.anfakeinstance00ins777" ;;
  "bv boot-volume list"|"bv volume list") echo 0 ;;
  "compute instance terminate") rm -f "$D/inst" ;;
  "network subnet delete"|"network security-list delete") ;;
  "network internet-gateway delete"|"network vcn delete") ;;
  *) echo "fake oci: 모르는 명령 $cmd" >&2; exit 2 ;;
esac
"""

FAKE_KEYGEN = r"""#!/usr/bin/env bash
# 가짜 ssh-keygen — -f 파일에 표시값 개인키, .pub 에 공개키
f=""
while (( $# )); do [[ "$1" == "-f" ]] && f="$2"; shift; done
k="PRIVATE"  # 머리줄을 쪼개 둔다(공개 안전 검사가 레포의 개인키 모양을 찾는다)
{
  printf -- '-----BEGIN OPENSSH %s KEY-----\nZZPRIVATEKEYSENTINEL\n' "$k"
  printf -- '-----END OPENSSH %s KEY-----\n' "$k"
} > "$f"
printf 'ssh-ed25519 AAAAfakepublic kbj-vm\n' > "$f.pub"
"""


@pytest.fixture
def fake(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("oci", FAKE_OCI), ("ssh-keygen", FAKE_KEYGEN)):
        p = bin_dir / name
        p.write_text(body, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    state = tmp_path / "state"
    state.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "HOME": str(home),
        "FAKE_DIR": str(state),
        "OCI_TENANCY": TENANCY,
        "OCI_REGION": "ap-chuncheon-1",
        "KBJ_RETRY_INTERVAL": "0",
    }


def _run(
    script: Path, env: dict[str, str], *args: str, stdin: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 — 고정 인자
        ["bash", str(script), *args],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        input=stdin,
        env={**os.environ, **env},
        cwd=env["HOME"],
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = Path(env["FAKE_DIR"]) / "calls.log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _mutating(calls: list[str]) -> list[str]:
    return [c for c in calls if re.search(r" (create|launch|update|delete|terminate|action) ", c)]


_FULL_OCID = re.compile(r"ocid1\.[a-z]+\.oc1\.[\w.-]*\.\w{10,}|ocid1\.[a-z]+\.oc1\.\.\w{10,}")


def _assert_clean(text: str) -> None:
    assert KEY_MARK not in text and "BEGIN OPENSSH" not in text, "개인키가 출력에 있다"
    assert not _FULL_OCID.search(text), f"전체 OCID 가 출력에 있다: {_FULL_OCID.search(text)}"


def test_create_order_idempotent_and_clean(fake: dict[str, str]) -> None:
    r = _run(CREATE, fake)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    calls = _calls(fake)
    assert all(c.startswith("--region ap-chuncheon-1 ") for c in calls[1:]), "홈 리전으로"
    order = [
        "network vcn create",
        "network internet-gateway create",
        "network route-table update",
        "network security-list create",
        "network subnet create",
        "compute instance launch",
    ]
    idx = [next(i for i, c in enumerate(calls) if o in c) for o in order]
    assert idx == sorted(idx), "VCN → IGW → 라우트 → 보안 목록 → 서브넷 → 인스턴스 순서"
    vcn = next(c for c in calls if "vcn create" in c)
    assert '["10.0.0.0/16"]' in vcn
    sl = next(c for c in calls if "security-list create" in c)
    for port in (22, 80, 443):
        assert f'"min":{port},"max":{port}' in sl
    assert '"protocol":"all"' in sl and "5432" not in sl and "8000" not in sl
    assert "10.0.0.0/24" in next(c for c in calls if "subnet create" in c)
    img = next(c for c in calls if "image list" in c)
    assert "Canonical Ubuntu" in img and "24.04" in img and "VM.Standard.A1.Flex" in img
    launch = next(c for c in calls if "instance launch" in c)
    for want in (
        "VM.Standard.A1.Flex",
        '{"ocpus":4,"memoryInGBs":24}',
        "--boot-volume-size-in-gbs 100",
        "--assign-public-ip true",
        "--display-name kbj-vm",
        "/.ssh/kbj_vm.pub",
    ):
        assert want in launch, want
    # cloud-init: install.sh 를 받아 ubuntu 로, 브랜치 전달
    ud = (Path(fake["FAKE_DIR"]) / "user_data").read_text(encoding="utf-8")
    assert "/claude/build/scripts/vm/install.sh" in ud and '--branch "claude/build"' in ud
    assert "sudo -u ubuntu" in ud and "keys.sh" in ud
    # 결과 안내: IP·sslip.io·다음 명령
    assert "203.0.113.10" in out and "https://203-0-113-10.sslip.io" in out
    assert "ssh -i " in out and "/.ssh/kbj_vm ubuntu@203.0.113.10" in out
    assert "cd ~/kbj && bash scripts/vm/keys.sh" in out and "bash scripts/vm/start.sh" in out
    assert "/var/log/kbj-install.log" in out
    _assert_clean(out)
    key = Path(fake["HOME"]) / ".ssh" / "kbj_vm"
    assert stat.S_IMODE(key.stat().st_mode) == 0o600

    # 두 번째 실행: 모두 재사용 — 만들기·고치기 없음, 키도 그대로
    log = Path(fake["FAKE_DIR"]) / "calls.log"
    log.unlink()
    r2 = _run(CREATE, fake)
    assert r2.returncode == 0, (r2.stdout + r2.stderr)[-3000:]
    assert _mutating(_calls(fake)) == []
    assert "있음" in r2.stdout and "203-0-113-10.sslip.io" in r2.stdout
    _assert_clean(r2.stdout + r2.stderr)


def test_capacity_retry_rotates_ad(fake: dict[str, str]) -> None:
    r = _run(CREATE, {**fake, "FAKE_CAPACITY_FAILS": "2"})
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    launches = [c for c in _calls(fake) if "instance launch" in c]
    ads = [re.search(r"--availability-domain (\S+)", c).group(1) for c in launches]  # type: ignore[union-attr]
    assert [a.rsplit("-", 1)[1] for a in ads] == ["1", "2", "1"], ads
    assert out.count("[시도 ") == 3 and "자리 없음" in out
    _assert_clean(out)  # 오류 원문의 요청 OCID 도 가린다


def test_capacity_gives_up_after_deadline(fake: dict[str, str]) -> None:
    env = {**fake, "FAKE_CAPACITY_FAILS": "99", "KBJ_RETRY_MAX_MINUTES": "0"}
    r = _run(CREATE, env)
    assert r.returncode != 0
    assert "자리가 나지 않았다" in r.stderr
    assert len([c for c in _calls(fake) if "instance launch" in c]) == 1


def test_dry_run_calls_nothing(fake: dict[str, str]) -> None:
    r = _run(CREATE, fake, "--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out[-3000:]
    assert _calls(fake) == [], "dry-run 은 oci 를 실행하지 않는다"
    assert "[dry-run] oci network vcn create" in out and "compute instance launch" in out
    assert not (Path(fake["HOME"]) / ".ssh" / "kbj_vm").exists()
    _assert_clean(out)


def test_free_limit_stops(fake: dict[str, str]) -> None:
    r = _run(CREATE, {**fake, "FAKE_OTHER_OCPUS": "2.0", "FAKE_OTHER_MEM": "12.0"})
    assert r.returncode != 0
    assert "무료 한도" in r.stderr
    assert not [c for c in _calls(fake) if "instance launch" in c]


def test_fatal_error_explained_not_retried(fake: dict[str, str], tmp_path: Path) -> None:
    bad = tmp_path / "bin" / "oci"
    bad.write_text(
        '#!/usr/bin/env bash\nprintf \'%s\\n\' "$*" >> "$FAKE_DIR/calls.log"\n'
        'printf \'ServiceError:\\n{\\n "code": "NotAuthorizedOrNotFound",\\n'
        f' "message": "Authorization failed for {TENANCY}"\\n}}\\n\' >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    r = _run(CREATE, fake)
    assert r.returncode != 0
    assert "권한이 없거나" in r.stderr and "NotAuthorizedOrNotFound" in r.stderr
    _assert_clean(r.stderr)
    assert len(_calls(fake)) == 1


def test_status_lists_only(fake: dict[str, str]) -> None:
    assert _run(CREATE, fake).returncode == 0
    (Path(fake["FAKE_DIR"]) / "calls.log").unlink()
    r = _run(CREATE, fake, "--status")
    assert r.returncode == 0, r.stderr
    assert _mutating(_calls(fake)) == []
    assert "kbj-vm" in r.stdout and "RUNNING" in r.stdout and "203-0-113-10.sslip.io" in r.stdout
    _assert_clean(r.stdout + r.stderr)


def test_destroy_requires_typed_phrase(fake: dict[str, str]) -> None:
    assert _run(CREATE, fake).returncode == 0
    log = Path(fake["FAKE_DIR"]) / "calls.log"
    log.unlink()
    r = _run(DESTROY, fake, stdin="yes\n")
    assert r.returncode != 0 and "취소" in r.stderr
    assert _mutating(_calls(fake)) == []
    r = _run(DESTROY, fake, stdin="kbj 지우기\n")
    assert r.returncode == 0, (r.stdout + r.stderr)[-3000:]
    muts = _mutating(_calls(fake))
    order = ["instance terminate", "subnet delete", "security-list delete", "route-table update"]
    order += ["internet-gateway delete", "vcn delete"]
    idx = [next(i for i, c in enumerate(muts) if o in c) for o in order]
    assert idx == sorted(idx)
    assert "--preserve-boot-volume false" in muts[0]
    _assert_clean(r.stdout + r.stderr)


def test_bad_args_and_branch(fake: dict[str, str]) -> None:
    r = _run(CREATE, fake, "--branch", "x;rm")
    assert r.returncode != 0 and _calls(fake) == []
    r = _run(CREATE, fake, "--nope")
    assert r.returncode != 0
    r = _run(CREATE, {**fake, "KBJ_BRANCH": "main"}, "--dry-run")
    assert "/main/scripts/vm/install.sh" not in r.stdout  # user_data 는 dry-run 에서 만들지 않는다
    r = _run(CREATE, {**fake, "KBJ_BRANCH": "main"})
    ud = (Path(fake["FAKE_DIR"]) / "user_data").read_text(encoding="utf-8")
    assert "/main/scripts/vm/install.sh" in ud and '--branch "main"' in ud


def test_guide_quick_path() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    head = guide.split("## ①", 1)[0]
    assert "빠른 길" in head and "oci_create.sh" in head and "Cloud Shell" in head
    raw = "https://raw.githubusercontent.com/kimbob0604-cmyk/KBJ/claude/build/scripts/vm"
    assert f"curl -fsSL {raw}/oci_create.sh | bash" in head
    assert "수동으로 할 때" in guide
    adr = next((ROOT / "docs" / "adr").glob("0040-*.md")).read_text(encoding="utf-8")
    assert "oci_create.sh" in adr
