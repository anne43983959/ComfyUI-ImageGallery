<#
投放 / 撤回：把沙箱里的源码副本送到 ComfyUI 的 custom_nodes 目录。
源码永远是唯一真实副本；custom_nodes 里那份可以随时删掉重来。

  pwsh -File tools\deploy.ps1            投放（镜像复制）
  pwsh -File tools\deploy.ps1 -List      只列出会复制/删除什么，不落盘
  pwsh -File tools\deploy.ps1 -Remove    撤回（删掉部署副本）
#>
[CmdletBinding()]
param(
    [switch]$Remove,
    [switch]$List,
    [string]$Target = 'D:\ComfyUI\custom_nodes\ComfyUI-ImageGallery'
)

$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $PSScriptRoot

Write-Host '[deploy] 源码: ' $src
Write-Host '[deploy] 目标: ' $Target

if ($Remove) {
    if (Test-Path -LiteralPath $Target) {
        Remove-Item -LiteralPath $Target -Recurse -Force
        Write-Host '[deploy] 已删除部署副本；ComfyUI 下次启动即看不到本插件'
    } else {
        Write-Host '[deploy] 目标不存在，无需删除'
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $src)) { throw ('source not found: ' + $src) }

$roboArgs = @(
    $src, $Target,
    '/MIR',
    '/XD', 'cache', '__pycache__', '.git', 'tools', '.venv',
    '/XF', '*.pyc', '*.pyo'
)
# 演练模式保留文件清单；正式投放只留一行结果
if ($List) {
    $roboArgs += '/L'
} else {
    $roboArgs += @('/NFL', '/NDL', '/NJH', '/NJS', '/NP')
}

& robocopy @roboArgs
$rc = $LASTEXITCODE
if ($rc -ge 8) { throw ('robocopy 失败，退出码 ' + $rc) }

if (-not $List) {
    $required = @('__init__.py', 'web\gallery_button.js', 'templates\gallery.html', 'static\js\gallery.js')
    foreach ($rel in $required) {
        $full = Join-Path $Target $rel
        if (-not (Test-Path -LiteralPath $full)) { throw ('部署校验失败，缺少 ' + $rel) }
    }
    Write-Host '[deploy] 部署完成，robocopy 退出码' $rc
    Write-Host '[deploy] 重启 ComfyUI 后生效；顶栏应出现图片图标，点击打开 /gallery'
} else {
    Write-Host '[deploy] 以上为演练结果（未落盘）'
}
