@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "D:\Academic\Vivado2018\Vivado\2018.3\bin\xvlog.bat" "--sv" "-d" "FULL_MAPPED_VENDOR_PORTS" "export/full_top_funcsim.v" "input/structural_mapped_tb.sv" "D:\Academic\Vivado2018\Vivado\2018.3\data\verilog\src\glbl.v"
exit /b %ERRORLEVEL%
