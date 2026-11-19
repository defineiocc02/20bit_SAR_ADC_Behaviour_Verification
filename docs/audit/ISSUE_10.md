# Issue 10 复核：跨样本错码与交接沿片选重叠

复核对象为 GitHub 分支 `merge/adi-repro` 修复前的 `9e4f1fd`。它和报告者使用的 `ffc10ef` 具有逐字节相同的 `recon_v21.sv`、`acq_conv_ctx.sv`。下表中的前后结果由本地 Verilator 在数字引脚上重新测得。报告者另行使用了 ModelSim 和自己的 Verilog-2001 实现。[原始问题](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/issues/10)

| 报告事项 | 裁定与处理 |
| --- | --- |
| 合法负数使下一笔清零 | 成立。错误位和数值现使用同一笔的有符号 X；保留原始两笔作为连续回归的一部分。 |
| 释放、晋升和受理同沿时片选重叠 | 成立。受理沿前实测有七片同时接到采集和转换选通。现由同一份采集描述符产生片选、预置写门及数据。 |
| 历史 CAAZ 系统封装无法展开 | 对 `ffc10ef` 成立。比对其封装与子模块声明发现一个未声明参数和 13 个未声明端口。当前分支已移除这两个历史设计文件。 |

## 两笔定点重构

固定 ADC2 原码 2048、Q24 偏置 768、增益校正 1、G 为 32、Wp 为 0。Wm 从 15 变为 17。所有权重按 Q30 编码。标签 601 与 602 分别在第 6、17 拍受理，中间有十个空拍。各笔均在受理后第六拍输出。

| 标签 | 整数参考码 | 修复前实测码 / err | 修复后实测码 / err |
| --- | --- | --- | --- |
| 601 | `0x78000` | `0x78000 / 0` | `0x78000 / 0` |
| 602 | `0x88000` | `0x00000 / 1` | `0x88000 / 0` |

原 S3 同沿写入 `x3_q`，却对该寄存器的旧值做或归约。空拍保留上一笔 X，合法负数的高位又全是符号扩展的 1，于是正值标签 602 被错误清零。现由本笔乘积取出 X，判断高位是否等于第 31 位的符号扩展，并在同一沿锁存 X、错误和标签。现行回归含 8,209 笔冻结向量、八组共 21 笔逐标签事务、六个 S3 边界探针。[重构 RTL](../../rtl/v21/recon_v21.sv) · [逐笔期望与原始激励](../../tools/recon_oracle.py)

## 物理采集与转换片权

A 正在转换，B 已采样，C 等待受理。A 的捕获、hold 和安静窗均已满足时，控制器可以在下一个上升沿同时释放 A、提升 B、接收 C。表中每一位对应一个物理片，位 0 为片 0。

| 观测时刻 | ACQ | CONV | 采样写门 | ACQ ∩ CONV |
| --- | --- | --- | --- | --- |
| 修复前，交接沿前 | `040FE`（C） | `000FF`（A） | `040FE` | `000FE`，片 1–7 |
| 修复后，交接沿前 | `3BD00`（B） | `000FF`（A） | `3BD00` | `00000` |
| 修复后，交接沿后 | `040FE`（C） | `3BD00`（B） | `040FE` | `00000` |

修复前，组合信号 `do_prepare` 先把片选和采样写门切到 C，而转换描述符仍持有 A。修复后，CONV 有主时一直使用冻结的 ACQ 描述符驱动宏接口；在受理上升沿更新描述符后，新片选、写门和四位预置一起生效。CONV 本来就空闲时仍允许提前预置。测试让 B 与 C 实际驱动不同的四位值，同时检查所有 72 根预置数据线。[片权与预置路由](../../rtl/v21/acq_conv_ctx.sv) · [沿前沿后接口测试](../../tb/v21/tb_analog_if.sv)

