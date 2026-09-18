// sim/smoke/tb_multi.sv —— 多文件 + include 路径的冒烟测试
//
// 和 hello_vcs.sv 一起跑，验证「文件列表里多个文件 + --incdir」这条路径：
//
//   python tools/run_rtl_sim.py --top tb_multi --fresh \
//       --src sim/smoke/hello_vcs.sv sim/smoke/tb_multi.sv sim/smoke/hello_defs.svh \
//       --incdir sim/smoke --fetch '*.hex'
//
// 这里的 `include "hello_defs.svh"` 故意只写文件名 —— 它能不能被找到，完全取决于
// --incdir 有没有正确传到 VCS。

`include "hello_defs.svh"

`timescale 1ns/1ps

module tb_multi;

  // hello_vcs 自己会 $finish，所以这个 TB 不需要再产生激励
  hello_vcs u_dut ();

  initial begin
    $display("%s: tb_multi elaborated, hello_vcs instantiated as u_dut", `HELLO_TAG);
  end

endmodule
