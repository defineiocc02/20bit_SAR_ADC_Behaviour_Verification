#!/usr/bin/env bash
#=============================================================================
# synth/sweep_p_stages.sh -- 在 VM 上串行扫 recon_core 的 P_STAGES（面积/时序旋钮）
#=============================================================================
# 为什么要有这个脚本
#   `P_STAGES` 是 div_floor 里"一个时钟内级联几级恢复余数"的旋钮：
#       N_CYC = ceil(63 / P_STAGES)   （63 = ACC_BITS - (V_FRAC+1) = P_W_A）
#   P_STAGES 大 -> 组合深、面积大、但一次除法占的拍数少（吞吐高）；
#   P_STAGES 小 -> 面积小、但除法要占用更多拍（吞吐掉）。
#   顶层 `sar20_digital_core` 里 recon_core 的 P_STAGES 是**写死的 7**，
#   所以判断"能不能为时序牺牲吞吐"必须逐个参数点真跑一遍。
#
# 前置
#   在**已经同步好的**远端目录里执行（`run_synth.sh` / `run_dc.tcl` /
#   `src/` / `inc/` 都在当前目录）。同步用本机侧的：
#       python synth/run_synth.py --name sweep_recon --top recon_core --dry-run \
#           --files rtl/core/recon_core.sv rtl/core/div_floor.sv rtl/core/adc2_dec.sv rtl/core/cal_weight_reduce.sv rtl/core/cal_residue_mac.sv rtl/core/cal_output_stage.sv \
#           --incdirs rtl/params --clk-period 10
#   **不要**在一次长任务还在跑的时候覆盖 run_synth.sh（bash 按字节偏移读脚本，
#   会读到被撕开的残片 -> 假的 rc=2，这是踩过的真事故；见 README §6 坑 4）。
#
# 用法
#   cd ~/adc_rtl_synth/sweep_recon
#   FILES="src/rtl/core/recon_core.sv src/rtl/core/div_floor.sv src/rtl/core/adc2_dec.sv src/rtl/core/cal_weight_reduce.sv src/rtl/core/cal_residue_mac.sv src/rtl/core/cal_output_stage.sv" \
#   INCDIRS=inc/rtl/params CLK_PERIOD=10 ./sweep_p_stages.sh "1 4 7 14 21 63"
#
# 产出（当前目录）
#   out_p<N>/{area.rpt,timing.rpt,power.rpt,qor.rpt,status.txt,dc.log,elaborate.log,
#             run_dc.snapshot.tcl}   每个参数点一份，脚本版本可追溯
#   wrapper_p<N>.log    run_synth.sh 的退出码判定过程（含真实 rc）
#   rc_p<N>.txt         每个点的 rc（0 收敛 / 3 时序未收敛 / 1 流程失败）
#   sweep.log           整轮的时序日志（含每点墙钟秒数）
#   SWEEP_DONE          全部跑完的标记文件（轮询用；**没有它就不要下结论**）
#
# 退出码
#   0 = 所有点都跑完（**不代表都收敛**，逐点看 rc_p<N>.txt）
#   2 = 参数错（缺 FILES/INCDIRS 等）
#=============================================================================
set -uo pipefail   # 刻意**不开 -e**：单点失败不应中断整轮扫描，否则只剩半个表

POINTS="${1:-1 4 7 14 21 63}"
TOP="${TOP:-recon_core}"
PERIOD="${CLK_PERIOD:-10}"
FILES="${FILES:-}"
INCDIRS="${INCDIRS:-}"
DCT="${DC_TIMEOUT:-5400}"

if [[ -z "$FILES" ]]; then echo "sweep_p_stages.sh: FILES is required (相对本目录的源文件列表)" >&2; exit 2; fi
if [[ ! -x ./run_synth.sh ]]; then echo "sweep_p_stages.sh: ./run_synth.sh not executable here (先同步再跑)" >&2; exit 2; fi

rm -f SWEEP_DONE
# ⚠️ 这里**刻意不删 `rc_p*.txt`、也不截断 `sweep.log`**（实测踩过）：
# 早先的写法是 `rm -f SWEEP_DONE rc_p*.txt` + `> sweep.log`。同一个目录被复用
# 跑第二个批次时（本轮就先跑了 7/4/63，后来又跑 5），这些"跨批次"的汇总记录会被
# **后一批擦掉** —— P=1/4/7 的 `rc_p<N>.txt` 和一个批次前的 `sweep.log` 就是这样丢的。
# 权威证据（每点自己的 `out_p<N>/status.txt` 与 `wrapper_p<N>.log`）没丢，
# 所以结论没受影响；但"跨批次记录随批次消失"是设计缺陷，改掉。
# 现在：`rc_p<N>.txt` 只按点名覆盖自己；`sweep.log` 改为**追加**并带批次分隔头。

{
    echo "==================================================================="
    echo "sweep start $(date -Is) host=$(hostname) top=$TOP period=$PERIOD points='$POINTS'"
    # 口径必须写进日志：`compile`（非 ultra）与 `compile_ultra` 的面积/时序**不可直接比较**，
    # 一张表里混两种口径等于没口径。status.txt 里也逐点记 COMPILE_MODE=。
    echo "compile_mode=${COMPILE_MODE:-ultra}"
    echo "run_synth.sh md5=$(md5sum run_synth.sh | awk '{print $1}') run_dc.tcl md5=$(md5sum run_dc.tcl | awk '{print $1}')"
    for p in $POINTS; do
        t0=$(date +%s)
        echo "--- P_STAGES=$p start $(date -Is) ---"
        # 每个点一个独立 out 目录：报告不互相覆盖，且各自的
        # run_dc.snapshot.tcl 就是"这一点用的是哪版 tcl"的证据。
        DC_TIMEOUT="$DCT" ./run_synth.sh \
            --top "$TOP" --files "$FILES" \
            ${INCDIRS:+--incdirs "$INCDIRS"} \
            --clk-period "$PERIOD" --params "P_STAGES=$p" \
            --out-dir "out_p$p" > "wrapper_p$p.log" 2>&1
        rc=$?
        t1=$(date +%s)
        printf '%s\n' "$rc" > "rc_p$p.txt"
        echo "P_STAGES=$p rc=$rc elapsed=$((t1 - t0))s $(date -Is)"
    done
    echo "sweep end $(date -Is)"
} >> sweep.log 2>&1

touch SWEEP_DONE
echo "sweep_p_stages.sh: all points done; see sweep.log / rc_p*.txt"
exit 0
