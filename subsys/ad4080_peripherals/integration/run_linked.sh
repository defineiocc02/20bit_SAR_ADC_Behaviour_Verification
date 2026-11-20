#!/usr/bin/env bash
# 单次链路构建使用新目录，编译失败时不沿用其它运行的二进制。
# 记录结束位与 SRAM 首、末读事件有不同判据；二者都保留回执。
# 真正的编译和运行输出写入本轮目录，以便定位首次缺载拍。
# 本次绑定中的 CRC 为工程夹具服务，尾部位序和迟到退场均由 RTL 测量。
# 检查点完成后仍核对源摘要，以发现编译时并发编辑。
set -euo pipefail

# 保持每轮构建目录独立。用主文件表与附加文件表定位唯一的生产 RTL。
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
build_dir=$(mktemp -d "${READBACK_BUILD_ROOT:-/tmp}/ad4080-readback-link.XXXXXX")
printf 'Build directory %s\n' "$build_dir"
verilator_cmd=${VERILATOR:-verilator}
tb_file=integration/peripheral_readback_link_tb.sv

cd -- "$build_dir"
mkdir integration
for list_file in files.f readback_files.f lint.vlt check_tool_run.py run_linked.sh; do
    ln -s "$project_dir/integration/$list_file" "integration/$list_file"
done
# 两份清单分别列出服务和数据读回分层，供结构检查识别真实存储所有权。
source_lists=(integration/files.f integration/readback_files.f)
cat "${source_lists[@]}" > rtl_sources.f
while IFS= read -r rtl_file; do
    mkdir -p "$(dirname "$rtl_file")"
    ln -s "$project_dir/$rtl_file" "$rtl_file"
    shasum -a 256 "$rtl_file"
done < rtl_sources.f > sources.sha256
ln -s "$project_dir/$tb_file" "$tb_file"
shasum -a 256 "${source_lists[@]}" integration/lint.vlt integration/check_tool_run.py \
    integration/run_linked.sh "$tb_file" >> sources.sha256

"$verilator_cmd" --lint-only -Wall --top-module ad4080_fifo_lvds_record_link \
    --Mdir lint integration/lint.vlt -f rtl_sources.f 2>&1 | tee lint.log
printf 'Readback RTL lint PASS\n' | tee -a lint.log
"$verilator_cmd" --binary --timing --assert --compiler clang -CFLAGS '-std=c++20' -j 2 \
    --output-split 0 -MAKEFLAGS VM_PARALLEL_BUILDS=0 \
    --Mdir obj_dir --top-module peripheral_readback_link_tb -f rtl_sources.f "$tb_file" \
    > compile.log 2>&1 || { cat compile.log; exit 1; }
./obj_dir/Vperipheral_readback_link_tb 2>&1 | tee simulation.log
python3 integration/check_tool_run.py simulation simulation.log 'PERIPHERAL_READBACK_LINK PASS'
python3 integration/check_tool_run.py simulation simulation.log 'RECORD_LVDS_GOLDEN PASS'
# 这些是连续原始 CLK 与两种不会压缩成成功帧的故障，防止截断日志漏验。
python3 integration/check_tool_run.py simulation simulation.log 'RECORD_RAW_N2 PASS'
python3 integration/check_tool_run.py simulation simulation.log 'RECORD_RAW_N1 PASS'
python3 integration/check_tool_run.py simulation simulation.log 'RECORD_LATE_CRC PASS'
python3 integration/check_tool_run.py simulation simulation.log 'RECORD_PAYLOAD_UNDERRUN PASS'
printf '%s\n' 'case,raw,t_ps,valid,kind,pair,first,last,first_load,last_load,crc_ready,error,error_code' > raw_record.csv
sed -n 's/^RAW_RECORD_CSV,//p' simulation.log >> raw_record.csv
# 完成的 N=2/N=1 分别 36/26 行。晚 CRC 和断供各保留 24 个正常拍加 1 个缺拍。
test "$(wc -l < raw_record.csv)" -eq 113

# 可选综合前端检查。WASI 只能读预开目录，从统一列表生成临时连续输入。
if [[ -n ${YOSYS:-} ]]; then
    while IFS= read -r rtl_file; do
        cat "$rtl_file"
        printf '\n'
    done < rtl_sources.f > synth_sources.sv
    "$YOSYS" --version > yosys_version.log
    "$YOSYS" -l synth.log -Q -T -p 'read_verilog -sv -D SYNTHESIS synth_sources.sv; hierarchy -check -top ad4080_fifo_lvds_record_link; proc; opt; memory -nomap; check -assert; stat; write_json synthesized.json' \
        > synth_console.log 2>&1 || { cat synth_console.log; exit 1; }
    python3 integration/check_tool_run.py yosys synthesized.json synth.log ad4080_fifo_lvds_record_link \
        --memory record_link
    printf 'Readback generic structure PASS\n'
fi
shasum -a 256 -c sources.sha256 > source_check.log
