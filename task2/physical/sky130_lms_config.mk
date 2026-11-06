# Full digital top with loaded frozen-weight arithmetic.
include $(dir $(DESIGN_CONFIG))sky130_integrated_config.mk

export DESIGN_NAME = sar20_digital_lms_top
export DESIGN_NICKNAME = task_integration_sar20_lms
export VERILOG_FILES += $(REPO_DIR)/task_reconstruction/rtl/sar20_lms_recon.sv \
                        $(REPO_DIR)/task_integration/rtl/sar20_digital_lms_top.sv

# Size for coefficient storage and arithmetic after actual synthesis.
export DIE_AREA =
export CORE_AREA =
export CORE_UTILIZATION = 33
export CORE_ASPECT_RATIO = 1
export CORE_MARGIN = 20
