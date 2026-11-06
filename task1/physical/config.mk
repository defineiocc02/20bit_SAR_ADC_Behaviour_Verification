# OpenROAD-flow-scripts design configuration for the public Nangate45 proxy.
export DESIGN_NAME = module1_top
export DESIGN_NICKNAME = task1_module1
export PLATFORM = nangate45

TASK1_DIR := $(abspath $(dir $(DESIGN_CONFIG))/..)
export VERILOG_FILES = $(TASK1_DIR)/rtl/slice_scheduler.sv \
                       $(TASK1_DIR)/rtl/fig11_dem.sv \
                       $(TASK1_DIR)/rtl/module1_top.sv
export SDC_FILE = $(TASK1_DIR)/physical/constraint.sdc
export DIE_AREA = 0 0 220 220
export CORE_AREA = 10 10 210 210
export PLACE_DENSITY = 0.35
export NUM_CORES = 4
