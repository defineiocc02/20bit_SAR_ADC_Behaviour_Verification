$ErrorActionPreference='Stop'
Set-Location -LiteralPath 'C:/Users/Administrator/adc_rtl_vivado/run.fe9182c26b9b4a76a18a3ca0baff85ce'
& 'D:/Academic/Vivado2018/Vivado/2018.3/bin/vivado.bat' -mode batch -nojournal -log sta_regreg.log -source sta_regreg.tcl
exit $LASTEXITCODE
