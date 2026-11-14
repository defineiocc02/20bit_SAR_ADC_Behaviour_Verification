# VCS `source_control_contract_tb` 运行 trace

- 运行日期：2026-11-12
- 环境：虚拟机 `vm-meow`，VCS W-2024.09-SP1_Full64，`VCS_TARGET_ARCH=amd64`，`LM_LICENSE_FILE=27080@localhost`
- 命令：
```bash
vcs ... sim/tb/source_control_contract_tb.sv sim/tb/wrap_source_control_tb.sv \
  -top wrap_source_control_tb -o tb_simv
./tb_simv +weights=sim/fixtures/source_control_component/weights.hex \
  +scalars=sim/fixtures/source_control_component/scalars.hex \
  +trace_dir=/tmp/trace_out +frames=32
```
- 结果：`SOURCE_CONTROL_COMPONENT_COMPLETE observations=56 outputs=54 checks=368 epochs=2`
- 仿真时间：6.725 µs；CPU 0.17 s

## 边界

这是 **source_control_component 夹具**：`N_SLICES=18, N_ACTIVE=8, N_UNIT_MAIN=2, N_UNIT_SUB=1, ADC2_BITS=6, CONTROL_WORD_BITS=2, STATE_BITS=3`。
它不是 20b 全芯片，也不是 20b 整链 scoreboard；368 个 `require_ok` 只覆盖组件合同。
tb 自己打印 `SOURCE_CONTROL_ANALOG_MAPPING_TIMING_CAAZ_NOISE_NOT_VERIFIED`。
