# SAR20与并行DSP接口

> 本页记录真实SAR→DSP接口。handoff003固定15源及限定数字系统运行；[复现输入](tooling.md#复现指定的14个场景)和[逐模块状态](../../docs/archive/digital_status_matrix.md)可查。完整SAR输入包与算术见[ABI2合同](../../docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)。

[sar20_system_top](../rtl/sar20_system_top.sv)连接SAR退休口和[sar20_ad4080_top](../rtl/sar20_ad4080_top.sv)。两个source模块负责SADC／RDAC采集偏移、实际mask与已知公共项；DSP不从输出码补造这些信息。

| 接口事项 | 调用合同与实际行为 | 尚需系统验收 |
| :--- | :--- | :--- |
| 时钟／复位 | sar_*与控制同clk；SAR复位同步低有效，DSP局部多为异步置位，系统须提供安全释放并让SAR观察所需复位沿 | CDC/RDC、复位宽度与实际宏时序 |
| 码制 | SAR及适配层并行输出20位offset-binary；DSP内部补码，两个边界翻转最高位 | 真实数据接口的编码与PHY |
| 入口接受／拒绝 | sample_accepted／sample_rejected及ID只在输入事件有效；没有ready／反压或事件队列 | 软件可读事件捕获及丢样合同 |
| 数字flags／controller fault | 五flags或模拟sticky、`sar_conversion_fault`live门控并锁stream_fault；core conversion_fault以controller_error或CAAZ fault产生，独立于merged status优先级 | 只覆盖已报告故障；任意未报告ID跳号不由此检测 |
| 模拟异常 | sar_analog_ovf为独立粘滞停流资格；SAR逐结果dout_analog_bad尚未替代该封装的保守sticky合同 | 坏样本ID、逐输出异常和恢复边界 |
| 故障与在途 | 故障live门控sample_accepted、输出valid和FIFO读，下一沿锁stream_fault；无raw不生成reject ID／flags，之后good raw以真实ID和原flags0拒绝 | 故障布尔不精确标出缺失sample ID；入口事件不能替代输出窗口标签 |
| 完整重启／epoch | reset／clear／restart优先清adapter；上游fault仍1时释放后live gate仍关闭并再锁。真实core epoch clear＋完整重载可恢复controller原因 | 会取消FIFO／滤波，非无损恢复；fault与pending0都不是模拟idle |
| SPI软件复位 | 以真实adc_idle、capture_incomplete及capture_io_active资格约束；配置复位保留已完成捕获记录／会话 | 非空记录跨真实停流／恢复的最终集成测试；不能用恒1资格替代 |
| 输出clip | correction_clip_low/high属于系统校正级同拍事件 | 抽取／FIFO后逐输出标签尚无 |
| 滤波／捕获 | 输出可聚合多笔输入或延迟读出；valid代表数学输出，未设置旧settled为器件ready | 窗口、源ID、flags及系数版本元数据 |
| 外部资格 | adc_idle与interface_ready由系统提供；缺来源参数以cfg_reject／processing_reject等显式拒绝 | 实际ADC状态、上电、复位与主机过采样时序 |
| 数据接口 | 并行处理／捕获读口已有实现；配置SPI不是结果数据SPI | 数据PHY、连续吞吐、CNV转换启动与电气时序；旧lvds_framer已退役 |
| calobs v2 | SAR独立调试包按schema导出，DSP结果FIFO没有接收此包 | 包宽预算、epoch、受控捕获、缺样和独立参考对齐 |
| CAAZ | system固定P_CAAZ_ENABLE=0；fault布尔含pair故障的接线不等于实际组合运行 | 显式raw／pair元数据、模拟保持／H/K与CAAZ＋DSP仍OPEN |

系统编译依赖20个主清单模块、15个DSP模块、静态错误码头和显式8文件包；不要用全目录扫描补齐未知依赖。接口验证必须实际执行并绑定源、参数、输入与所有结果标志，不把文档当测试通过证据。

40 MS/s是目标时间轴；16相位对应的640 MHz只是预算。当前没有该系统的目标工艺综合／STA、PHY或完整跨域签核。

本轮限定系统实测：3个独立epoch的来源错误13／coarse错误7／fine错误8，每例随后4个真实good raw被拒；3次DSP restart仍阻断、3次epoch clear／重载恢复。系统185次20位比较、214次握手、15448项检查；这些是工程共同输入夹具，不是物理Fig.19 ADC宏或任意ID缺样识别的证明。
