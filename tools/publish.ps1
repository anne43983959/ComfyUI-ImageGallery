# 发布：初始化仓库、提交、创建远端并推送（幂等，可重复运行）
# 沙箱注意：本机 msys（sh.exe）起不来，任何走 shell 的 credential helper 都会失败，
# 所以推送这一段把凭据放进远端 URL 并显式清空 helper，推完立刻把 URL 改回干净的。
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$repo = 'anne43983959/ComfyUI-ImageGallery'
$clean = "https://github.com/$repo.git"

Write-Host "[publish] 仓库根目录: $root"
if (-not (Test-Path '.git')) {
    git init -b main | Write-Host
}
git add -A
$staged = (git diff --cached --name-only | Measure-Object -Line).Lines
Write-Host "[publish] 暂存文件数: $staged"
if ($staged -gt 0) {
    git -c user.name='御坂桜' -c user.email='High43983959@163.com' commit -m 'Update image gallery plugin' | Write-Host
} else {
    Write-Host "[publish] 无改动需要提交"
}

gh repo view $repo *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[publish] 创建远端仓库 $repo"
    gh repo create $repo --public --disable-wiki --description 'Same-origin image gallery for ComfyUI output: prompt search, model facets, workflow diff families, favorites, tags and trash.' | Write-Host
}

$token = (gh auth token).Trim()
if (-not $token) { throw '拿不到 gh token，无法推送' }
git remote set-url origin ("https://x-access-token:" + $token + "@github.com/$repo.git")
$env:GIT_TERMINAL_PROMPT = '0'
try {
    git -c credential.helper= push -u origin main 2>&1 | Write-Host
} finally {
    git remote set-url origin $clean
}
Write-Host "[publish] origin = $(git remote get-url origin)"
Write-Host "[publish] 最近提交:"
git log --oneline -n 3 | Write-Host
$local = (git rev-parse HEAD).Trim()
$remote = (gh api "repos/$repo/commits" --jq '.[0].sha').Trim()
Write-Host "[publish] 本地 HEAD = $local"
Write-Host "[publish] 远端 HEAD = $remote"
if ($local -eq $remote) { Write-Host "[publish] 校验通过：远端与本地一致" } else { Write-Host "[publish] 校验失败：远端与本地不一致" }
