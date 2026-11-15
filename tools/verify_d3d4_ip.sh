#!/usr/bin/env bash
# D3/D4 数字 IP 的完整验收：三层证据一条命令跑完
#
#   L1 叶子级逐位对拍   dem_mask_v3 / dither_pre  vs  dem_dither.py
#   L2 IP 级逐位对拍    d3d4_ip（含相位发生器）  vs  ref_d3d4_ip.py
#   L3 门级等价         综合网表 + 标准单元模型   vs  同一金标
#
# 综合（L4）需要 DC 与 PDK，不在本脚本内自动执行——见 docs/SYNTH_D3D4.md。
#
# 用法：
#   本地（需 iverilog 或 vcs 在 PATH 里）：
#     bash tools/verify_d3d4_ip.sh
#   远端（本机无仿真器时）：
#     D3D4_RUNNER=vm-yian bash tools/verify_d3d4_ip.sh
#
# 环境变量：
#   D3D4_RUNNER   远端主机名（走 ~/.ssh/config）。留空则本地执行。
#   D3D4_WORKDIR  远端工作目录，默认 ~/d3d4_verify
#   D3D4_SIM      vcs | iverilog，默认 vcs
#
# 注意：本脚本**不会**把"编译成功"当成通过。每一层都必须出现
#       `RESULT: PASS` 才继续，否则立即非零退出。
set -uo pipefail
cd "$(dirname "$0")/.."

RUNNER="${D3D4_RUNNER:-}"
WORK="${D3D4_WORKDIR:-~/d3d4_verify}"
SIM="${D3D4_SIM:-vcs}"
SHA=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)

# 远端非交互 shell 没有 EDA 环境，必须显式设置（这是踩过的坑）
SETUP='export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1; \
export SNPSLMD_LICENSE_FILE=27080@localhost; \
export PATH=$VCS_HOME/bin:$PATH'

run() { if [ -n "$RUNNER" ]; then ssh -o BatchMode=yes "$RUNNER" "$1"; else bash -c "$1"; fi; }

fail() { echo "FAIL: $*"; exit 1; }

echo "=================================================================="
echo " D3/D4 数字 IP 验收   SHA=$SHA  SIM=$SIM  runner=${RUNNER:-local}"
echo "=================================================================="

# ---------------------------------------------------------------- 准备
echo "[0/4] 准备源文件与向量"
SRCS="rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/sched18.sv rtl/follower_rt.sv \
      rtl/dem_mask_v3.sv rtl/dither_pre.sv rtl/d3d4_ip.sv"
TBS="tb/tb_dem_dither.sv tb/tb_d3d4_ip.sv"
VECS="tb/vec_phase_rtl.txt tb/vec_d3d4_ip.txt"
# 编译清单也要带上：L1 走 files_d3d4.f，缺它 vcs 直接报
# "Unable to open 'files_d3d4.f'"，而错误信息里不含"编译"二字，容易误判。
FLISTS="build/filelists/files_d3d4.f build/filelists/files_d3d4_ip.f"

# 金标必须先重新生成并跑到成功，否则后面的 PASS 可能是拿旧向量自证
python3 tools/gen_vec_phase.py  >/dev/null || fail "生成 vec_phase_rtl.txt"
python3 tools/ref_d3d4_ip.py    >/dev/null || fail "生成 vec_d3d4_ip.txt"
echo "      金标已重新生成（gen_vec_phase.py / ref_d3d4_ip.py）"

if [ -n "$RUNNER" ]; then
  # $WORK 里可能带 ~ 或 $HOME，scp 那一侧不会展开，必须先解析成绝对路径。
  # 这一步踩过两次：`~/x` 会被 scp 当成字面目录名，
  # `$HOME/x` 在远端单引号上下文里不展开，都会得到 "No such file or directory"。
  WORK=$(run "cd $WORK 2>/dev/null || mkdir -p $WORK; cd $WORK && pwd") \
      || fail "解析远端工作目录 $WORK"
  echo "      远端工作目录: $WORK"
  run "mkdir -p $WORK/rtl $WORK/tb" || fail "建远端目录"
  scp -q $SRCS "$RUNNER:$WORK/rtl/"  || fail "拷贝 rtl"
  scp -q $TBS  "$RUNNER:$WORK/tb/"   || fail "拷贝 tb"
  scp -q $VECS "$RUNNER:$WORK/tb/"   || fail "拷贝向量"
  # 清单要放到与 -f 一致的相对位置，否则远端解析不到。
  # 本地清单在 build/filelists/，远端也建同样目录结构，这样两侧的 -f 路径可用同一个字符串。
  run "mkdir -p $WORK/build/filelists" || fail "建远端清单目录"
  scp -q $FLISTS "$RUNNER:$WORK/build/filelists/" || fail "拷贝编译清单"
  RUNCD="cd $WORK"
