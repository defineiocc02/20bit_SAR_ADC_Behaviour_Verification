// =============================================================================
// sim/smoke/hello_vcs.sv —— VCS 通路冒烟测试（不依赖任何 RTL 子树）
//
// 用途：在任何 RTL 写出来之前，先证明「本机 → 远程 VM → VCS 编译 → 运行 → 取回
// 日志/产物」这条链路是通的，并且退出码能正确区分三种结局。
//
// 通过 tools/run_rtl_sim.py 跑，三种用法：
//
//   # 1) 干净通过：退出码应为 0
//   python tools/run_rtl_sim.py --top hello_vcs --src sim/smoke/hello_vcs.sv \
//       --fresh --fetch '*.hex'
//
//   # 2) 注入 $error：退出码应为 4
//   python tools/run_rtl_sim.py --top hello_vcs --src sim/smoke/hello_vcs.sv \
//       --define HELLO_INJECT_ERROR --fetch '*.hex'
//
//   # 3) 注入 $fatal：退出码应为 3
//   python tools/run_rtl_sim.py --top hello_vcs --src sim/smoke/hello_vcs.sv \
//       --define HELLO_INJECT_FATAL --fetch '*.hex'
//
// 若是用 sim/run_vcs.sh 直接跑：
//   sim/run_vcs.sh -top hello_vcs -l /tmp/hello.log sim/smoke/hello_vcs.sv
//
// 选择 `$finish` 而不是 `$stop`：VCS W-2024.09 不带 DVE，`$stop` 会挂在交互式
// 提示符上，在没有 GUI 的批处理环境里只会白等超时。
// =============================================================================

`timescale 1ns/1ps

module hello_vcs;

  integer fd;
  integer i;

  initial begin
    $display("HELLO_FROM_VCS: top=hello_vcs, time=%0t", $time);

    // 写一个小文件出去，用来验证 tools/run_rtl_sim.py --fetch 的取回通路
    fd = $fopen("hello_out.hex", "w");
    if (fd == 0) begin
      $error("无法打开 hello_out.hex 用于写入");
    end else begin
      for (i = 0; i < 4; i = i + 1) begin
        $fwrite(fd, "%04x\n", 16'h1000 + i);
      end
      $fclose(fd);
      $display("HELLO_FROM_VCS: wrote hello_out.hex (4 lines)");
    end

`ifdef HELLO_INJECT_ERROR
    $error("deliberate $error (HELLO_INJECT_ERROR)");
`endif

`ifdef HELLO_INJECT_FATAL
    $fatal(1, "deliberate $fatal (HELLO_INJECT_FATAL)");
`endif

    $display("HELLO_FROM_VCS: done");
    $finish;
  end

endmodule
