#!/usr/bin/env bash
set -euo pipefail

# 在 ASCII 构建树内通过符号链接引用源文件。日志和对象留在独立目录中。
project_dir=$(cd -- "$(dirname -- "$0")/.." && pwd)
build_dir=$(mktemp -d /tmp/ad4080-peripherals.XXXXXX)
printf 'Build directory %s\n' "$build_dir"
verilator_cmd=${VERILATOR:-verilator}
tb_file=integration/peripheral_services_tb.sv

cd -- "$build_dir"
mkdir integration
ln -s "$project_dir/integration/files.f" integration/files.f
ln -s "$project_dir/integration/lint.vlt" integration/lint.vlt
ln -s "$project_dir/integration/check_tool_run.py" integration/check_tool_run.py
ln -s "$project_dir/integration/run_verify.sh" integration/run_verify.sh
while IFS= read -r rtl_file; do
    mkdir -p "$(dirname "$rtl_file")"
    ln -s "$project_dir/$rtl_file" "$rtl_file"
done < integration/files.f
ln -s "$project_dir/$tb_file" "$tb_file"

# 各工具从相同 filelist 展开。保留源哈希与编译器版本，便于对应本次运行证据。
while IFS= read -r rtl_file; do
    shasum -a 256 "$rtl_file"
done < integration/files.f > sources.sha256
shasum -a 256 integration/files.f integration/lint.vlt integration/check_tool_run.py \
    integration/run_verify.sh "$tb_file" >> sources.sha256
"$verilator_cmd" -V > verilator_version.log

"$verilator_cmd" --lint-only -Wall --top-module ad4080_peripheral_services \
    --Mdir lint integration/lint.vlt -f integration/files.f 2>&1 | tee lint.log
printf 'RTL lint PASS\n' | tee -a lint.log

"$verilator_cmd" --binary --timing --assert --compiler clang -CFLAGS '-std=c++20' -j 2 \
    --Mdir obj_dir --top-module peripheral_services_tb -f integration/files.f "$tb_file" \
    > compile.log 2>&1 || { cat compile.log; exit 1; }
./obj_dir/Vperipheral_services_tb 2>&1 | tee simulation.log
python3 integration/check_tool_run.py simulation simulation.log 'PERIPHERAL_SERVICES PASS'

# 可选无工艺库综合，保留存储器抽象，避免把四个银行展开为寄存器阵列。
if [[ -n ${YOSYS:-} ]]; then
    # WASI 运行时只能读取其预开目录。把同一组源作为临时连续输入交给前端。
    while IFS= read -r rtl_file; do
        cat "$rtl_file"
        printf '\n'
    done < integration/files.f > synth_sources.sv
    {
        printf '%s\n' 'read_verilog -sv -D SYNTHESIS synth_sources.sv' \
            'hierarchy -check -top ad4080_peripheral_services' 'proc' 'opt' \
            'memory -nomap' 'check -assert' 'stat' 'write_json synthesized.json'
    } > synth.ys
    "$YOSYS" --version > yosys_version.log
    "$YOSYS" -l synth.log -Q -T -s synth.ys > synth_console.log 2>&1 \
        || { cat synth_console.log; exit 1; }
    python3 integration/check_tool_run.py yosys synthesized.json synth.log \
        ad4080_peripheral_services --memory result_banks
    printf 'Generic synthesis structure PASS\n'
fi
shasum -a 256 -c sources.sha256 > source_check.log
