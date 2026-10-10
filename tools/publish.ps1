# 发布：初始化仓库、提交、创建远端并推送（幂等，可重复运行）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$repo = 'anne43983959/ComfyUI-ImageGallery'
$url = "https://github.com/$repo.git"

Write-Host "[publish] 仓库根目录: $root"
if (-not (Test-Path '.git')) {
    git init -b main | Write-Host
}
git add -A
$staged = (git diff --cached --name-only | Measure-Object -Line).Lines
Write-Host "[publish] 暂存文件数: $staged"
if ($staged -gt 0) {
    git -c user.name='御坂桜' -c user.email='High43983959@163.com' commit -m 'Add ComfyUI image gallery plugin' | Write-Host
} else {
    Write-Host "[publish] 无改动需要提交"
}

gh repo view $repo *> $null
if ($LASTEXITCODE -eq 0) {
    Write-Host "[publish] 远端仓库已存在"
    git remote get-url origin *> $null
    if ($LASTEXITCODE -ne 0) { git remote add origin $url }
} else {
    Write-Host "[publish] 创建远端仓库 $repo"
    gh repo create $repo --public --source . --remote origin --description 'Same-origin image gallery for ComfyUI output: prompt search, model facets, workflow diff families, favorites, tags and trash.' --disable-wiki | Write-Host
}
git push -u origin main 2>&1 | Write-Host
Write-Host "[publish] 最近提交:"
git log --oneline -n 3 | Write-Host
Write-Host "[publish] 远端: $(git remote get-url origin)"
