# 可综合RTL入口与显式绑定

> 现行20模块使用ABI2显式双绑定。指定静态DUT `full_typechecked_001`与同池重放见[数据](../docs/readme/data/source_bound_rtl_calibration.json)；有限窗口不证明停采、模拟建立或目标工艺。

当前完整数字核为[top/sar20_digital_core.sv](top/sar20_digital_core.sv)。[rtl_sources.f](rtl_sources.f)列20个模块：16个core、2个top、1个CAAZ control与1个CAAZ calibration；另依赖静态错误码头和导出包中的参数／两只source模块。`sadc_enc`是编码器，两个CAAZ模块为默认关闭的扩展。

[工程指南](../docs/rtl/RTL_ENGINEERING_GUIDE.md)解释当前流程，[现行接口与算术合同](../docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)已按实际代码经RTL／校准owner核对；[A表](../docs/archive/current_digital_source_map.md)给原文对照，[B表](../docs/archive/digital_status_matrix.md)给状态与证据等级。

## 完整核需要什么

| 输入 | 实际职责 | 边界 |
| :--- | :--- | :--- |
| 显式Config与format | 固定9位／18/8目标、ADC2、几何、Q格式、展开数等 | 构造Config不代表字段完整；不提供旧默认参数头 |
| `source_acquisition_binding` | 已冻结word／state和acquiring组对应的SADC／RDAC采集偏移 | 两个端口属于正在采集的组，不属于当前转换或前代fine |
| `source_control_binding` | 同笔已决粗码、word／state和实际slice生成实际mask与已知公共项 | 没有源3D DEM或旧SID／scalar dither回退；公共项须与该笔RDAC采集偏移一致 |
| calobs v2 schema及binding文档 | 定义实际包宽、字段顺序、单位与连接恢复 | 不包含模拟真值、epoch、FIFO或外部传输 |

导出器按明确输入生成8文件包：`config.json`、`format.json`、`binding.json`、`rtl_params.vh`、`source_control_binding.sv`、`source_acquisition_binding.sv`、`packet_schema.json`、`rtl_input_manifest.json`。文件名、哈希及模块ABI以最终冻结导出器为准；能生成包不等于已验证原芯片网络。

## 控制、配置与输出

只保留结构路径，不再提供`P_STRUCTURAL=0`旧粗细码兼容模式。16相位是工程调度：独立量化器与共享Flash形成9位粗码，RDAC跟随已决事件，上一笔残差进入fine。source word／state在新采集边界接受，随实际采集组冻结；无valid不会复用旧字为好样本。重复已决码仍是独立事件。

核心输入同属`clk`域，`rst_n`同步低有效；比较器valid／data必须满足捕获沿建立保持。注册TP／参考／RA使能只是模拟宏边界，不提供门极复位／evaluate波形、本地保持、非交叠或建立证明。

当前epoch须完整写入所有物理权重和offset／min／max三个标量后单独validate；运行中拒绝改写。`cfg_clear_valid`取消在途结果和配对、撤销资格并清完整位图，保留系数／行和数值供回读，但下一epoch仍须完整重载。0x1018旧四模式控制地址已退役。

`dout_valid`是退休事件，须同时读取ID、flags和`dout_analog_bad`。增益／累加器异常时可能保留上次合法码；粘滞状态不是逐结果元数据或事件队列。CAAZ启用时还需保留raw／pair归属，不能从标签推断真实AZ保持。

## 执行与当前证据

[完整核及组件复现](../docs/archive/REPRODUCING.md#5-显式导出与rtl组件执行)给出实际8文件导出、训练和同池重放命令。控制组件使用显式`--inputs-dir`、权重、三个标量和32 frames；`--tops`与库外新`--out`必填。数值或配对组件另需明确的格式或向量，缺项／缺工具时失败。

默认关闭的CAAZ扩展另有[现行8包数字组件](../docs/archive/REPRODUCING.md#caaz数字组件)及独立配对对拍，未证明真实AZ存储或H/K。

Fig.19受限静态等效已在指定Python／RTL有限窗对拍。source 3D DEM、完整dither电容／开关／噪声网络、实际比较器与参考时序尚未完成。16×40 MHz=640 MHz仅为预算。

原`71e7d5a`的22bench／3lint、XSim及25ns BUFG OOC资料继续在[20260930档案](../docs/evidence/20260930/README.md)保留原身份；其40 MHz FPGA核／2.5 MS/s及OOC hold限制不改，不作为当前ASIC时序或物理签核。
