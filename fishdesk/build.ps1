# 小肥鱼控制台 打包脚本：建 venv -> 装依赖 -> PyInstaller 单文件
param(
    [string]$BasePython = "",
    [switch]$SkipInstall
)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here

$candidates = @(
    $BasePython,
    $env:DSH_PYTHON,
    "C:\Users\one\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
) | Where-Object { $_ -and (Test-Path $_) }
if (-not $candidates) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -notmatch "WindowsApps") { $candidates = @($cmd.Source) }
}
if (-not $candidates) { throw "找不到可用的 Python，请用 -BasePython 指定解释器路径" }

$venvPy = Join-Path $here ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Host "== 创建 venv：$($candidates[0])" -ForegroundColor Cyan
    & $candidates[0] -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv 创建失败" }
}

if (-not $SkipInstall) {
    Write-Host "== 安装依赖" -ForegroundColor Cyan
    & $venvPy -m pip install --upgrade pip -q
    & $venvPy -m pip install -r requirements.txt --timeout 30
    if ($LASTEXITCODE -ne 0) {
        Write-Host "== 官方源失败，改用清华镜像" -ForegroundColor Yellow
        & $venvPy -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --timeout 30
        if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
    }
}

Write-Host "== 编译检查" -ForegroundColor Cyan
& $venvPy -m compileall -q main.py app
if ($LASTEXITCODE -ne 0) { throw "语法检查失败" }

Write-Host "== PyInstaller" -ForegroundColor Cyan
& $venvPy -m PyInstaller --noconfirm --clean fishdesk.spec
if ($LASTEXITCODE -ne 0) { throw "打包失败" }

$exe = Join-Path $here "dist\XiaoFeiYuConsole.exe"
if (Test-Path $exe) {
    $mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "== 完成：$exe（$mb MB）" -ForegroundColor Green
} else {
    throw "没有产出 $exe"
}
