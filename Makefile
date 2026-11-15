# 验收与统计入口。CI 与本地调用同一目标。
#
# 注意：这些目标都**不允许**在失败时静默通过。
# 每一层内部都是 "必须出现 RESULT: PASS 才继续"，否则非零退出。

.PHONY: help verify-d3d4 verify-d3d4-ip verify-sched18 vec

help:
	@echo "make verify-d3d4        叶子级对拍（133,376 + 512 组）"
	@echo "make verify-d3d4-ip     三层验收：叶子 / IP / sched18（+门级，若给网表）"
	@echo "make verify-sched18     sched18 随机性统计（20000 拍）"
	@echo "make vec                重新生成全部金标向量"
	@echo ""
	@echo "远端 VCS："
	@echo "  D3D4_RUNNER=vm-yian make verify-d3d4-ip"
	@echo "含门级："
	@echo "  D3D4_RUNNER=vm-yian D3D4_NETLIST=<net.v> D3D4_SCMODEL=<std.v> make verify-d3d4-ip"
	@echo ""
	@echo "综合见 docs/SYNTH_D3D4.md（需 DC + PDK，不在 make 内自动执行）"

verify-d3d4:
	bash tools/verify_d3d4.sh

verify-d3d4-ip:
	bash tools/verify_d3d4_ip.sh

verify-sched18:
	python3 tools/sched18_stats.py --cycles 20000

# 金标必须能重新生成，否则后面的 PASS 可能是拿旧向量自证。
vec:
	python3 tools/gen_vec_phase.py
	python3 tools/ref_d3d4_ip.py