else
  RUNCD="cd $PWD"
fi

# ---------------------------------------------------------------- L1
echo "[1/4] L1 叶子级逐位对拍（133376 + 512 组）"
if [ "$SIM" = "vcs" ]; then
  run "$SETUP; $RUNCD && vcs -full64 -sverilog -timescale=1ns/1ps \
       -f build/filelists/files_d3d4.f -o simv_l1 > l1_c.log 2>&1" || fail "L1 编译"
  run "$RUNCD && ./simv_l1 > l1.txt 2>&1" || fail "L1 仿真"
else
  run "$RUNCD && iverilog -g2012 -o simv_l1 -f build/filelists/files_d3d4.f > l1_c.log 2>&1" || fail "L1 编译"
  run "$RUNCD && vvp simv_l1 > l1.txt 2>&1" || fail "L1 仿真"
fi
if [ -n "$RUNNER" ]; then
  scp -q "$RUNNER:$WORK/l1.txt" /tmp/d3d4_l1.txt || fail "取回 L1 日志"
else
  cp l1.txt /tmp/d3d4_l1.txt
fi
L1=$(python3 tools/cmp_dem_dither.py /tmp/d3d4_l1.txt 2>&1)
echo "$L1" | tail -4 | sed 's/^/      /'
echo "$L1" | grep -q "RESULT: PASS" || fail "L1 未通过"

# ---------------------------------------------------------------- L2
echo "[2/4] L2 IP 级逐位对拍（含相位发生器，512 笔）"
IPF="rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/sched18.sv rtl/follower_rt.sv \
     rtl/dem_mask_v3.sv rtl/dither_pre.sv rtl/d3d4_ip.sv tb/tb_d3d4_ip.sv"
if [ "$SIM" = "vcs" ]; then
  run "$SETUP; $RUNCD && vcs -full64 -sverilog -timescale=1ns/1ps $IPF \
       -o simv_l2 > l2_c.log 2>&1" || fail "L2 编译"
  # tb_d3d4_ip.sv 用相对路径 $fopen("vec_d3d4_ip.txt")，
  # 因此必须在 tb/ 目录里执行，否则报 "FATAL: cannot open vec_d3d4_ip.txt"。
  run "cd $WORK/tb && ../simv_l2 > ../l2.txt 2>&1" || fail "L2 仿真"
else
  run "$RUNCD && iverilog -g2012 -o simv_l2 $IPF > l2_c.log 2>&1" || fail "L2 编译"
  run "cd tb && ../simv_l2 > ../l2.txt 2>&1" || fail "L2 仿真"
fi
if [ -n "$RUNNER" ]; then
  scp -q "$RUNNER:$WORK/l2.txt" /tmp/d3d4_l2.txt || fail "取回 L2 日志"
else
  cp l2.txt /tmp/d3d4_l2.txt
fi
grep -E "覆盖|errors|RESULT" /tmp/d3d4_l2.txt | sed 's/^/      /'
grep -q "RESULT: PASS" /tmp/d3d4_l2.txt || fail "L2 未通过"

# ---------------------------------------------------------------- L2.5
# sched18 的随机性统计。逐拍对拍只能证明"RTL == 模型"，
# 不能证明"随机性够好"——退化序列下 |ACQ|=8 等不变量依然成立。
# 这正是曾经 16 位乘法溢出（i1≡j0≡0）能长期潜伏的原因。
echo "[2.5] sched18 随机性统计（20000 拍）"
SCHED18=$(python3 tools/sched18_stats.py --cycles 20000 2>&1)
echo "$SCHED18" | grep -E "违例|不平衡|χ²|IDLE|RESULT" | sed 's/^/      /'
echo "$SCHED18" | grep -q "RESULT: PASS" || fail "sched18 随机性不达标"

