@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "D:\Academic\Vivado2018\Vivado\2018.3\bin\vivado.bat" -mode batch -nojournal -log vivado.log -source synth/run_vivado_ooc.tcl -tclargs "xc7vx690tffg1761-2" out 1.5625 7
exit /b %ERRORLEVEL%
