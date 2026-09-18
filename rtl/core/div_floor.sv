//===========================================================================
// div_floor.sv -- 有符号 floor 除法（全数字核里唯一的除法）
//===========================================================================
// 职责一句话
//   计算 q = floor(a / d)：a 有符号、d 无符号且 > 0，向 **-inf** 取整。
//   本模块**不做**乘法、不做饱和、不做溢出判定；它只保证"除法本身"的取整方向。
//
// 来源
//   docs/rtl/RTL_ARITHMETIC_CONTRACT.md §3.5：
//       word = floor(shifted / denom)
//   RTL 的 `/` 向 0 截断，Python 的 `//` 向 -inf 取整 —— 负商差 1 个 LSB，
//   且**只在半个码流上出错**。仿真激励选得好就发现不了，所以必须单独验证。
//
// 算法（恢复余数法，逐位）
//   mag = |a|（无符号 P_W_A 位），高位补零到 W_PAD = ceil(P_W_A/P_STAGES)*P_STAGES
//   R = 0; Q = 0
//   for i = 0 .. W_PAD-1（从高位到低位）:
//       R = (R << 1) | mag[i]
//       if (R >= d) { R -= d; Q[i] = 1 } else { Q[i] = 0 }
//   q_abs = Q[P_W_A-1:0]
//   q     = a[MSB] ? -(q_abs + (R != 0)) : q_abs
//
// 正确性论证（供复核者复算，四条都独立可查）
//   1. 恢复余数法对 mag/d（d > 0）给出**精确**的商与余数 —— 标准结论。
//   2. W_PAD >= P_W_A，高位补零只产生**前导 0 商位**，商值不变。
//   3. 负修正：a < 0 时 floor(a/d) = -ceil(|a|/d) = -(q_abs + (R != 0))。
//   4. **不可能溢出**：|a| < 2^(P_W_A-1)、d >= 1 ==> |q| <= |a| < 2^(P_W_A-1)，
//      P_W_A 位有符号一定装得下。因此本模块**没有**溢出输出。
//
// 单位契约
//   无量纲整数。a 与 q 同为 P_W_A 位有符号；d 为 P_W_D 位无符号。
//
// 参数来源分级
//   P_W_A 默认 = ACC_BITS - (V_FRAC + 1) = 63（由契约 §3.5.1 的 33 位移位推来）
//   P_W_D 默认 = 60（由契约 §5 的 SigmaW < 2^60 推来）
//   二者都是 [推导] 值，且默认取自 rtl_params.vh。**RTL 不另立参数表。**
//
// 契约与不变量 / 适用域
//   * d == 0 判为 `err`（结构性错误，对应契约 §4.3），此时 q 强制为 0。
//   * 固定延迟：`start` 在第 t 拍为高 -> `done` 在第 t+N_CYC+1 拍为高
//     （N_CYC = W_PAD/P_STAGES）。`busy` 期间到来的 `start` 被**忽略**
//     （不做反压；由 ctrl_fsm 保证不重叠）。
//   * **组内位序**：一个时钟内级联 P_STAGES 级，第 s 级产生的商位落在该组的
//     第 (P_STAGES-1-s) 位（MSB 优先）。写反了会得到"每 P_STAGES 位一块的位反转"，
//     且 P_STAGES=1 时看不出来 —— 改动这里必须用 `sim/tb/p2_tb.sv` 的 T1/T2 单向测试复核。
//   * **位反转的实测指纹（极性已由独立复核更正，2026-09-18）**：
//     `a = 0x4000_0000_0000_0001`、`d = 1`，`P_W_A = 63`。
//     ⚠️ a 的 bit 62 是**符号位**，故 a = -(2^62 - 1)（负数），`mag = 2^62 - 1`。
//     正确结果：`Q[56] = 1`（唯一的那个商位）；写反后：商位被搬到 `Q[62]`。
//     我先前把两边的极性说反了 —— **可用的指纹是"差异位在 {56, 62}"**，
//     至于哪边算对，要靠 `a // d` 判，不要靠叙述。
//   * 本模块是**迭代**实现：单次占用 N_CYC 拍。P_STAGES 是面积/时序旋钮。
//     若为时序把 P_STAGES 调小，**吞吐会掉**，调用方必须重算相位预算。
//     替代方案（阵列除法器 / 倒数 ROM + 修正）登记为 P6 优化项，
//     采用前必须补"商误差恰为 0"的证明与独立验证。
//===========================================================================
`include "rtl_params.vh"

module div_floor #(
    parameter int P_W_A    = ACC_BITS - (V_FRAC + 1),
    parameter int P_W_D    = 60,
    parameter int P_STAGES = 7
) (
    input  logic                    clk,
    input  logic                    rst_n,
    input  logic                    start,
    input  logic signed [P_W_A-1:0] a,
    input  logic        [P_W_D-1:0] d,
    output logic signed [P_W_A-1:0] q,
    output logic                    err,
    output logic                    busy,
    output logic                    done
);

  localparam int W_R   = ((P_W_A > P_W_D) ? P_W_A : P_W_D) + 1;
  localparam int N_CYC = (P_W_A + P_STAGES - 1) / P_STAGES;
  localparam int W_PAD = N_CYC * P_STAGES;
  localparam int W_CNT = (N_CYC < 2) ? 1 : $clog2(N_CYC);

  logic [W_PAD-1:0]    mag;
  logic [P_W_D-1:0]    dv;
  logic [W_R-1:0]      rem;
  logic [W_PAD-1:0]    quo;
  logic                neg;
  logic [W_CNT-1:0]    cnt;
  logic                run;
  logic                err_r;

  logic [W_R-1:0]      r_chain [0:P_STAGES];
  logic [P_STAGES-1:0] qbits;
  logic [W_R-1:0]      r_next;
  logic [W_PAD-1:0]    q_next;
  logic [W_R-1:0]      dv_ext;
  logic [W_R-1:0]      shifted;

  integer s;
  integer bitpos;

  assign busy = run;

  always_comb begin
    dv_ext     = {{(W_R - P_W_D){1'b0}}, dv};
    r_chain[0] = rem;
    for (s = 0; s < P_STAGES; s = s + 1) begin
      // cnt 的有效范围是 [0, N_CYC)，故 bitpos 落在 [0, W_PAD) —— 不需要下界保护。
      bitpos  = W_PAD - 1 - (int'(cnt) * P_STAGES + s);
      shifted = {r_chain[s][W_R-2:0], mag[bitpos]};
      // ⚠️ 真 bug 记录（2026-09-18，P2 单向测试抓出）：
      // 商是 **MSB 优先**产生的，所以一个时钟内第 s 级产生的商位，必须落到这一组
      // 7 位里的**高位**。原先写成 `qbits[s]`，于是 `q_next = (quo << P_STAGES) | qbits`
      // 把整组按位倒序拼进去了 —— 表现为"每个 7 位块内部位序反转"。
      // 只有当 P_STAGES == 1 时反转不可见（一位一组没有内部顺序），所以 P1 的模块查不出来。
      // 实测指纹：a = 0x4000_0000_0000_0001, d = 1 时 Q[62] 应为 1，实际是 Q[56]=1。
      if (shifted >= dv_ext) begin
        r_chain[s+1]                = shifted - dv_ext;
        qbits[P_STAGES-1-s]         = 1'b1;
      end else begin
        r_chain[s+1]                = shifted;
        qbits[P_STAGES-1-s]         = 1'b0;
      end
    end
    r_next = r_chain[P_STAGES];
    q_next = (quo << P_STAGES) | {{(W_PAD - P_STAGES){1'b0}}, qbits};
  end

  always_ff @(posedge clk) begin
    if (!rst_n) begin
      mag   <= {W_PAD{1'b0}};
      dv    <= {P_W_D{1'b0}};
      rem   <= {W_R{1'b0}};
      quo   <= {W_PAD{1'b0}};
      neg   <= 1'b0;
      cnt   <= {W_CNT{1'b0}};
      run   <= 1'b0;
      err_r <= 1'b0;
      q     <= {P_W_A{1'b0}};
      err   <= 1'b0;
      done  <= 1'b0;
    end else begin
      done <= 1'b0;
      if (start && !run) begin
        mag   <= {{(W_PAD - P_W_A){1'b0}}, (a[P_W_A-1] ? (~a + 1'b1) : a)};
        dv    <= d;
        rem   <= {W_R{1'b0}};
        quo   <= {W_PAD{1'b0}};
        neg   <= a[P_W_A-1];
        cnt   <= {W_CNT{1'b0}};
        run   <= 1'b1;
        err_r <= (d == {P_W_D{1'b0}});
      end else if (run) begin
        if (int'(cnt) == N_CYC - 1) begin
          // 最后一拍结算。q_abs 的高位（>= P_W_A）必然为 0 —— 见模块头论证 4。
          if (err_r) q <= {P_W_A{1'b0}};
          else       q <= neg ? -(q_next[P_W_A-1:0] + {{(P_W_A-1){1'b0}}, (r_next != 0)})
                              :   q_next[P_W_A-1:0];
          err  <= err_r;
          run  <= 1'b0;
          done <= 1'b1;
        end else begin
          rem <= r_next;
          quo <= q_next;
          cnt <= cnt + {{(W_CNT-1){1'b0}}, 1'b1};
        end
      end
    end
  end

endmodule
