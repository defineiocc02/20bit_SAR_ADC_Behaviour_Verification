$ProgressPreference = 'SilentlyContinue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ErrorActionPreference = 'Stop'
$run = 'C:/Users/Administrator/adc_rtl_vivado/run.9dd6a40dd22c47279f99d03d25ab1d76'
try {
    Set-Location -LiteralPath $run
    & tar.exe -xzf source.tar.gz
    if ($LASTEXITCODE -ne 0) { throw 'tar.exe extraction failed' }
    $batchBytes = [Convert]::FromBase64String('QGVjaG8gb2ZmDQpzZXRsb2NhbCBEaXNhYmxlRGVsYXllZEV4cGFuc2lvbg0KY2hjcCA2NTAwMSA+bnVsDQpjYWxsICJDOi9Vc2Vycy9BZG1pbmlzdHJhdG9yL21pbmljb25kYTMvcHl0aG9uLmV4ZSIgInN5bnRoL3J1bl92aXZhZG9fZnVsbF9tYXBwZWQucHkiICItLWRjcCIgIkM6L1VzZXJzL0FkbWluaXN0cmF0b3IvYWRjX3J0bF92aXZhZG8vcnVuLmNlNDk1NGZiYjczYzQxODg5YmE1NGI2Y2M3NjlmOGZlL291dC9wb3N0X3N5bnRoLmRjcCIgIi0tc3ludGhlc2lzLW1hbmlmZXN0IiAic3ludGhlc2lzX21hbmlmZXN0Lmpzb24iICItLWRjcC1zaGEyNTYiICI2M2E2ZmFhY2RjNDRlNTRkYTAyNTE0YTcwZmY2NThhYzMxNzU2ODUzNDk2MTE0NTEzMDE4YWM1NzE0Y2M4NTZlIiAiLS1zdGFnZXMiICI1IiAiLS12aXZhZG8iICJEOi9BY2FkZW1pYy9WaXZhZG8yMDE4L1ZpdmFkby8yMDE4LjMvYmluL3ZpdmFkby5iYXQiICItLW91dCIgIm91dCIgIi0tdGltZW91dCIgIjM2MDAiICItLWV4ZWN1dGUiDQpleGl0IC9iICVFUlJPUkxFVkVMJQ0K')
    [IO.File]::WriteAllBytes((Join-Path $run 'run_vivado.cmd'), $batchBytes)
    $process = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /v:off /c run_vivado.cmd' -WorkingDirectory $run -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $run 'console.log') -RedirectStandardError (Join-Path $run 'console.stderr.log')
    $null = $process.Handle
    if (-not $process.WaitForExit(7200000)) {
        if (-not $process.HasExited) {
            & taskkill.exe /PID $process.Id /T /F
            if ($LASTEXITCODE -ne 0) { throw 'Failed to terminate timed-out Vivado process tree' }
        }
        if (-not $process.WaitForExit(30000)) { throw 'Vivado process did not terminate' }
        [IO.File]::WriteAllText((Join-Path $run 'vivado_exit_code.txt'), '124')
        exit 124
    }
    $process.WaitForExit()
    $code = $process.ExitCode
    if ($null -eq $code) { throw 'Vivado process exit code unavailable' }
    [IO.File]::WriteAllText((Join-Path $run 'vivado_exit_code.txt'), [string]$code)
    exit $code
} catch {
    [IO.File]::WriteAllText((Join-Path $run 'runner_failure.txt'), $_.ToString())
    [Console]::Error.WriteLine($_.ToString())
    exit 1
}
