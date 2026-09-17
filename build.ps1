# Build dist\To PDF.exe (single file, no console window).
# Usage:  powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    python -m venv .venv
}
$py = ".\.venv\Scripts\python.exe"
& $py -m pip install --quiet --upgrade pip
& $py -m pip install --quiet -r requirements.txt pyinstaller

& $py tools\make_icon.py
& $py -m PyInstaller --noconfirm --clean to-pdf.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Get-Item "dist\To PDF.exe"
"Built {0} ({1:N1} MB)" -f $exe.FullName, ($exe.Length / 1MB)
