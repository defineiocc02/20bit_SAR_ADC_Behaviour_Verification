@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "D:\Academic\Vivado2018\Vivado\2018.3\bin\xsim.bat" "full_mapped_p5" "-R" "-testplusarg" "mapped_trace=trace.csv" "-log" "xsim.log"
exit /b %ERRORLEVEL%
