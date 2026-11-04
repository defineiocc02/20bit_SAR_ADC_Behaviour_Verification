
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$dcp='C:/Users/Administrator/adc_rtl_vivado/run.86ebbd7c9da34a33b49af64888b4797e/out/post_route.dcp'
$expected='fba86312b0c96add78ca388b972a52ab77c1b781eec34c1b91027b1ea7358072'
if(!(Test-Path $dcp)){throw 'Frozen routed checkpoint missing'}
$before=(Get-FileHash $dcp -Algorithm SHA256).Hash.ToLowerInvariant()
if($before -ne $expected){throw 'Frozen routed checkpoint hash mismatch'}
$work='C:/Users/Administrator/adc_rtl_vivado/analysis.20261007.753387dfc10a4c9ca8f2f917fce3d5ec'
$script=Join-Path $work 'reopen_route.tcl'

$scriptHash=(Get-FileHash $script -Algorithm SHA256).Hash.ToLowerInvariant()
if($scriptHash -ne 'f17e4d60c111a991672f059f36728f959e8474b1d13034abbd251341a83bc28b'){throw 'Tcl changed in transfer'}
& 'D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat' -mode batch -nojournal -log (Join-Path $work 'vivado.log') -source $script -tclargs $dcp (Join-Path $work 'out') *> (Join-Path $work 'console.log')
$code=$LASTEXITCODE
$after=(Get-FileHash $dcp -Algorithm SHA256).Hash.ToLowerInvariant()
if($after -ne $before){throw 'Frozen DCP changed during reporting'}
$record=@{work=$work;dcp=$dcp;before_sha256=$before;after_sha256=$after;script_sha256=$scriptHash;vivado_raw_exit_code=$code;scope='re-export frozen P5 DCP; no synthesis/placement/routing/constraint edits'}
$record | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $work 'execution.json')
Write-Output ('REMOTE_REPORT_DIRECTORY='+$work.Replace('\','/'))
Write-Output ('VIVADO_RAW_EXIT_CODE='+$code)
if($code -ne 0){Get-Content (Join-Path $work 'console.log') -Tail 25; exit $code}
