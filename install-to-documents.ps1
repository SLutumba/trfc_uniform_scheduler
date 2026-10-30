# Run from a local copy of this project. Copy files into the exact requested Windows folder.
$ErrorActionPreference = 'Stop'
$source = Split-Path -Parent $MyInvocation.MyCommand.Path
$destination = 'C:\Users\sheki\Documents\TRFC uniform scheduler'
if (-not (Test-Path -LiteralPath $source)) { throw 'Project source folder not found.' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
if ([IO.Path]::GetFullPath($source).TrimEnd('\') -eq [IO.Path]::GetFullPath($destination).TrimEnd('\')) {
    Write-Host "Already in $destination"
    exit 0
}
foreach ($file in Get-ChildItem -LiteralPath $source -File -Recurse | Where-Object {
    $_.FullName -notmatch '[\\/](\.git|\.venv|__pycache__|\.pytest_cache|\.review-[^\\/]*|test-results)[\\/]' -and
    $_.Name -ne '.env' -and $_.Extension -notin @('.db', '.sqlite', '.sqlite3')
}) {
    $relative = $file.FullName.Substring($source.Length).TrimStart('\', '/')
    $target = Join-Path $destination $relative
    New-Item -ItemType Directory -Path (Split-Path $target) -Force | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $target -Force
}
Write-Host "Files copied into $destination"
