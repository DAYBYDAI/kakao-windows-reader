param(
    [string]$Python,
    [string]$SkillsRoot = (Join-Path $env:USERPROFILE '.claude\skills'),
    [switch]$Force
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$sourceSkill = Join-Path $PSScriptRoot 'skill\kakao-windows'
if (-not (Test-Path -LiteralPath (Join-Path $sourceSkill 'tool\entrypoint.py'))) {
    throw 'Use the release ZIP, or run scripts/build-release.ps1 first.'
}
if (-not $Python) {
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) { $Python = (& $launcher.Source -3 -c "import sys; print(sys.executable)" | Select-Object -Last 1) }
    if (-not $Python) {
        $candidate = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.Source -notlike '*\WindowsApps\*') { $Python = $candidate.Source }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw 'Install 64-bit Python 3.11 or newer, then retry. Or use -Python C:\path\python.exe'
}
& $Python -c "import sys; assert sys.version_info >= (3,11) and sys.maxsize > 2**32, '64-bit Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Unsupported Python version or architecture.' }
$parent = [IO.Path]::GetFullPath($SkillsRoot)
New-Item -ItemType Directory -Path $parent -Force | Out-Null
$destination = [IO.Path]::GetFullPath((Join-Path $parent 'kakao-windows'))
if ([IO.Path]::GetDirectoryName($destination) -ne $parent.TrimEnd('\')) { throw 'Invalid target boundary.' }
if ((Test-Path -LiteralPath $destination) -and -not $Force) {
    throw 'Skill already exists. Use -Force to preserve it as a backup and replace it.'
}
$staging = Join-Path $parent ('kakao-windows-install-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $staging | Out-Null
Copy-Item -Path (Join-Path $sourceSkill '*') -Destination $staging -Recurse
# Create the environment at its final path: Windows virtualenv launchers are not relocatable.
if (Test-Path -LiteralPath $destination) {
    $backup = [IO.Path]::GetFullPath((Join-Path $parent ('kakao-windows-backup-' + [guid]::NewGuid().ToString('N'))))
    if ([IO.Path]::GetDirectoryName($backup) -ne $parent.TrimEnd('\')) { throw 'Invalid backup boundary.' }
    Move-Item -LiteralPath $destination -Destination $backup
    Write-Host ('Previous skill preserved at: ' + $backup)
}
if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($staging)) -ne $parent.TrimEnd('\')) { throw 'Invalid staging boundary.' }
Move-Item -LiteralPath $staging -Destination $destination
$venv = Join-Path $destination 'tool\.venv'
& $Python -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed; the source skill is preserved.' }
$venvPython = Join-Path $venv 'Scripts\python.exe'
& $venvPython -m pip install --only-binary=:all: -r (Join-Path $destination 'tool\requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed. Check network/proxy access to PyPI and retry with -Force.' }
& $venvPython (Join-Path $destination 'tool\entrypoint.py') --version
if ($LASTEXITCODE -ne 0) { throw 'CLI startup verification failed.' }
Write-Host ('Installed: ' + $destination)
Write-Host 'In native Claude Code, run /kakao-windows and ask it to set up this computer.'
Write-Host 'No hourly task is created by this installer. First-time skill setup configures it when requested.'
