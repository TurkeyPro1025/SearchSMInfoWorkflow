param(
    [ValidateSet("flow", "http", "node")]
    [string]$Mode = "flow",
    [string]$Node = "",
    [string]$InputJson = "",
    [int]$Port = 5000
)

$ErrorActionPreference = "Stop"

$utf8Encoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $utf8Encoding
[Console]::OutputEncoding = $utf8Encoding
$OutputEncoding = $utf8Encoding
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "未找到虚拟环境 Python: $PythonExe"
}

$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        $line = $_.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            return
        }

        $parts = $line.Split("=", 2)
        if ($parts.Count -eq 2) {
            [System.Environment]::SetEnvironmentVariable($parts[0], $parts[1])
        }
    }
}

$args = @("$ProjectRoot\src\main.py", "-m", $Mode)

if ($Mode -eq "http") {
    $args += @("-p", "$Port")
}

if ($Mode -eq "node") {
    if (-not $Node) {
        throw "node 模式必须传入 -Node"
    }
    $args += @("-n", $Node)
}

if ($InputJson) {
    $args += @("-i", $InputJson)
}

& $PythonExe @args
exit $LASTEXITCODE