**复核中另见。** 旧实现排空后，采集描述符无效却仍把候选掩码 `39701` 送到采集片选。现有区分冷启动的状态位。首次受理前仍是八片预选、写门为零；停收排空后，空 ACQ 选通和写门都为零，恢复接收也要等到下一笔准备请求。加强后的用例在旧 RTL 上 124 项中有 10 项失败，在现行 RTL 上 124 项全部通过。连续采样的十二个场景另在每个上升沿前后核对片组互斥和写门归属。[生产顶层连续采样测试](../../tb/v21/sample_cadence.cpp)

这些测量覆盖已稳定的数字引脚在时钟沿两侧的取值。实际模拟开关的非重叠时间、驱动延迟与建立时间，要在宏接口和物理时序验收时测定。

## 旧封装为何无需改回当前工程

`ffc10ef` 的 `sar20_system_top` 实例化了 `sar20_digital_core`，却传入该模块声明中不存在的 `P_CAAZ_ENABLE` 及 13 个 CAAZ 端口。`9e4f1fd` 和现行生产清单都不含这两个设计文件。讨论区给出的历史修复计数属于报告者的运行。这里以当时的源码核对接口，以当前生产清单确定影响范围。[历史封装](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/ffc10eff9adfdd3321b1d018a281a0a28b69ec47/subsys/ad4080_dsp/rtl/sar20_system_top.sv) · [历史模块声明](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/ffc10eff9adfdd3321b1d018a281a0a28b69ec47/rtl/top/sar20_digital_core.sv) · [现行生产清单](../../flow/build/filelists/files_adc_v21.f)

## 复现

在仓库根使用 Verilator 与 C++ 编译器。第一条运行保存重构的逐拍 CSV，第二项测试台在 run.log 中输出交接沿前后的实际路由。各用例也会独立检查错误位、数据与计数。

~~~bash
work="$(mktemp -d)"
python3 -B tools/run_open_rtl.py --simulator verilator \
  --tops tb_recon_stream tb_analog_if v21_sample_cadence tb_i4_recon --out "$work/results"
rg '^ROUTE (handover|only_conv|drained|resume_idle)|^SUMMARY' "$work/results/tb_analog_if/run.log"
python3 - "$work/results/tb_recon_stream/report.json" <<'PY'
import json, sys
case = json.load(open(sys.argv[1]))["cases"]["issue10_ten_idle_cycles"]
for tx in case["transactions"]:
    out = tx["output"]
    print(tx["tag"], tx["accept_tick"], out["tick"], hex(out["out_code"]), out["out_err"])
PY
~~~

复核旧行为可在库外重建修复前的生产源，保留现行观测工具。旧顶层缺少后来新增的三个 SADC/测试控制引脚，下面只在临时测试台去掉该行连接，原有字输入保持相同。

~~~bash
old="$(mktemp -d)"
git archive HEAD | tar -x -C "$old"
git archive 9e4f1fd rtl | tar -x -C "$old"
python3 - "$old/tb/v21/tb_analog_if.sv" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
lines = p.read_text().splitlines(keepends=True)
assert sum(".dither_force_i(" in line for line in lines) == 1
p.write_text("".join(line for line in lines if ".dither_force_i(" not in line))
PY
for bench in tb_analog_if tb_recon_stream; do
  if python3 -B "$old/tools/run_open_rtl.py" --simulator verilator \
      --tops "$bench" --out "$old-result-$bench"; then
    echo "预期负对照失败，请核查旧 RTL 与激励" >&2
    exit 1
  fi
done
rg '^ROUTE handover_before|^SUMMARY' "$old-result-tb_analog_if/tb_analog_if/run.log"
python3 - "$old-result-tb_recon_stream/tb_recon_stream/trace.csv" <<'PY'
import csv, sys
with open(sys.argv[1]) as f:
    for row in csv.DictReader(f):
        if row["scenario"] == "8" and row["out_valid"] == "1":
            print(row["out_tag"], hex(int(row["out_code"])), row["out_err"])
PY
git ls-tree -r ffc10ef -- rtl/top/sar20_digital_core.sv subsys/ad4080_dsp/rtl/sar20_system_top.sv
git ls-tree -r HEAD -- rtl/top/sar20_digital_core.sv subsys/ad4080_dsp/rtl/sar20_system_top.sv
~~~
