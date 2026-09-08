# Bootstrap Whiskers Agent: Docker, Python 3.11 venv, requirements, then setup.py all.
# Real setup logic lives in terminal/script/setup.py — this script stays thin.
[CmdletBinding()]
param(
    [switch]$NoUv,
    [switch]$SkipDeps,
    [switch]$Yes,
    [switch]$Dev,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$ResetPlugins,
    [ValidateSet("openai", "anthropic", "gemini", "gemini-vertex")]
    [string]$Provider,
    [string]$From,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Remaining
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

function Test-DockerDaemon {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Error "docker is not on PATH. Install Docker Desktop, then retry."
        exit 1
    }
    # `docker info` writes routine WARNING: lines to stderr even on success; under
    # $ErrorActionPreference = "Stop" those get wrapped as terminating NativeCommandError
    # and abort the script against a healthy daemon. Check the exit code only.
    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & docker info *> $null
    $dockerRc = $LASTEXITCODE
    $ErrorActionPreference = $prevEap
    if ($dockerRc -ne 0) {
        Write-Error "Docker daemon is not running. Start Docker Desktop and retry."
        exit 1
    }
}

function Install-UvIfNeeded {
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }
    $localUv = Join-Path $env:USERPROFILE ".local\bin\uv.exe"
    if (Test-Path $localUv) {
        $env:PATH = "$(Split-Path $localUv);$env:PATH"
        return $true
    }
    Write-Host "Installing uv (https://astral.sh/uv)..."
    try {
        irm https://astral.sh/uv/install.ps1 | iex
    } catch {
        Write-Warning "uv install failed: $_"
        return $false
    }
    if (Get-Command uv -ErrorAction SilentlyContinue) { return $true }
    if (Test-Path $localUv) {
        $env:PATH = "$(Split-Path $localUv);$env:PATH"
        return $true
    }
    return $false
}

Test-DockerDaemon

$python = $null
if (-not $SkipDeps) {
    $usedUv = $false
    if (-not $NoUv) {
        if (Install-UvIfNeeded) { $usedUv = $true }
        else { Write-Warning "uv unavailable; falling back to python -m venv + pip" }
    }

    if ($usedUv) {
        uv venv .venv --python 3.11
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        uv pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        $py = Get-Command python3.11 -ErrorAction SilentlyContinue
        if (-not $py) { $py = Get-Command python -ErrorAction SilentlyContinue }
        if (-not $py) {
            Write-Error "Python 3.11+ not found. Install it or omit -NoUv so uv can fetch CPython."
            exit 1
        }
        & $py.Source -m venv .venv
        $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
        & $venvPy -m pip install -U pip
        & $venvPy -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $python = Join-Path $Root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        $python = Join-Path $Root ".venv\bin\python"
    }
} else {
    $venvPy = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $python = $venvPy }
    else { $python = "python" }
}

$forward = @()
if ($Yes) { $forward += "--yes" }
if ($Dev) { $forward += "--dev" }
if ($Force) { $forward += "--force" }
if ($DryRun) { $forward += "--dry-run" }
if ($ResetPlugins) { $forward += "--reset-plugins" }
if ($Provider) { $forward += @("--provider", $Provider) }
if ($From) { $forward += @("--from", $From) }
if ($Remaining) { $forward += $Remaining }

$setup = Join-Path $Root "terminal\script\setup.py"
& $python $setup all @forward
exit $LASTEXITCODE
