# 로컬에서 보드를 돌린다. Windows PowerShell.
#
#   .\board\local.ps1 setup     처음 한 번 — 가상환경 + 의존성 + .env 틀
#   .\board\local.ps1 init      전 종목 과거 일봉 적재 (30~60분, 처음 한 번)
#   .\board\local.ps1 daily     오늘치 수집 → 집계 → 랭킹 → 엑셀 → 화면
#   .\board\local.ps1 open      만들어 둔 화면을 브라우저로
#   .\board\local.ps1           daily 후 open
#
# 처음 실행이 막히면 (실행 정책):
#   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
$ErrorActionPreference = 'Stop'

$Here = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Here
$Venv = Join-Path $Here '.venv'
$Py = Join-Path $Venv 'Scripts\python.exe'

function Need-Venv {
  if (-not (Test-Path $Py)) {
    Write-Error "가상환경이 없다. 먼저: .\board\local.ps1 setup"
  }
}

function Cmd-Setup {
  $sys = Get-Command python -ErrorAction SilentlyContinue
  if (-not $sys) { Write-Error "python 이 없다. python.org 에서 설치하고 'Add to PATH' 를 켜라" }
  & python -c 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'
  if ($LASTEXITCODE -ne 0) { Write-Error "Python 3.9 이상이 필요하다" }

  if (-not (Test-Path $Venv)) { & python -m venv $Venv }
  & $Py -m pip install -q --upgrade pip
  & $Py -m pip install -q -r board\requirements.txt
  Write-Host "의존성 설치 완료"

  if (-not (Test-Path board\.env)) {
    Copy-Item board\.env.example board\.env
    Write-Host "board\.env 를 만들었다. 열어서 인증키를 채워라 (이 파일은 커밋되지 않는다)"
  } else {
    Write-Host "board\.env 는 이미 있다"
  }

  & $Py -m board.run --test
  Write-Host "`n다음: .\board\local.ps1 init   (처음 한 번, 30~60분)"
}

$cmd = if ($Args.Count -gt 0) { $Args[0] } else { '' }
$rest = if ($Args.Count -gt 1) { $Args[1..($Args.Count - 1)] } else { @() }

switch ($cmd) {
  'setup' { Cmd-Setup }
  'init'  { Need-Venv; & $Py -m board.run --init  @rest }
  'daily' { Need-Venv; & $Py -m board.run --daily @rest }
  'open'  { Need-Venv; & $Py -m board.run --serve @rest }
  '--'    { Need-Venv; & $Py -m board.run @rest }
  ''      { Need-Venv; & $Py -m board.run --daily; & $Py -m board.run --serve }
  default { Need-Venv; & $Py -m board.run @Args }
}
