# d3d4_ip 的显式综合输入契约（供 synth/run_dc.tcl 消费）
#
# 用法（在远端执行，库路径按实际 PDK 填）：
#   export SYNTH_INPUT_TCL=$PWD/synth/d3d4/contract_d3d4_ip.tcl
#   dc_shell -f synth/run_dc.tcl
#
# 所有路径都是绝对路径，run_dc.tcl 会逐个 assert 存在性；
# 本文件不提供任何默认值，理由见 synth/README.md 的门禁说明。
set synth_inputs [dict create \
    top          d3d4_ip \
    sources      [list \
                    /work/d3d4/rtl/dem_phase_gen.sv \
                    /work/d3d4/rtl/dem_mask_v3.sv \
                    /work/d3d4/rtl/dither_pre.sv \
                    /work/d3d4/rtl/d3d4_ip.sv] \
    include_dirs [list /work/d3d4/rtl] \
    defines      "" \
    libraries    [list /work/lib/tcbn28hpcplusbwp7t40p140ssg0p81vm40c_ccs.db] \
    sdc          /work/d3d4/synth/d3d4/sdc_d3d4_ip.sdc \
    clock        clk \
    compile_command compile_ultra \
    parameters   "" \
    out          /work/d3d4/out/d3d4_ip]
