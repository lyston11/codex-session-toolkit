param(
    [switch] $Editable,
    [switch] $Force,
    [Alias("h")]
    [switch] $Help,
    [string] $Python
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
$packageName = "codex_session_toolkit"
$venvDir = if ($env:VENV_DIR) { $env:VENV_DIR } else { Join-Path $projectRoot ".venv" }

function Show-Usage {
    @"
Usage: .\install.ps1 [-Editable] [-Force] [-Python <python-bin>]

Create or refresh an isolated local virtual environment under .\.venv.
The installer keeps package changes inside the project and does not modify
your base Python environment.

Options:
  -Editable        Install in editable mode for local development
  -Force           Recreate the local .venv before installing
  -Python <bin>    Use a specific Python executable
"@
}

function Resolve-PythonCommand {
    param([string] $PreferredPython)

    if ($PreferredPython) {
        return ,@($PreferredPython)
    }
    if (Get-Command "python" -ErrorAction SilentlyContinue) { return ,@("python") }
    if (Get-Command "py" -ErrorAction SilentlyContinue) { return ,@("py", "-3") }
    if (Get-Command "python3" -ErrorAction SilentlyContinue) { return ,@("python3") }
    return $null
}

function Assert-LastExitCode {
    param([string] $StepName)

    if ($LASTEXITCODE -ne 0) {
        throw "$StepName failed with exit code $LASTEXITCODE."
    }
}

function Test-VenvUsesSystemSitePackages {
    param([string] $VenvPath)

    $cfgFile = Join-Path $VenvPath "pyvenv.cfg"
    if (-not (Test-Path $cfgFile)) {
        return $false
    }

    $cfgText = Get-Content $cfgFile -Raw
    return $cfgText -match '(?im)^\s*include-system-site-packages\s*=\s*true\s*$'
}

function Get-SitePackagesPath {
    param([string] $PythonExe)

    return (& $PythonExe -c "import sysconfig; print(sysconfig.get_path('purelib'))").Trim()
}

function Install-LocalPackage {
    param(
        [string] $PythonExe,
        [string] $ProjectRoot,
        [switch] $Editable
    )

    $sitePackages = Get-SitePackagesPath -PythonExe $PythonExe
    $pthFile = Join-Path $sitePackages "$packageName-local.pth"
    $installedPackageDir = Join-Path $sitePackages $packageName
    $sourcePackageDir = Join-Path $ProjectRoot "src\$packageName"

    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    if (Test-Path $pthFile) {
        Remove-Item -Force $pthFile
    }
    if (Test-Path $installedPackageDir) {
        Remove-Item -Recurse -Force $installedPackageDir
    }

    if ($Editable) {
        Set-Content -Path $pthFile -Value (Join-Path $ProjectRoot "src") -Encoding UTF8
    } else {
        Copy-Item -Recurse -Force $sourcePackageDir $installedPackageDir
    }
}

function Install-ConsoleWrappers {
    param([string] $PythonExe)

    $wrapperCmd = Join-Path $venvDir "Scripts\codex-session-toolkit.cmd"
    $wrapperPs1 = Join-Path $venvDir "Scripts\codex-session-toolkit.ps1"
    $cmdBody = @"
@echo off
"%~dp0python.exe" -m $packageName %*
"@
    $ps1Body = @"
param(
    [Parameter(ValueFromRemainingArguments = `$true)]
    [string[]] `$PassthroughArgs
)
& "`$PSScriptRoot\python.exe" -m $packageName @PassthroughArgs
exit `$LASTEXITCODE
"@

    Set-Content -Path $wrapperCmd -Value $cmdBody -Encoding ASCII
    Set-Content -Path $wrapperPs1 -Value $ps1Body -Encoding UTF8
}

if ($Help) {
    Show-Usage
    exit 0
}

$pyCmd = Resolve-PythonCommand -PreferredPython $Python
if (-not $pyCmd) {
    Write-Host "Error: Python not found. Install Python 3 first." -ForegroundColor Red
    exit 127
}

if ($Force -and (Test-Path $venvDir)) {
    Remove-Item -Recurse -Force $venvDir
}

if ((Test-Path $venvDir) -and (Test-VenvUsesSystemSitePackages -VenvPath $venvDir)) {
    Write-Host "Existing venv is not isolated (system site packages are enabled)." -ForegroundColor Yellow
    Write-Host "Recreating $venvDir as an isolated local environment..." -ForegroundColor Yellow
    Remove-Item -Recurse -Force $venvDir
}

$pythonExe = $pyCmd[0]
$pythonPreArgs = @()
if ($pyCmd.Length -gt 1) {
    $pythonPreArgs = $pyCmd[1..($pyCmd.Length - 1)]
}

Write-Host "=============================================" -ForegroundColor Cyan
Write-Host " Codex Session Toolkit - Installer (Windows)" -ForegroundColor Cyan
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "Project:   $projectRoot"
Write-Host "Python:    $pythonExe $($pythonPreArgs -join ' ')"
Write-Host "Venv:      $venvDir"
Write-Host "Isolation: enabled"
if ($Editable) {
    Write-Host "Mode:      editable"
} else {
    Write-Host "Mode:      standard"
}

& $pythonExe @pythonPreArgs -m venv $venvDir
Assert-LastExitCode "python -m venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Error: failed to create local venv at $venvDir" -ForegroundColor Red
    exit 1
}

Install-LocalPackage -PythonExe $venvPython -ProjectRoot $projectRoot -Editable:$Editable
Install-ConsoleWrappers -PythonExe $venvPython

Write-Host ""
Write-Host "Install complete." -ForegroundColor Green
Write-Host "Run now:"
Write-Host "  .\codex-session-toolkit.cmd"
Write-Host "Version:"
Write-Host "  .\.venv\Scripts\codex-session-toolkit.cmd --version"
