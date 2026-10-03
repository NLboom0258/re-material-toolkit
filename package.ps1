<#
.SYNOPSIS
  Build the release package: dist/re-material-toolkit-<version>.zip
.DESCRIPTION
  Bundles a source snapshot plus the shipped binaries (exe/dll) into a ready-to-run zip.
  - dll already lives in material_toolkit/bin/ ; this script copies D3D_Shaders.exe
    (and optionally the translator exe) into the package's bin/.
  - GPL compliance: third_party/D3D_Shaders/ source is included as well.
.NOTES
  This script is intentionally ASCII-only (Windows PowerShell 5.1 mis-decodes
  non-ASCII .ps1 files that lack a BOM).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File package.ps1 -Version 0.1.0 -D3DShadersExe "E:\path\D3D_Shaders.exe"
#>
param(
    [string]$Version = "0.1.0",
    [string]$D3DShadersExe = "",
    [string]$TranslatorExe = ""
)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stage = Join-Path $root "build\re-material-toolkit-$Version"
$dist = Join-Path $root "dist"

Write-Host ("== package re-material-toolkit " + $Version + " ==")
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path $stage | Out-Null

# top-level files
foreach ($f in @("run.py", "run.bat", "README.md", "LICENSE", "THIRD_PARTY.md", "requirements.txt")) {
    $src = Join-Path $root $f
    if (Test-Path $src) { Copy-Item -LiteralPath $src -Destination $stage -Force }
}

# directories (skip __pycache__ / *.pyc)
function Copy-Tree($srcDir, $dstDir) {
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    Get-ChildItem -LiteralPath $srcDir -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($_.Name -eq "__pycache__") { return }
            Copy-Tree $_.FullName (Join-Path $dstDir $_.Name)
        } else {
            if ($_.Name -like "*.pyc") { return }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dstDir $_.Name) -Force
        }
    }
}
foreach ($d in @("material_toolkit", "material_studio", "third_party")) {
    Copy-Tree (Join-Path $root $d) (Join-Path $stage $d)
}

# binaries into the package bin/
$bin = Join-Path $stage "material_toolkit\bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null

if ($D3DShadersExe) {
    if (Test-Path -LiteralPath $D3DShadersExe) {
        Copy-Item -LiteralPath $D3DShadersExe -Destination (Join-Path $bin "D3D_Shaders.exe") -Force
        Write-Host "  + D3D_Shaders.exe"
    } else {
        Write-Warning ("D3D_Shaders.exe not found: " + $D3DShadersExe)
    }
} elseif (-not (Test-Path (Join-Path $bin "D3D_Shaders.exe"))) {
    Write-Warning "package has no D3D_Shaders.exe! Pass -D3DShadersExe or pre-place it in material_toolkit/bin/."
}

if ($TranslatorExe) {
    if (Test-Path -LiteralPath $TranslatorExe) {
        Copy-Item -LiteralPath $TranslatorExe -Destination (Join-Path $bin "hlsl_blend_dxbc_translator.exe") -Force
        Write-Host "  + hlsl_blend_dxbc_translator.exe"
    } else {
        Write-Warning ("translator exe not found: " + $TranslatorExe)
    }
}

# zip
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zip = Join-Path $dist "re-material-toolkit-$Version.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip

$a = Get-Item -LiteralPath $zip
Write-Host ""
Write-Host ("done: " + $zip)
Write-Host ("size: " + [math]::Round($a.Length / 1MB, 2) + " MB")
Write-Host ("staged at: " + $stage)
