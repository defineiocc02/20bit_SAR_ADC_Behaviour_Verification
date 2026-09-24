# RTL 结构与数字校正交付说明（2026-09-20）

本次扩展承接功能修复提交 `5082066`。默认顶层已接入结构控制和跨周期余差上下文，
数字校正拆为独立模块；旧向量通过显式兼容模式运行。
本报告区分可综合数字实现、文献披露、模型假设和待模拟/物理验证项。
详细来源与端口时序见 [ADR0018](../adr/0018-physical-calibration-and-structural-controls.md)。

## 1. 本次修复的重要结构问题

1. 原顶层只接收完整粗码，不能控制论文的双 SAR 与共享3-bit Flash。新增两套种子 SAR
   和独立后级 SAR 比较器握手；粗码试探与已决结果分离，RDAC 只接已决结果。
2. 固定两个 bank 无法实现18中动态选择。新池调度器记录实际 acquisition 归属，
   采样之后才晋升 conversion，从其补集选择下次 acquisition，两个备用 slice 真正参与。
3. ADC2 应校正上一笔 RA 余差，不能拿当前 slice、DEM、注入或掩码配对。
   新上下文槽将 sample ID、物理 ID、开关、dither 和 injection 整体传递。
4. 固定 bank 权重选择不适用于动态 slice。改为静态物理系数行与窄掩码选择，
   同时检测重复/越界物理 ID。重构控制、求和、乘加和输出分别建模。
5. 模拟开关不能直接由二进制计数器组合译码驱动。TP、参考和 RA 控制改为寄存器输出，
   粗参考到准确参考之间保留完整死区；MOS 级非交叠和时钟质量仍须模拟实现。
6. 量化器 dither 原先只有数字扰动，缺少配对的模拟命令。新增两个量化器的注入码，
   RDAC 减去同一笔注入；采样 dither 则保留原物理轨和有效输入权重口径。
7. 结果增加样本编号和单笔数字错误位，避免把“保持上个合法码”误判为本次合法转换。
8. 模块拆分后用 `rtl/rtl_sources.f` 统一源文件清单，更新综合示例依赖；
   两个顶层 profile 分别进行严格 lint。修复 Verilator5.020 检出的常量减法位宽问题；
   求和树采用独立生成节点，避免旧工具对聚合数组误报组合环，未关闭该检查。统一编译入口显式设置8192次展开预算，覆盖最大8191个静态节点，
   避免Verilator5.020默认1024次展开上限截断硬件树。

## 2. 模块与调试入口

| 模块 | 职责 | 关键观察/调节项 |
|---|---|---|
| `weight_store` / `calib_regs` | 完整系数镜像与 epoch 原子生效 | written bitmap、sum_all、cfg_ready、禁止运行时写入 |
| `cal_weight_reduce` | 物理掩码路由及平衡求和 | active、physical_on、sum_W、sum_Wa、rails、invalid_slice |
| `cal_residue_mac` | 余差、偏置、独立公共注入与增益乘加 | op1/op2/op3、num、any_ovf |
| `div_floor` | 带符号向下取整 | P_STAGES、busy/done/err、q |
| `cal_output_stage` | 输出码和元数据同拍提交 | sample_id、flags、保持旧合法码的条件 |
| `recon_core` | 校正事务控制 | stage_b、gain_s、rails_s、采样/配置撤销 |
| `cal_sample_context` | 当前 RA/上一笔 ADC2 上下文分离 | residue_valid、fine_id、fine_slices、fine_injection |
| `sar_trial_ctrl` | SAR 位试探和比较器握手 | seed、trial、resolved、compare_enable、cancel |
| `slice_pool_ctrl` | 18个物理 slice 的因果分配 | acquiring/converting IDs、PRNG seed、conversion_valid |
| `analog_phase_ctrl` | 采样、AZ、RA、参考控制 | P_CAPTURE、P_REF_ON、TP寄存器、死区 |
| `sar_structural_ctrl` | 结构控制集成与截止条件 | phase、coarse_ok/fine_ok、context_valid、错误码 |
| `sar20_digital_core` | 配置、模式选择与数字校正顶层 | P_STRUCTURAL、P_RECON_STAGES、接口状态 |

完整文件及编译顺序由 `rtl/rtl_sources.f` 给出。不要只复制 recon_core 一个文件，
它明确依赖三个校正模块、adc2_dec 和 div_floor。

## 3. 校正算法如何进一步获得可用系数

RTL 实现的是系数应用链。可靠的外部标定建议采用以下流程：

1. 固定一个可追溯的模拟宏版本，记录真实物理 slice/unit 编号、SAR/DEM 开关命令、
   ADC2 原始码、已知注入、温度和采样条件。物理单位索引不能随 DEM 置换重命名。
2. 在可追溯参考输入下建立线性观测模型：余差为每个单元系数乘实际输入/参考开关项之和，
   再加偏置。输入是标定仪器或标定激励的测量值，不能在运行校正时从 testbench 偷取真值。
3. DEM 和激励要让物理单元充分出现不同开关状态；检查设计矩阵秩、条件数和单元覆盖。
   1278个物理系数并不保证能被一条固定正弦唯一识别。未充分激励的共线系数应合并、
   施加有依据的先验或重新采集，不能仅凭训练残差小就宣布标定成功。
4. 先明确整体增益、参考幅度、后级增益和偏置的尺度约束。它们与所有权重同时自由变化
   会产生不可辨识解。本仓库权重为有效C/Cf（子单元含β），已经包含RA电荷增益；ADC2的
   min/max与offset取RA后电压并除以输入Vfs形成Q32，不能额外重复除以32。
   若另选去增益约定，必须同时一致缩放全部权重、F和O，不能只改其中一项。
