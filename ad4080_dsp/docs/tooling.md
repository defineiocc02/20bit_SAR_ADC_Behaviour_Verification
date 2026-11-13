# 显式工具入口与验证层级

本页给出实际脚本入口、显式输入及证据范围。旧2026-10-19工具选型、下载问题及版本数保留于[9bdf记录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/ad4080_dsp/docs/tooling.md)，不能用它判断当前宿主能力。

## 语法、文本与文件头

```bash
# 必须明确给文件和依赖；PYTHON指向已有pyslang的解释器
STRICT=1 bash ad4080_dsp/scripts/lint.sh ad4080_dsp/rtl/sinc1_decim.sv
python ad4080_dsp/scripts/sv_lint.py --strict ad4080_dsp/rtl/sinc1_decim.sv
python ad4080_dsp/scripts/check_headers.py ad4080_dsp/rtl/sinc1_decim.sv
```

| 入口 | 实际检查 | 不代表什么 |
| :--- | :--- | :--- |
| sv_lint.py | 显式文件的pyslang解析／elaboration及文本规则；--strict将警告计为失败 | 不代表功能、CDC、综合或时序通过 |
| check_headers.py | 所选文件头字段 | 不验证来源真实性、接口数值或作者归属 |
| lint.sh | 上述检查加可选Verible；无参数退出2 | 可选工具NOT_RUN不计已执行；不隐式扫描RTL或补参数包 |

可用`PYTHON`／`VERIBLE_BIN`选择已配置工具；不能从旧主机路径或历史工具表推断当前环境。工具缺失、输入不可读或未满足依赖必须可见，禁止降级为打印PASS。

## 编译与仿真

```bash
python tools/run_open_rtl.py --tops result_fifo_tb --out ../sar-output/fifo-check-001
# 完整核组件另需 --inputs-dir；数值／向量组件另需对应format或数据
```

`--tops`与库外新`--out`必填；`--simulator`选择已配置的verilator／vcs／iverilog。`VERILATOR`、`VCS`、`IVERILOG`／`VVP`按实际runner支持选择命令。每个已登记组件只编译明确依赖，并检查完成marker、正计数／零错误项与必要OPEN说明。

`ad4080_dsp/sim/run_all.sh`转发同一显式runner，不是默认全库套件；`run_top.sh`只选ad4080_top_tb，其余输入仍须提供。未登记、未绑定、编译失败或没有有效完成证据都不算通过；没有旧golden／二进制自动回退。

## 复现指定的14个场景

实际system场景使用2+1单元、6位ADC2的[数学夹具](../../sim/fixtures/source_control_component/fixture_manifest.json)。权重、标量和coarse／fine目标属于夹具；两采集端口的合成偏移不作为真实Fig.19电荷网络。控制文件按状态0..6、word0..3的有序笛卡尔积生成，每行十六进制。

```bash
SAR_DSP_ROOT="$(mktemp -d)"
SAR_DSP_FIXTURE=sim/fixtures/source_control_component
PYTHONPATH=src python tools/export_rtl_params.py \
  --config "$SAR_DSP_FIXTURE/config.json" --format "$SAR_DSP_FIXTURE/format.json" \
  --binding "$SAR_DSP_FIXTURE/binding.json" --out "$SAR_DSP_ROOT/inputs"

python - "$SAR_DSP_FIXTURE" "$SAR_DSP_ROOT/controls.hex" <<'PY'
import json
import sys
from pathlib import Path

fixture, output = map(Path, sys.argv[1:])
stream = json.loads((fixture / "fixture_manifest.json").read_text())["recommended_stream"]
word_bits = json.loads((fixture / "binding.json").read_text())["control_word_bits"]
with output.open("x", encoding="ascii") as handle:
    for state in stream["legal_state_values"]:
        for word in stream["control_word_values"]:
            handle.write(f"{word | (state << word_bits):x}\n")
PY

python tools/run_open_rtl.py --out "$SAR_DSP_ROOT/run" --tops \
  result_fifo_tb cnv_ctrl_contract_tb sys_error_corr_contract_tb sinc5_decim_tb \
  spi_slave_tb fifo_capture_contract_tb fifo_capture_16k ad4080_top_tb \
  sinc5_comp_contract_tb sar20_dsp_integration_tb ad4080_regs_rejection_tb \
  spi_unbound_contract_tb sinc_rate_oracle_tb sar20_system_top_tb \
  --inputs-dir "$SAR_DSP_ROOT/inputs" --weights "$SAR_DSP_FIXTURE/weights.hex" \
  --scalars "$SAR_DSP_FIXTURE/scalars.hex" --controls "$SAR_DSP_ROOT/controls.hex" \
  --coarse-target 173 --fine-target 32
```

14场景含FIFO同一bench的两个深度。普通runner的所选lint与handoff003的5个严格`-Wall` profile分开；不能用此命令宣称已执行全部严格profile。真实system比较以SAR输出整数码为参考，核码制、握手、故障锁停及恢复，不是模拟精度实验。

## 当前发布需要的记录

| 记录 | 必须绑定的身份 |
| :--- | :--- |
| 源／输入 | 精确SHA、Config／format／binding、两只source模块和packet schema |
| 工具与执行 | 实际命令、版本、编译／运行日志、完成marker及非空计数 |
| 数字结果 | 码、ID、flags、拒绝／恢复、独立参考及工作域；不能只报用例存在 |
| 工艺层级 | 顶层、库／器件、SDC、角、阶段与原始STA；没有则明确未执行 |

[旧仿真](verification_sim.md)、[BMC](formal.md)、[ECP5](synthesis_ecp5.md)和手算面积／延时继续按原版本引用。RTLCoder、Verible下载或pyslang旧故事不作为当前门禁成绩；自动后处理工具也不能据模板编造来源、作者、版本或当前能力。当前结果按[B表](../../docs/archive/digital_status_matrix.md)的实际场景及证据范围引用。
