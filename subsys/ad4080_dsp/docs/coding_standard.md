# RTL与注释维护约定

适用于当前ad4080_dsp源码及与SAR的接入。SV正文借鉴lowRISC的`//`组织方式，保持项目真实文件头；Python调用文档借鉴Google docstring，仅在需要时写Args、Returns、Raises、Attributes。完整SAR接口与算术另见[现行合同](../../docs/rtl/CURRENT_INTERFACE_AND_ARITHMETIC.md)。

## 注释与接口文档

文件头说明职责、时钟／复位、关键接口、单位、延迟／吞吐和使用边界。作者、日期和版本按真实Git／维护记录填写，不生成虚构团队、版本或验证结果。没有直接手册依据的内容明确标工程选择或假设。

普通说明使用完整自然段，不按逗号、每句话或短语机械分节。短标签与说明同行，例如`// enable: 撤销时清资格；重新启动需经过预热。`。参数使用`name: 说明`，短参数相邻排列；长说明按语义折行并增加一级缩进，保留标识符、数值与单位、条件与结论的完整性。中文须兼顾显示宽度。

实现旁的注释解释关键原因、算法关系、同沿优先级、启动和异常边界，不重复整段接口合同。短函数和显然赋值不套长模板；仅大功能区使用分隔线。调用方约定、代码实际检查与尚待外部验收分别说明。

| 对象 | 维护规则 |
| :--- | :--- |
| 名称 | 文件／模块／信号使用现有一致的snake_case，参数／localparam使用清楚的常量命名；不要为排版改动公共ABI |
| 数值 | 位宽、符号、扩展／截断、舍入和饱和明确；魔法数须有真实来源或计算解释 |
| 顺序与组合 | always_ff使用非阻塞赋值，组合逻辑完整赋值；无锁存、多驱动、组合环或casex依赖 |
| 时钟／复位 | 按模块真实声明；SAR同步复位与DSP局部异步复位不混为一套，释放／跨域由系统独立验证 |
| 保留注释 | 编译／综合／Verilator pragma、type-ignore、noqa、coverage、编码声明等语义注释不得按普通说明改写 |
| 注释专改 | Python去真实docstring后的AST／类型注释保持，SV功能token／指令／pragma保持；这不是形式等价或新仿真通过 |

## 显式工具与执行边界

```bash
# 从仓库根目录；工具需预先配置，文件及依赖由调用者明确提供
STRICT=1 bash ad4080_dsp/scripts/lint.sh \
  ad4080_dsp/rtl/sinc1_decim.sv
python tools/run_open_rtl.py --tops result_fifo_tb --out ../sar-output/fifo-check-001
```

lint.sh不选择默认套件：pyslang语法／elaboration与文本检查、文件头检查分别报告；可执行Verible存在才额外运行，否则记NOT_RUN。缺依赖或参数包不能算通过。完整核需要显式source输入包，不用裸目录glob伪造闭包。

语法／文本检查、smoke、bench对拍、形式验证、综合／STA和物理证据分别记录。缺工具不自动安装，不用预测／归档结果替代当前运行。具体参数和退出条件见[tooling](tooling.md)；实际发布门禁按当前任务范围和冻结清单执行。

## 变更与审阅

先读当前合同和实际源，改动前备份并固定输入／版本。实现变化同步维护来源与状态，必要检查覆盖行为与反例；注释变化保留技术含义和语义指令。冻结公共接口变化需留下明确迁移记录，不保留误导性的旧别名或隐式回退。

手册依据以[追溯表](ad4080_traceability.md)和原文位置核对，表格本身不是唯一真相。最终判断看实际源、输入、工具和执行结果；旧[9bdf规范](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/9bdf7708f42733896d7ead89935be41b4258fb6f/ad4080_dsp/docs/coding_standard.md)仅作历史。
