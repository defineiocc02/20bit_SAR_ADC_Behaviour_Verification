#!/bin/bash
set -e
export VCS_HOME=/opt/synopsys/vcs/W-2024.09-SP1
export PATH=$VCS_HOME/bin:$PATH
export SNPSLMD_LICENSE_FILE=27080@localhost

rm -rf /root/timing/topw/mut
mkdir -p /root/timing/topw/mut
cd /root/timing/topw/mut
cp /root/timing/topw/tb/tb_digital_top_w.sv .
cp /root/timing/topw/tb/vec_topw_coef.txt .
cp /root/timing/topw/tb/vec_topw_gold.txt .
cp /root/timing/topw/rtl/*.sv .
cp digital_top_w.sv golden_ref.sv

run_mut() {
  name="$1"
  vcs -full64 -sverilog -timescale=1ns/1ps -o simv_mut tb_digital_top_w.sv digital_top_w.sv dither_split.sv dem_shuffle.sv recon_d5_w2.sv >/dev/null 2>&1
  echo "===== MUT $name ====="
  ./simv_mut > mut_$name.log 2>&1 || true
  echo "  ERROR lines: $(grep -c 'ERROR:' mut_$name.log)"
  grep -E "^PHASE|^RESULT" mut_$name.log
}

# M1: slice id +1
# M4: g_tab 增量更新丢掉了减旧值（重复加载版本时 G 错）
cp golden_ref.sv digital_top_w.sv
python3 - <<'PY'
p='digital_top_w.sv'; s=open(p).read()
s=s.replace("g_tab[cfg_slice] <= g_tab[cfg_slice] - w_tab[cfg_slice][cfg_unit] + cfg_w;",
            "g_tab[cfg_slice] <= g_tab[cfg_slice] + cfg_w;")
open(p,'w').write(s)
PY
run_mut "gtab-no-sub"

cp golden_ref.sv digital_top_w.sv
python3 - <<'EOF'
p='digital_top_w.sv'; s=open(p).read()
s=s.replace("(sid_base[4:0] + gi) % 5'd18", "(sid_base[4:0] + gi + 1) % 5'd18")
open(p,'w').write(s)
EOF
run_mut "slice-id+1"

# M2: mask ignores y
cp golden_ref.sv digital_top_w.sv
python3 - <<'EOF'
p='digital_top_w.sv'; s=open(p).read()
s=s.replace("(j == int'(reserved_i)) ? y[s] : h[j]", "(j == int'(reserved_i)) ? h[j] : h[j]")
open(p,'w').write(s)
EOF
run_mut "mask-ignore-y"

# M3: dither crossed at E2
cp golden_ref.sv digital_top_w.sv
python3 - <<'EOF'
p='digital_top_w.sv'; s=open(p).read()
s=s.replace("dither_b<= dither_a;", "dither_b<= dither_a ^ 6'd1;")
open(p,'w').write(s)
EOF
run_mut "dither-flip"

cp golden_ref.sv digital_top_w.sv
