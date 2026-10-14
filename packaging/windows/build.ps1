<#
Build the installable Windows bundle.

    pwsh packaging/windows/build.ps1 [-Out dist\LS-IHC] [-NoR] [-PythonVersion 3.13.x]

Produces a self-contained folder:

    runtime\     python.org embeddable Python 3.13 with every library installed
    app\ scripts\ analysis\   the repository, unchanged
    R\           R and the figure packages (left out with -NoR)
    keras\       StarDist's pretrained model, so Measure works offline
    The Great IHC Leap Forward.cmd           starts the app from the folder (portable use)

installer.iss wraps that folder into The-Great-IHC-Leap-Forward-Setup.exe.

Why not PyInstaller (app\lsapp.spec): the stages are loaded by file path at run
time, so a freezer cannot see what they import, and the spec had to leave
StarDist and TensorFlow out. An embedded interpreter with a real site-packages
runs every stage exactly as it runs from source.

Python 3.13 rather than 3.14: pylibCZIrw publishes no cp314 wheel.
#>
[CmdletBinding()]
param(
    [string]$Out = "dist\LS-IHC",
    [string]$PythonVersion = "",     # empty = newest 3.13.x on python.org
    [string]$RVersion = "",          # empty = current CRAN release
    [switch]$NoR
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"      # Invoke-WebRequest is 10x slower with the bar

$Repo = (Resolve-Path "$PSScriptRoot\..\..").Path
$Out = [IO.Path]::GetFullPath((Join-Path $Repo $Out))
$Cache = Join-Path $Repo "work\packaging-cache"
New-Item -ItemType Directory -Force $Cache | Out-Null

function Get-Cached([string]$Url) {
    $file = Join-Path $Cache ([IO.Path]::GetFileName($Url))
    if (-not (Test-Path $file)) {
        Write-Host "  downloading $Url"
        Invoke-WebRequest $Url -OutFile "$file.part"
        Move-Item "$file.part" $file
    }
    return $file
}

function Invoke-Checked([string]$Exe, [string[]]$ArgList) {
    & $Exe @ArgList
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($ArgList -join ' ') exited $LASTEXITCODE" }
}

if (Test-Path $Out) { Remove-Item -Recurse -Force $Out }
New-Item -ItemType Directory -Force $Out | Out-Null

# --- Python ---------------------------------------------------------------
if (-not $PythonVersion) {
    $index = (Invoke-WebRequest "https://www.python.org/ftp/python/").Content
    $PythonVersion = [regex]::Matches($index, 'href="(3\.13\.\d+)/"') |
        ForEach-Object { [version]$_.Groups[1].Value } |
        Sort-Object -Descending |
        Where-Object {
            # A version folder appears before its files do; take the newest one
            # that actually has the embeddable zip.
            try { Invoke-WebRequest -Method Head "https://www.python.org/ftp/python/$_/python-$_-embed-amd64.zip" | Out-Null; $true }
            catch { $false }
        } | Select-Object -First 1
    $PythonVersion = "$PythonVersion"
}
Write-Host "Python $PythonVersion"
$Runtime = Join-Path $Out "runtime"
Expand-Archive (Get-Cached "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip") $Runtime

# The ._pth file IS sys.path for an embedded interpreter. Add site-packages,
# turn `import site` on (so sitecustomize runs), and add the install root so
# `python -m app` finds the app package without depending on the working dir.
$pth = Get-ChildItem $Runtime -Filter "python3*._pth" | Select-Object -First 1
$zipName = (Get-ChildItem $Runtime -Filter "python3*.zip" | Select-Object -First 1).Name
Set-Content -Encoding ascii $pth.FullName @($zipName, ".", "Lib\site-packages", "..", "import site")
Copy-Item "$PSScriptRoot\sitecustomize.py" $Runtime

# TensorFlow needs the MSVC C++ runtime (msvcp140*.dll), which the embeddable
# zip does not carry and a fresh laptop may not have. App-local copies are
# Microsoft's supported alternative to running the redistributable as admin.
foreach ($dll in "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140_1.dll", "concrt140.dll") {
    $src = Join-Path $env:WINDIR "System32\$dll"
    if ((Test-Path $src) -and -not (Test-Path (Join-Path $Runtime $dll))) { Copy-Item $src $Runtime }
}

$Py = Join-Path $Runtime "python.exe"
Invoke-Checked $Py @((Get-Cached "https://bootstrap.pypa.io/get-pip.py"), "--no-warn-script-location", "-q")

# pyinstaller is in requirements.txt for build_app.bat only.
$req = Join-Path $Cache "requirements-app.txt"
Get-Content "$Repo\requirements.txt" | Where-Object { $_ -notmatch '^\s*pyinstaller' } | Set-Content $req
Invoke-Checked $Py @("-m", "pip", "install", "--no-warn-script-location", "--prefer-binary",
    "-r", $req, "-r", "$Repo\requirements-czi.txt", "-r", "$PSScriptRoot\requirements-bundle.txt")
& $Py -m pip freeze | Set-Content (Join-Path $Out "BUNDLE_VERSIONS.txt")
Add-Content (Join-Path $Out "BUNDLE_VERSIONS.txt") "python==$PythonVersion"

# --- The repository -------------------------------------------------------
foreach ($d in "app", "scripts", "analysis") {
    Copy-Item -Recurse "$Repo\$d" (Join-Path $Out $d)
}
foreach ($f in "config.example.json", "README.md") { Copy-Item "$Repo\$f" $Out }
Get-ChildItem $Out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Set-Content -Encoding ascii (Join-Path $Out "The Great IHC Leap Forward.cmd") '@start "" "%~dp0runtime\pythonw.exe" -m app %*'

# --- StarDist model -------------------------------------------------------
# Fetched once here so the first Measure run needs no network. stardist ends
# from_pretrained by symlinking <key> -> <key>_extracted, which an unelevated
# user cannot create; a real copy in its place means it never tries.
$env:KERAS_HOME = Join-Path $Out "keras"
Invoke-Checked $Py @("-c", "from stardist.models import StarDist2D; StarDist2D.from_pretrained('2D_versatile_fluo')")
$modelDir = Join-Path $env:KERAS_HOME "models\StarDist2D\2D_versatile_fluo"
$link = Join-Path $modelDir "2D_versatile_fluo"
$extracted = Join-Path $modelDir "2D_versatile_fluo_extracted"
if (Test-Path $extracted) {
    if (Test-Path $link) { (Get-Item $link).Delete() }
    Copy-Item -Recurse $extracted $link
}
Remove-Item Env:\KERAS_HOME

# --- R --------------------------------------------------------------------
if (-not $NoR) {
    if (-not $RVersion) {
        $page = (Invoke-WebRequest "https://cloud.r-project.org/bin/windows/base/").Content
        $RVersion = [regex]::Match($page, 'R-(\d+\.\d+\.\d+)-win\.exe').Groups[1].Value
        $RUrl = "https://cloud.r-project.org/bin/windows/base/R-$RVersion-win.exe"
    } else {
        $RUrl = "https://cloud.r-project.org/bin/windows/base/old/$RVersion/R-$RVersion-win.exe"
    }
    Write-Host "R $RVersion"
    $RDir = Join-Path $Out "R"
    $setup = Get-Cached $RUrl
    $p = Start-Process $setup -Wait -PassThru -ArgumentList @(
        "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CURRENTUSER", "/NOICONS",
        "/COMPONENTS=main,x64",
        "/DIR=`"$RDir`"")
    if ($p.ExitCode -ne 0) { throw "R installer exited $($p.ExitCode)" }
    $Rscript = Join-Path $RDir "bin\Rscript.exe"
    # Into R's own library, so the bundle is the whole of R as the app sees it.
    $lib = (Join-Path $RDir "library") -replace '\\', '/'
    Invoke-Checked $Rscript @("-e",
        "install.packages(c('ggplot2','ggtext','lmerTest','emmeans','multcomp','officer','readxl'), lib='$lib', repos='https://cloud.r-project.org', type='binary'); " +
        "for (p in c('ggplot2','ggtext','lmerTest','emmeans','multcomp','officer','readxl')) library(p, character.only=TRUE, lib.loc='$lib')")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $RDir "doc\manual")
    Add-Content (Join-Path $Out "BUNDLE_VERSIONS.txt") "R==$RVersion"
}

# Precompile, so the install folder is not written to on first start and the
# uninstaller removes everything it put there. Not checked: a few packages ship
# test fixtures that are deliberately not valid Python.
& $Py -m compileall -q -j 0 $Out | Out-Null

$size = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Built {0} ({1:N0} MB)" -f $Out, $size)

# compileall may exit non-zero (see above); that is not this script failing.
exit 0
