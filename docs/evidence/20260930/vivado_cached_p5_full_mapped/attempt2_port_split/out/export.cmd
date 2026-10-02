@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "D:\Academic\Vivado2018\Vivado\2018.3\bin\vivado.bat" "-mode" "batch" "-nojournal" "-log" "export.vivado.log" "-source" "input/run_vivado_full_mapped.tcl" "-tclargs" "input/post_synth.dcp" "export" "xc7vx690tffg1761-2" "5"
exit /b %ERRORLEVEL%
