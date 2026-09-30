# 可综合 RTL 入口

当前顶层是 `top/sar20_digital_core.sv`。`rtl_sources.f` 固定编译顺序，含 **22 个 core + 2 个 top 目录模块**；`params/` 的两个 include 头文件使生产源码总数为 26。`sadc_enc` 是编码器模块，完整 ADC 顶层只有 `sar20_digital_core`。

详细职责、状态、相位、配置、观测点和调试顺序见 [RTL_ENGINEERING_GUIDE.md](../docs/rtl/RTL_ENGINEERING_GUIDE.md)；综合与布线时序流程见 [FPGA_SYNTHESIS_AND_STA.md](../synth/FPGA_SYNTHESIS_AND_STA.md)。本入口解释当前实现；仿真、综合和时序结论以对应源码绑定的原始报告为准。

## 两种精化 profile

| 参数 | 模式 | 主要输入 |
|---|---|---|
| `P_STRUCTURAL=1`，默认 | 双 9-bit seeded SAR、共享 3-bit Flash、18-slice 池、前代 residue 的 12-bit fine SAR | Flash 温度计与 valid；coarse/fine 比较器 valid/data |
| `P_STRUCTURAL=0` | 既有码域向量兼容模式，phase8/14 捕获 | 外部 `sadc_code/sadc_rdy`、`adc2_code/adc2_rdy` |

结构模式已在 `sar_structural_ctrl` 内实例化 Flash 编码器。两种模式共享物理系数、配置和精确定点重构；profile 是编译期参数，不能运行时切换。

默认结构模式使用16拍帧：旧phase0沿转交前代上下文；1沿启动当前coarse及前代fine；8沿锁存coarse截止；9沿捕获当前residue；14沿锁存fine截止；15沿条件启动重构。注册模拟使能按 next_phase 生效，与“前沿事务”口径分开。首次pool advance只priming，不能要求配置后立刻输出。兼容模式有独立节拍表，不使用这一结构模式截止协议。

## 目录与职责

```text
rtl/
  rtl_sources.f                 24 个 SystemVerilog 模块的唯一编译清单
  core/
    analog_phase_ctrl.sv         注册相位/模拟宏使能
    sar_trial_ctrl.sv            seeded SAR试探与已决码
    sar_structural_ctrl.sv       双SAR/共享Flash/前代fine集成
    slice_pool_ctrl.sv           因果8-of-18池与priming
    cal_sample_context.sv        residue→fine物理上下文
    ctrl_fsm.sv, slice_alloc.sv  兼容模式控制/分配
    dem_state_gen.sv             A/B DEM状态
    dem_addr_gen.sv              二维主阵列与子阵列位置
    swap_decode.sv              计数/bridge译码
    dither_gen.sv               离散dither随机码
    unit_therm.sv               物理开关掩码
    rdac_drv.sv                 注册数字开关输出
    weight_store.sv             系数/完整位图/共享旧值读选/行和
    calib_regs.sv               标量/控制/validate
    cal_weight_reduce.sv        T/G/R物理归约
    adc2_dec.sv                 后端bin center/half-even
    cal_residue_mac.sv          精确定点MAC
    div_floor.sv                迭代floor除法
    cal_output_stage.sv         code/ID/flags提交
    recon_core.sv               快照与计算调度
    status_regs.sv              粘滞状态/错误
  top/
    sadc_enc.sv                 独立可测编码器；结构模式也使用它
    sar20_digital_core.sv        完整数字核
  params/
    rtl_params.vh               参数导出器生成物，禁止手工编辑
    rtl_error_codes.vh          静态接口错误码
```

## 配置和输出契约

所有顺序复位为同步低有效，配置、比较器和ready信号同属 `clk`；没有自动CDC。只有合法总线写且未生效、未提交、未clear、控制序列空闲时接受配置。

当前epoch须完整写入18×71权重及offset/min/max三标量，等待四控制位原子写完，再单独请求validate。合法权重为 `0<W<2^47`，ADC2 `min<max`，sampling与quantizer dither互斥。validate是检查请求；写冲突不会自动重试。配置生效后拒写。clear取消在途转换并清完整位图，保留旧权重/行和/标量读回，因此仍要完整重载。

`dout_valid` 是完成事件。必须同时消费 `dout_sample_id/dout_flags`；acc_ovf或gain_err时有效事件仍产生，但保留上次合法字。重构延迟为 `ceil(63/P_RECON_STAGES)+2`：P5/P6/P7为15/13/11完整拍，固定帧的启动间隔为16拍。

## 验证入口与证据范围

```sh
python tools/run_open_rtl.py
```

入口执行23bench（原22项及新增FSM见证）与结构核、兼容核、独立编码器3种严格lint，需要Verilator 5.x timing和C++20。单项选择和原始日志路径见工程指南。位宽、参数导出及向量漂移按 [RTL_ARITHMETIC_CONTRACT.md](../docs/rtl/RTL_ARITHMETIC_CONTRACT.md) 验收。

已归档的最终共享读选实现为 `71e7d5a`（26文件内容摘要前缀 `89b9f8153fea`）：原22bench/3lint、完整P5 XSim及真实25ns BUFG OOC路由分别保存于 [20260930证据入口](../docs/evidence/20260930/README.md)。该实测支持40MHz FPGA核心与2.5MS/s节拍；外部OOC hold仍不满足，ASIC640MHz及模拟性能尚未验收。排版与注释的新字节身份由当前报告交付清单记录，不能冒用旧运行的原始SHA。

RTL实现片外求系数后的片上数字校正，不包含片上LMS/RLS学习器。模拟宏、专利的完整电路与跨ADC tracking差距见 [FULL_AUDIT_20260930.md](../docs/rtl/FULL_AUDIT_20260930.md)；数字宏使能端口不能单独证明晶体管机制或论文指标。
