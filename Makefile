# D3/D4 验收入口。CI 与本地调用同一目标。
.PHONY: verify-d3d4
verify-d3d4:
	bash tools/verify_d3d4.sh
