// sim/smoke/hello_defs.svh —— 冒烟测试用的头文件
//
// 存在的唯一目的：证明两件事
//   1. tools/run_rtl_sim.py 同步的文件清单（sim_files.f）里多文件混排没问题；
//   2. `--incdir <dir>` 能正确变成 VCS 的 `+incdir+<远端路径>`，
//      这样 RTL 侧 `\`include "rtl_params.vh"` 这种写法以后可以直接照搬。

`ifndef HELLO_DEFS_SVH
`define HELLO_DEFS_SVH

`define HELLO_TAG "hello_defs.svh"

`endif  // HELLO_DEFS_SVH
