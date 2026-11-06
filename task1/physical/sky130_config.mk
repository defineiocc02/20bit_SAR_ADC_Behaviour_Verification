# OpenROAD-flow-scripts configuration for the public SkyWater SKY130HD flow.
export DESIGN_NAME = module1_top
export DESIGN_NICKNAME = task1_module1
export PLATFORM = sky130hd

TASK1_DIR := $(abspath $(dir $(DESIGN_CONFIG))/..)
export VERILOG_FILES = $(TASK1_DIR)/rtl/slice_scheduler.sv \
                       $(TASK1_DIR)/rtl/fig11_dem.sv \
                       $(TASK1_DIR)/rtl/module1_top.sv
export SDC_FILE = $(TASK1_DIR)/physical/sky130_constraint.sdc
export DIE_AREA = 0 0 450 450
export CORE_AREA = 20 20 430 430
export PLACE_DENSITY = 0.42
export TNS_END_PERCENT = 100
export NUM_CORES = 4
