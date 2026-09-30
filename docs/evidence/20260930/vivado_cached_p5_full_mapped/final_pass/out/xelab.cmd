@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
call "D:\Academic\Vivado2018\Vivado\2018.3\bin\xelab.bat" "structural_mapped_tb" "glbl" "-L" "unisims_ver" "-s" "full_mapped_p5" "-mt" "2" "-generic_top" "P_RECON_STAGES=5"
exit /b %ERRORLEVEL%
