# =============================================================================
# 文件名     : Makefile
# 功能       : 从仓库根目录进入同一组数字设计与证据检查。
# 设计说明   : 工具由运行主机提供。工件写入工作树外的新目录。
# =============================================================================

PYTHON ?= python3
RUN_ROOT ?= $(shell mktemp -d "/tmp/sar-digital.XXXXXX")
# 同一次 make 并行运行多个目标时共享一个收据根，子目录由各目标独占。
RUN_ROOT := $(abspath $(RUN_ROOT))
PERIPH := rtl/ad4080_periph
CAL := $(PERIPH)/calibration

.PHONY: lint sim-core sim-periph sim-top cal audit

lint:
	$(PYTHON) tools/verify_rtl_quality.py --synth --out "$(RUN_ROOT)/strict"

sim-core:
	mkdir -p "$(RUN_ROOT)"
	V21_WORKDIR="$(RUN_ROOT)/core" bash tools/verify_v21.sh

sim-periph:
	mkdir -p "$(RUN_ROOT)"
	$(PYTHON) $(PERIPH)/verify/run_peripheral_audit.py --repo . --work-parent "$(RUN_ROOT)" --receipt "$(RUN_ROOT)/peripheral.json"

sim-top:
	$(PYTHON) tools/verify_rtl_quality.py --sim-top --out "$(RUN_ROOT)/chip"

cal:
	mkdir -p "$(RUN_ROOT)"
	$(PYTHON) $(CAL)/verify_rtl.py --repo . --out "$(RUN_ROOT)/ad4080-system-calibration.json" --require-manual-order
	$(PYTHON) $(PERIPH)/interfaces/dsp_review/verify_filter_response.py --repo . --output "$(RUN_ROOT)/filter-response"
	DSP_UPSTREAM_REPO="$(CURDIR)" AD4080_PRODUCT_WORK_PARENT="$(RUN_ROOT)" bash $(PERIPH)/integration/product/run_product.sh --calibration

audit:
	$(PYTHON) tools/check_doc_counts.py --check
	$(PYTHON) tools/check_engineering.py
	node tools/v21_algorithm_atlas.mjs --check
	node $(PERIPH)/verify/check_manual_trace.mjs --check-local
	$(PYTHON) $(PERIPH)/verify/check_delivery.py
