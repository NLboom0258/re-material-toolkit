<#
.SYNOPSIS
  Build the release package: dist/re-material-toolkit-<version>.zip
.DESCRIPTION
  Packages ONLY runtime-needed content (whitelist) plus the shipped binaries.
  Runtime content:
    run.py, run.bat, README.md, LICENSE, THIRD_PARTY.md, requirements.txt
    material_studio/
    material_toolkit/{__init__.py, material_toolkit.py, lib/, presets/, functions/,
                       pass_templates/ (minus _archive/), bin/}
  Excluded on purpose (repo-only, not needed to run):
    third_party/ (GPL source lives in the repo), pass_templates/_archive/,
    package.ps1, .gitignore, .git*.
  Binaries go to material_toolkit/bin/:
    - d3dcompiler_47.dll           (already committed in the repo)
    - D3D_Shaders.exe              (via -D3DShadersExe)
    - hlsl_blend_dxbc_translator.exe  (via -TranslatorExe)
.NOTES
  ASCII-only on purpose (Windows PowerShell 5.1 mis-decodes non-ASCII .ps1 without a BOM).
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File package.ps1 -Version 0.1.0 `
      -D3DShadersExe "path\D3D_Shaders.exe" -TranslatorExe "path\hlsl_blend_dxbc_translator.exe"
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

# ---- runtime files (whitelist) ----
foreach ($f in @("run.py", "run.bat", "README.md", "LICENSE", "THIRD_PARTY.md", "requirements.txt")) {
    $src = Join-Path $root $f
    if (Test-Path $src) { Copy-Item -LiteralPath $src -Destination $stage -Force }
}

function Copy-Tree($srcDir, $dstDir, $excludeDirs = @()) {
    New-Item -ItemType Directory -Force -Path $dstDir | Out-Null
    Get-ChildItem -LiteralPath $srcDir -Force | ForEach-Object {
        if ($_.PSIsContainer) {
            if ($_.Name -eq "__pycache__") { return }
            if ($excludeDirs -contains $_.Name) { return }
            Copy-Tree $_.FullName (Join-Path $dstDir $_.Name) $excludeDirs
        } else {
            if ($_.Name -like "*.pyc") { return }
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $dstDir $_.Name) -Force
        }
    }
}

# material_studio (whole)
Copy-Tree (Join-Path $root "material_studio") (Join-Path $stage "material_studio")

# material_toolkit: runtime parts only
$tkSrc = Join-Path $root "material_toolkit"
$tkDst = Join-Path $stage "material_toolkit"
New-Item -ItemType Directory -Force -Path $tkDst | Out-Null
foreach ($f in @("__init__.py", "material_toolkit.py")) {
    Copy-Item -LiteralPath (Join-Path $tkSrc $f) -Destination $tkDst -Force
}
Copy-Tree (Join-Path $tkSrc "lib") (Join-Path $tkDst "lib")
Copy-Tree (Join-Path $tkSrc "presets") (Join-Path $tkDst "presets")
# functions/: 只打包"已被 git 跟踪"的函数(排除本地未提交的临时/实验函数)
$fnSrc = Join-Path $tkSrc "functions"
$fnDst = Join-Path $tkDst "functions"
New-Item -ItemType Directory -Force -Path $fnDst | Out-Null
$trackedFn = @()
try { $trackedFn = @(& git -C $root ls-files "material_toolkit/functions" 2>$null) } catch { }
if ($trackedFn.Count -gt 0) {
    foreach ($rel in $trackedFn) {
        $src = Join-Path $root $rel
        if (Test-Path -LiteralPath $src) {
            Copy-Item -LiteralPath $src -Destination (Join-Path $fnDst (Split-Path $rel -Leaf)) -Force
        }
    }
    Write-Host ("  functions: " + $trackedFn.Count + " (git-tracked only)")
} else {
    Copy-Tree $fnSrc $fnDst
}
Copy-Tree (Join-Path $tkSrc "pass_templates") (Join-Path $tkDst "pass_templates") @("_archive")

# ---- binaries into package bin/ ----
$bin = Join-Path $tkDst "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null

$dll = Join-Path $tkSrc "bin\d3dcompiler_47.dll"
if (Test-Path $dll) {
    Copy-Item -LiteralPath $dll -Destination $bin -Force
} else {
    Write-Warning "missing d3dcompiler_47.dll in repo material_toolkit/bin/."
}

if ($D3DShadersExe -and (Test-Path -LiteralPath $D3DShadersExe)) {
    Copy-Item -LiteralPath $D3DShadersExe -Destination (Join-Path $bin "D3D_Shaders.exe") -Force
    Write-Host "  + D3D_Shaders.exe"
} elseif (-not (Test-Path (Join-Path $bin "D3D_Shaders.exe"))) {
    Write-Warning "no D3D_Shaders.exe. Pass -D3DShadersExe or pre-place it in material_toolkit/bin/."
}

if ($TranslatorExe -and (Test-Path -LiteralPath $TranslatorExe)) {
    Copy-Item -LiteralPath $TranslatorExe -Destination (Join-Path $bin "hlsl_blend_dxbc_translator.exe") -Force
    Write-Host "  + hlsl_blend_dxbc_translator.exe"
} elseif (-not (Test-Path (Join-Path $bin "hlsl_blend_dxbc_translator.exe"))) {
    Write-Warning "no hlsl_blend_dxbc_translator.exe (optional). Pass -TranslatorExe to include it."
}

# ---- zip ----
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zip = Join-Path $dist "re-material-toolkit-$Version.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip

$a = Get-Item -LiteralPath $zip
Write-Host ""
Write-Host ("done: " + $zip)
Write-Host ("size: " + [math]::Round($a.Length / 1MB, 2) + " MB")
Write-Host ("staged at: " + $stage)
