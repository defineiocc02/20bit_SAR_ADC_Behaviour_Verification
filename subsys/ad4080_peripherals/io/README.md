# DATA INTERFACE / CLK / Pad 工作包

| 交付 | 对应内容 |
| --- | --- |
| [IO_PLAN.md](IO_PLAN.md) | 芯片方位、Pad 组序和电气域、物理端口分组、跨域、DFT、目标引脚冻结条件 |
| [package_pins.csv](package_pins.csv) | AD4080 参考封装全部 49 球、电源域、逻辑复用及目标规划分组 |
| [SPEC.md](SPEC.md)、[microarchitecture.md](microarchitecture.md) | 本边沿字 RTL 合同、商用品 LVDS/两套 SPI/测试模式的证据与冲突处理、系统接收边界 |
| [ad4080_ddr_edge_words.sv](ad4080_ddr_edge_words.sv) | 20 位字到连续 LVDS 单/双 lane 装载数据的可综合映射 |
| [ad4080_ddr_edge_words_tb.sv](ad4080_ddr_edge_words_tb.sv)、[prove_edge_words.ys](prove_edge_words.ys) | 直接仿真与通用综合网表 SAT 证明的入口 |
| [FIFO_RECORD.md](FIFO_RECORD.md)、[ad4080_fifo_record_framer.sv](ad4080_fifo_record_framer.sv) | FIFO 固定头和字数。单 LVDS 复用 transport 并绑定外部 CRC24，默认 SPI 遵循 ADI 驱动结束的前缀 |
| [evidence](evidence)、[handoff_manifest.json](handoff_manifest.json) | 实际运行结果、目标边界静态核对、原件与交付文件散列 |

## 已完成的验证

- **Verilator 5.30**。`--timing -Wall` lint 和 Clang/C++20 `--binary` build 均退出 0。仿真通过 1109 次独立字重组、槽数和清零检查，另核对单双 lane 的两个手算固定码边沿向量。向量包括 80 条定向 walking-one/zero、1024 条可重复随机输入与正常/检查模式的组合，以及 5 条边界状态和检查退出关键字。[Verilator 日志](evidence/verilator.txt)
- **YoWASP Yosys 0.69**。`synth -noabc` 得到 50 个通用组合门，20 个 AND、29 个 MUX、1 个 NOT，`check -assert` 无报错，结构选择确认无 ff、latch、SR 和 memory。对通用综合网表的独立解码性质完成 SAT 证明，22 个自由输入位的所有二态取值均由该性质覆盖。[Yosys 日志](evidence/yosys.txt)
- **静态核对**。49 个参考封装球位按 Rev. B 图 2/表 6 核对。DIO、CLK、Pad 环和 IO_EAST 与目标分区坐标一致。包脚和草案组序在物理冻结前仍由封装/Pad 宏确定实际绑定。[坐标、包脚与注释率](evidence/static-audit.txt)
- **FIFO 记录**。原始时钟累计默认 profile 98,611 次位组检查，带约定 SPI 外部 CRC 尾 329 次，两种配置各完成 21 个故障检查。包含单/双字 LVDS 26/36 拍、单/四线 SPI 双字前缀 56/20 拍、16,384 字四线 SPI 连续 98,312 拍。同一记录的每个原始时钟沿均独立核对，载荷断供或晚尾不会报完成。两种通用 Yosys 综合为 1089/1064 个单元，各 97 个触发器、零锁存。[Verilator 日志](evidence/fifo-record-verilator.txt) · [Yosys 日志](evidence/fifo-record-yosys.txt) · [真实 SRAM 逐拍记录](../integration/evidence/raw_record.csv)

组合映射可独立在 DIO 使用。FIFO framer 的样本、时序、CRC 绑定和错误边界见 [FIFO 记录合同](FIFO_RECORD.md)。目标宏绑定、串化、跨域、两沿 STA、控制器兼容、包板链路和全吞吐的物理门禁见 [IO_PLAN.md](IO_PLAN.md)。

