$ProgressPreference = 'SilentlyContinue'
$utf8 = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$ErrorActionPreference = 'Stop'
$run = 'C:/Users/Administrator/adc_rtl_vivado/run.ce4954fbb73c41889ba54b6cc769f8fe'
try {
    Set-Location -LiteralPath $run
    & tar.exe -xzf source.tar.gz
    if ($LASTEXITCODE -ne 0) { throw 'tar.exe extraction failed' }
    $batchBytes = [Convert]::FromBase64String('QGVjaG8gb2ZmDQpzZXRsb2NhbCBEaXNhYmxlRGVsYXllZEV4cGFuc2lvbg0KY2hjcCA2NTAwMSA+bnVsDQpjYWxsICJEOlxBY2FkZW1pY1xWaXZhZG8yMDE4XFZpdmFkb1wyMDE4LjNcYmluXHZpdmFkby5iYXQiIC1tb2RlIGJhdGNoIC1ub2pvdXJuYWwgLWxvZyB2aXZhZG8ubG9nIC1zb3VyY2Ugc3ludGgvcnVuX3ZpdmFkb19vb2MudGNsIC10Y2xhcmdzICJ4Yzd2eDY5MHRmZmcxNzYxLTIiIG91dCAyNS4wIDUNCmV4aXQgL2IgJUVSUk9STEVWRUwlDQo=')
    [IO.File]::WriteAllBytes((Join-Path $run 'run_vivado.cmd'), $batchBytes)
    $process = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /v:off /c run_vivado.cmd' -WorkingDirectory $run -NoNewWindow -PassThru -RedirectStandardOutput (Join-Path $run 'console.log') -RedirectStandardError (Join-Path $run 'console.stderr.log')
    $null = $process.Handle
    if (-not $process.WaitForExit(14400000)) {
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
