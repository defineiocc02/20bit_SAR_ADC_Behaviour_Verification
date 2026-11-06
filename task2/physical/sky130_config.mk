# Eight-slice digital control on the public SKY130HD process.
export DESIGN_NAME = dither8_top
export DESIGN_NICKNAME = task2_dither8
export PLATFORM = sky130hd

REPO_DIR := $(abspath $(dir $(DESIGN_CONFIG))/../..)
export VERILOG_FILES = $(REPO_DIR)/task1/rtl/slice_scheduler.sv \
                       $(REPO_DIR)/task1/rtl/fig11_dem.sv \
                       $(REPO_DIR)/task1/rtl/module1_top.sv \
                       $(REPO_DIR)/task1/rtl/fig21_bridge.sv \
                       $(REPO_DIR)/task1/rtl/module1_bridge_top.sv \
                       $(REPO_DIR)/task2/rtl/dither_word_gen.sv \
                       $(REPO_DIR)/task2/eight_rtl/rtl/dither8_top.sv
export SDC_FILE = $(REPO_DIR)/task1/physical/sky130_constraint.sdc
export DIE_AREA = 0 0 450 450
export CORE_AREA = 20 20 430 430
export PLACE_DENSITY = 0.42
export TNS_END_PERCENT = 100
export NUM_CORES = 4
export SETUP_SLACK_MARGIN = 0.15