## 从本目录复跑

将 `VERILATOR`、`YOSYS` 设为可执行入口，或使用 PATH 同名命令。若 Verilator 以发行包提供，将其运行时根、二进制与 Python 工具依该发行包配置好。源码复制到 ASCII 临时目录，避免生成的构建命令误处理原工作目录。命令将仿真构建放到全新的 `--Mdir`。

```sh
(
    set -eu
    io_build=$(mktemp -d /tmp/ad4080-ddr.XXXXXX)
    cp ad4080_ddr_edge_words*.sv prove_edge_words.ys "$io_build/"
    cd "$io_build"
    "${VERILATOR:-verilator}" --lint-only --timing -Wall \
        --top-module ad4080_ddr_edge_words_tb \
        ad4080_ddr_edge_words.sv ad4080_ddr_edge_words_tb.sv
    "${VERILATOR:-verilator}" --compiler clang -CFLAGS '-std=c++20' \
        --binary --timing -Wall --top-module ad4080_ddr_edge_words_tb \
        --Mdir "$io_build/obj" ad4080_ddr_edge_words.sv ad4080_ddr_edge_words_tb.sv
    "$io_build/obj/Vad4080_ddr_edge_words_tb" > verilator-run.log
    rg -F 'PASS IO edge words: 1109 frame-word checks' verilator-run.log
    "${YOSYS:-yosys}" -Q -s prove_edge_words.ys > yosys.log
    rg -F 'SAT proof finished - no model found: SUCCESS!' yosys.log
    rg -F 'End of script.' yosys.log
)
```

纯逻辑流采用 `-noabc`。本机 WASM 工具实例尝试带 ABC 的默认 `synth` 时在调用 ABC 的中途退出，命令退出码为 0。复跑按上面的证明成功和脚本终止记录验收。Verilator 归档构建日志用 `<VERILATOR_ROOT>` 替代本机工具绝对路径。

### FIFO 记录两个配置复跑

```sh
(
    set -eu
    fifo_build=$(mktemp -d /tmp/ad4080-record.XXXXXX)
    cp ad4080_fifo_record_framer*.sv "$fifo_build/"
    cd "$fifo_build"
    for tail in 0 1; do
        "${VERILATOR:-verilator}" --compiler clang -CFLAGS '-std=c++20' \
            --binary --timing -Wall --output-split 0 -MAKEFLAGS VM_PARALLEL_BUILDS=0 \
            --top-module ad4080_fifo_record_framer_tb "-GBIND_SPI_TAIL=1'b$tail" \
            --Mdir "$fifo_build/obj-$tail" ad4080_fifo_record_framer.sv ad4080_fifo_record_framer_tb.sv
        "$fifo_build/obj-$tail/Vad4080_fifo_record_framer_tb" > "run-$tail.log"
        rg -F "PASS FIFO record SPI_TAIL=$tail" "run-$tail.log"
    done
    "${YOSYS:-yosys}" -Q -p 'read_verilog -sv ad4080_fifo_record_framer.sv; synth -noabc -top ad4080_fifo_record_framer; check -assert; select -assert-none t:$*latch* t:$_*LATCH*; stat' > synth.log
    rg -F 'End of script.' synth.log
    "${YOSYS:-yosys}" -Q -p 'read_verilog -sv ad4080_fifo_record_framer.sv; chparam -set SPI_CRC_TAIL_BOUND 1 ad4080_fifo_record_framer; synth -noabc -top ad4080_fifo_record_framer; check -assert; select -assert-none t:$*latch* t:$_*LATCH*; stat' > synth-bound.log
    rg -F 'End of script.' synth-bound.log
)
```

这版 Verilator 发行包的并行预编译头与本机 Clang 组合会缺 `.pch`，因此在上述复跑命令禁用输出拆分和并行预编译头。RTL 与工具发行包均使用原件本地入口。