# ---------------------------------------------------------------- L2.6
# 逐事件对齐。smoke 级判据（k 增长 8 次 / mask 改变 8 次）抓不到
# "mask 落后 k 一个事件"这类错位，必须逐事件比 k 与 160 位 mask。
echo "[2.6] 逐事件对齐（k / mask / 有效标志同事件）"
python3 tools/gen_event_align.py >/dev/null || fail "生成事件对齐金标"
if [ -n "$RUNNER" ]; then
  scp -q tb/tb_event_align.sv tb/vec_event_align.txt "$RUNNER:$WORK/tb/" || fail "拷贝事件对齐 TB"
fi
EAF="rtl/dem_phase_rr.sv rtl/dem_phase_gen.sv rtl/sched18.sv rtl/follower_rt.sv \
     rtl/dem_mask_v3.sv rtl/dither_pre.sv rtl/d3d4_ip.sv tb/tb_event_align.sv"
if [ "$SIM" = "vcs" ]; then
  run "$SETUP; $RUNCD && vcs -full64 -sverilog -timescale=1ns/1ps $EAF \
       -o simv_ea > ea_c.log 2>&1" || fail "事件对齐编译"
  run "cd $WORK/tb && ../simv_ea > ../ea.txt 2>&1" || fail "事件对齐仿真"
else
  run "$RUNCD && iverilog -g2012 -o simv_ea $EAF > ea_c.log 2>&1" || fail "事件对齐编译"
  run "cd tb && ../simv_ea > ../ea.txt 2>&1" || fail "事件对齐仿真"
fi
if [ -n "$RUNNER" ]; then
  scp -q "$RUNNER:$WORK/ea.txt" /tmp/d3d4_ea.txt || fail "取回事件对齐日志"
else
  cp ea.txt /tmp/d3d4_ea.txt
fi
grep -E "RESULT|errors" /tmp/d3d4_ea.txt | sed 's/^/      /'
grep -q "RESULT: PASS" /tmp/d3d4_ea.txt || fail "逐事件对齐未通过"

# ---------------------------------------------------------------- L3
echo "[3/4] L3 门级等价（需已有综合网表，见 docs/SYNTH_D3D4.md）"
NET="${D3D4_NETLIST:-}"
if [ -z "$NET" ]; then
  echo "      跳过：未设置 D3D4_NETLIST（指向综合网表）"
  echo "      复现方式见 docs/SYNTH_D3D4.md §六"
  L3_STATUS="SKIPPED"
else
  SCMODEL="${D3D4_SCMODEL:?需要 D3D4_SCMODEL 指向标准单元 .v 模型}"
  run "$SETUP; $RUNCD && vcs -full64 -sverilog -timescale=1ns/1ps \
       $SCMODEL $NET tb/tb_d3d4_ip.sv -o simv_l3 > l3_c.log 2>&1" || fail "L3 编译"
  run "cd $WORK/tb && ../simv_l3 > ../l3.txt 2>&1" || fail "L3 仿真"
  if [ -n "$RUNNER" ]; then
    scp -q "$RUNNER:$WORK/l3.txt" /tmp/d3d4_l3.txt || fail "取回 L3 日志"
  else
    cp l3.txt /tmp/d3d4_l3.txt
  fi
  grep -E "覆盖|errors|RESULT" /tmp/d3d4_l3.txt | sed 's/^/      /'
  grep -q "RESULT: PASS" /tmp/d3d4_l3.txt || fail "L3 未通过"
  L3_STATUS="PASS"
fi

# ---------------------------------------------------------------- 汇总
echo "=================================================================="
echo " 结果汇总"
echo "------------------------------------------------------------------"
echo "  L1 叶子级逐位对拍   PASS   (133376 + 512)"
echo "  L2 IP 级逐位对拍    PASS   (512 笔，含相位发生器)"
echo "  L2.5 sched18 随机性 PASS   (20000 拍，χ²/IDLE 覆盖/stats)"
echo "  L2.6 逐事件对齐     PASS   (k / 160-bit mask / 标志 同事件)"
echo "  L3 门级等价         $L3_STATUS"
echo "  L4 综合             见 docs/SYNTH_D3D4.md（需 DC + PDK）"
echo "=================================================================="
[ "$L3_STATUS" = "SKIPPED" ] && echo "注意：L3 被跳过，本次只证明 RTL 层正确。"
exit 0
