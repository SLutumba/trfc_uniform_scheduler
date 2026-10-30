# Run from an extracted copy of the TRFC Uniform Hub source.
# Requires Git installed and authenticated to GitHub.
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$repo = 'https://github.com/SLutumba/trfc_uniform_scheduler.git'
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git is not installed or not in PATH. Install Git for Windows first.'
}
if (-not (Test-Path '.git')) { git init -b main; if ($LASTEXITCODE -ne 0) { throw 'git init failed' } }
# An empty repository may have no commits; avoid replacing any other repository's history.
$remotes = @(git remote)
if ($remotes -contains 'origin') {
    $remote = git remote get-url origin
    if ($LASTEXITCODE -ne 0 -or $remote -ne $repo) { throw "Unexpected origin remote: $remote" }
} else {
    git remote add origin $repo
    if ($LASTEXITCODE -ne 0) { throw 'Failed to configure GitHub remote.' }
}
$existingHead = git ls-remote --heads origin main
if ($LASTEXITCODE -ne 0) { throw 'Cannot connect to GitHub. Check your connection and login.' }
if ($existingHead) { throw 'The GitHub repository already has commits. Use git pull/reconcile rather than overwriting history.' }
git add .
if ($LASTEXITCODE -ne 0) { throw 'git add failed' }
git commit -m 'Initial release: multi-ministry uniform scheduler'
if ($LASTEXITCODE -ne 0) { throw 'git commit failed. Check git user.name and user.email.' }
git push -u origin main
if ($LASTEXITCODE -ne 0) { throw 'git push failed. Authenticate and confirm repository access.' }
Write-Host 'Source pushed successfully to:' $repo