5. 系数做正值约束、异常点处理与独立留出验证，量化到Q30后再跑同一组留出集。
   检查量化后的权重范围、总和、剩余增益、负数floor、边界clip和异常保持语义。
6. 将完整表与三个标量作为同一版配置写入，validate后冻结。记录标定数据hash、
   求解器版本、固定点格式和对应模拟宏版本，便于跨机复现。
7. 分别检查 DC/低频线性、多个输入频率、幅度、PVT 与动态交织杂散。
   静态权重校正不能消除采样时刻、带宽、参考未建立和比较器亚稳造成的动态误差。

当前没有添加未经文献支持的片上权重训练器。需要后台在线学习时，应另立算法契约，
明确激励可观测性、收敛、系数冻结/双缓冲和误更新保护后再实现，不能把纯误差累加称作校准。

## 4. 验证方法与结果口径

- `sar_trial_tb`：全部4096个后级码和512个粗码，比较器延迟/停顿、完成脉冲、取消。
- `calibration_physical_tb`：2048组带物理系数差异的事务覆盖18个 slice；256-bit
  独立整数 oracle 检查权重、采样轨、偏置、注入、floor、clip、sample ID 和重复ID拒绝。
- `calibration_recovery_tb`：由真实失配系数与已知输入反算模拟余差，再独立量化为ADC2码。
  采用有效C/Cf总增益32、RA后ADC2量程和12-bit量化；2048个样本中，
  真实权重校正最大误差4个输出码，标称权重对照504个码。
  该夹具的后级量化预算为±4码并留1码边界裕量；这是算法恢复测试，**不是芯片INL/DR指标**。
- `structural_adc_tb`：仅用顶层模拟控制端口响应SAR试探，按真实RDAC引脚求解校正期望。
  覆盖关dither/采样dither/量化器dither三种模式、全部18 slice、参考与AZ互斥、
  缺少Flash/粗比较器/后级比较器结果后的恢复、完整配置与clear撤销。
  量化器dither实际加到Flash/比较器夹具后，由RDAC减去；逐位检查已决前缀，不能误接试探码。
- 原 P1/P2/P3 及协议回归继续运行，包括1,048,576码整数oracle与4095行旧端到端向量。
  旧向量显式选择兼容profile，不能把它们冒充新结构的模拟电路验证。

本地最终结果：15个SV测试台、3组严格lint通过，Python 572 passed / 3 deselected；
ruff/format/mypy通过。两个隔离负对照在成功编译后检出了“系数错误别名”和“RDAC使用试探码”。

工具兼容性复核：本地由Verilator v5.020源码构建的前端运行全部15个测试台通过；
仅为AppleClang适配C++20/coroutine运行时，没有修改编译器逻辑。新版本前端此前全量通过，
最后修改的恢复夹具及P2/P2 oracle再次通过。恢复夹具按事务一次性提交完整packed总线，
避免旧前端漏传播协程内逐元素赋值；P2诊断字符串使用`%s`。原生Linux5.020结果以当前PR CI为准。

最终执行结果及源文件hash见同目录 `evidence/structural_calibration_20260920.json`。
日志由 `tools/run_open_rtl.py` 生成到 `sim/artifacts/open_rtl/`，本地和CI使用相同入口。

```bash
python tools/run_open_rtl.py
PYTHONPATH=src pytest -m 'not slow' -q
```

## 5. 综合与后续电路仿真的实际限制

1. **权重存储仍是可复位寄存器阵列。** 18×71×48=61,344个数据位，另有写入bitmap、
   累加器和控制。它不是已映射 SRAM。接入宏存储前必须设计并行读/局部求和带宽，
   不能简单换成单口RAM而保持一个样本/16拍。
2. **静态系数、掩码路由是一种面积/布线权衡。** 避免宽权重交叉选择，但物理求和叶子
   增至18×71，补齐2048叶，深度11级。是否减少面积/达到时序必须看映射结果；
   下一步优先比较按物理行局部流水、装载期缓存行权重总和、分块存储的PPA。
3. **余差乘加仍有宽乘法及多级加减。** 当前保留精确溢出契约，不通过缩窄掩盖问题。
   若需分段流水，必须同时移动gain、异常位和sample ID，再重新预算16拍吞吐。
4. **除法展开深度是主要时序旋钮。** 默认一拍7级恢复步骤，不能凭仿真得出640MHz。
   可比较P_RECON_STAGES=5/7/9；减小展开后延迟增加，不能只报告面积收益。
5. **模拟边界仍需具体约束。** 比较器valid/data输入延迟、RDAC/参考输出负载、
   TP时钟树与门极延迟、reset释放、CDC和时钟不确定度不能留作工具默认值。
6. 当前同步节拍没有复现论文约7ns粗转换、完整采集窗、<1ns Flash返回及20-bit参考建立。
   建议下一步以控制端口驱动带电荷守恒/参考动态的模拟宏；先做无噪声直流/斜坡，
   再加入失配、建立、热噪声和抖动，并从实际输出测频谱/线性指标。
7. 本轮没有获得新的DC映射或布局布线报告；EDA主机读取连接超时。
   原论文40nm、现有库28nm的结果必须分开标注。lint、RTL仿真和脚本测试不等于PPA签核。

可运行的映射入口（需可用EDA主机与库配置）：

```bash
python synth/run_synth.py --name structural_calibration --top sar20_digital_core \
  --files rtl/core rtl/top --incdirs rtl/params --clk-period 1.5625
python synth/run_synth.py --name calibration_core --top recon_core \
  --files rtl/core --incdirs rtl/params --clk-period 1.5625
```

交付包含可核验的数字控制与校正实现、来源映射和回归；没有把未披露的电容分段、
专利替代实施例、晶体管尺寸/版图以及模拟性能补写成“已经全量复现”。
