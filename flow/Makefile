# =============================================================================
# 文件名     : Makefile
# 功能       : 从仓库根启动当前 v21 回归及共享部件检查。
# 设计说明   : 根目录从本文件定位，允许从任意当前目录读取本清单。
# =============================================================================
REPO_ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/..)

.PHONY: help verify-v21 verify-d3d4 verify-sched18

help:
	@echo "make -f flow/Makefile verify-v21        v21 数字回归（VCS）"
	@echo "make -f flow/Makefile verify-d3d4       DEM 掩码与扰动预置（VCS）"
	@echo "make -f flow/Makefile verify-sched18    片组调度统计（Python，20000 拍）"
	@echo "远端 v21 回归：V21_RUNNER=<host> make -f flow/Makefile verify-v21"

verify-v21:
	cd "$(REPO_ROOT)" && bash tools/verify_v21.sh

verify-d3d4:
	cd "$(REPO_ROOT)" && bash tools/verify_d3d4.sh

# 调度统计只运行参考模型，RTL 定向检验与向量身份另由各回归验证。
verify-sched18:
	cd "$(REPO_ROOT)" && python3 tools/sched18_stats.py --cycles 20000
