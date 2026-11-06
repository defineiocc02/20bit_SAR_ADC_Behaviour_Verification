# Use the same physical cohort for the integrated digital top.
include $(dir $(DESIGN_CONFIG))sky130_config.mk

export DESIGN_NAME = sar20_digital_top
export DESIGN_NICKNAME = task_integration_sar20
export VERILOG_FILES += $(REPO_DIR)/task3/rtl/caaz20_pair.sv \
                        $(REPO_DIR)/task_integration/rtl/sar20_digital_top.sv
