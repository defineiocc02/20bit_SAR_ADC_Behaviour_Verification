# 显式工具入口与验证层级

本页给出实际脚本入口、显式输入及证据范围。旧2026-10-19工具选型、下载问题及版本数保留于[9bdf记录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/ad4080_dsp/docs/tooling.md)，不能用它判断当前宿主能力。

## 语法、文本与文件头

```bash
# 必须明确给文件和依赖；PYTHON指向已有pyslang的解释器
STRICT=1 bash subsys/ad4080_dsp/scripts/lint.sh subsys/ad4080_dsp/rtl/sinc1_decim.sv
python subsys/ad4080_dsp/scripts/sv_lint.py --strict subsys/ad4080_dsp/rtl/sinc1_decim.sv
python subsys/ad4080_dsp/scripts/check_headers.py subsys/ad4080_dsp/rtl/sinc1_decim.sv
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
# 其他已登记测试台及其输入可查 runner --help。
```

`--tops`与库外新`--out`必填；`--simulator`选择已配置的verilator／vcs／iverilog。`VERILATOR`、`VCS`、`IVERILOG`／`VVP`按实际runner支持选择命令。每个已登记组件只编译明确依赖，并检查完成marker、正计数／零错误项与必要OPEN说明。

`subsys/ad4080_dsp/sim/run_all.sh`转发同一显式runner，不是默认全库套件；`run_top.sh`只选ad4080_top_tb，其余输入仍须提供。未登记、未绑定、编译失败或没有有效完成证据都不算通过；没有旧golden／二进制自动回退。

## 验证 DSP 数字输入

`sar20_dsp_integration_tb` 直接给适配层输入 20 位码、样本 ID 和故障信号，用于检查码制、输入握手、故障锁停和恢复。可与 FIFO、寄存器和滤波检查同批运行，证据对应各自选定的组件。

~~~bash
DSP_RUN_DIR=$(mktemp -d)
python tools/run_open_rtl.py --out "$DSP_RUN_DIR/components" --tops \
  result_fifo_tb ad4080_top_tb sinc_rate_oracle_tb sar20_dsp_integration_tb
~~~

handoff003 的原 14 场景与 5 个 lint profile 是当时的归档结果。整机试验的原命令和合成输入见 `git show 674b0d8:subsys/ad4080_dsp/docs/tooling.md`。

## 当前发布需要的记录

| 记录 | 必须绑定的身份 |
| :--- | :--- |
| 源／输入 | 精确 SHA、数字输入及启用的配置参数 |
| 工具与执行 | 实际命令、版本、编译／运行日志、完成marker及非空计数 |
| 数字结果 | 码、ID、flags、拒绝／恢复、独立参考及工作域；不能只报用例存在 |
| 工艺层级 | 顶层、库／器件、SDC、角、阶段与原始STA；没有则明确未执行 |

[旧仿真](verification_sim.md)、[BMC](formal.md)、[ECP5](synthesis_ecp5.md)和手算面积／延时继续按原版本引用。RTLCoder、Verible下载或pyslang旧故事不作为当前门禁成绩；自动后处理工具也不能据模板编造来源、作者、版本或当前能力。handoff003 的场景、源身份及证据边界见[归档 B 表](../../../docs/_history/archive/digital_status_matrix.md)。
