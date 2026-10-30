$ErrorActionPreference = 'Stop'
$projectPath = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectPath
$pythonPath = Join-Path $projectPath '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建 Python 虚拟环境' }
}
& $pythonPath -m pip install -r requirements.txt -c requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
Write-Output '启动跟上了吗：http://localhost:8000；手机请使用本机局域网 IP。按 Ctrl+C 停止。'
& $pythonPath -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
