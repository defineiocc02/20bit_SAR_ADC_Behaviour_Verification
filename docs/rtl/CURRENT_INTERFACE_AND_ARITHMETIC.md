# 现行接口与整数算术合同：ABI2

> **ABI2接口复核。** RTL／校准owner已按实际源码核对字段、单位、fold与整数公式；本文据此整理8文件包、20模块清册和调用边界。合同说明支持域；指定运行的通过与未完成项另见[B表](../archive/digital_status_matrix.md)，不据此宣称模拟、工艺或任意协议通过。

旧[ADR0021](../adr/0021-structural-contract-calibration.md)和[算术合同](RTL_ARITHMETIC_CONTRACT.md)只保留af8／9bdf历史。当前没有旧748位固定包、P_STRUCTURAL兼容路径或0x1018模式别名。

## 1. 显式输入包

| 文件 | 内容与身份 |
| :--- | :--- |
| config.json／format.json／binding.json | 三份明确输入的原始字节；不补旧电容／阈值／码制或模式配置 |
| rtl_params.vh | 上述输入精化出的尺寸、格式、控制宽度、16相位和展开参数 |
| source_control_binding.sv | 同名module：由实际coarse、word／state、slice生成实际mask及已知公共项 |
| source_acquisition_binding.sv | 另一同名module：由acquiring word／state／slice生成两采集偏移 |
| packet_schema.json | calobs v2字段宽度／顺序、单位、geometry、format、fold与输入摘要 |
| rtl_input_manifest.json | 包内文件摘要、参数、规格及支持域，不表示原芯片电路已复现 |

`input_sha256`分别包含`config_json`、`format_json`、`binding_json`、`source_sv`、`acquisition_sv`五个key。两个SV文件各自与module同名，raw SHA分别绑定，不能合并为一个source hash。完整包由[export_rtl_params.py](../../tools/export_rtl_params.py)生成；缺文件、漂移或不支持的绑定必须拒绝。

当前结构核约束9位coarse、18个物理slice／8个活动slice、16相位、20位输出；后级位数、每slice的main／sub单元数和control／state宽度由包声明并受实际elaboration守卫限定。Fig.19示例为63+8、CW=6、SW=1、N2=12；2+1等数学夹具只验证协议／算术，不构成同精度ADC。

调用方责任：source绑定不得读取实际电容、增益或保持电压来补数字答案。代码检查类型、尺寸、范围、哈希及声明的支持域；这些检查不证明任意Python闭包或SV绑定的来源、物理正确性和原文符合性。

## 2. 两采集端口与同笔归属

| 事件／端口 | 合同 |
| :--- | :--- |
| source_control_valid／ready | 在advance接受沿为下一acquiring组冻结word／state；无valid不复用旧字为好样本 |
| acquisition_control_valid | 资格覆盖已登记acquiring控制、实际采集绑定、错误和数值范围；只在该资格下解释两端口 |
| acquisition_sadc_shift_q | signed V_BITS，归一化V/Vfs的Q(V_FRAC)加性偏移，作用于真正SADC采样输入 |
| acquisition_rdac_shift_q | 同格式，作用于同一实际采集组的RDAC保存输入／底板；不是当前转换或前代fine的端口 |
| quiet转交 | 读取旧acquiring控制为current，旧residue为fine；后续已决事件使用对应current字 |
| 实际RDAC加载 | 绑定须valid、错误0、数值合法，且known_common_q等于同笔冻结的RDAC采集偏移；接受的mask／公共项随residue保存 |

调用方必须使所有外部核心输入满足clk捕获沿的建立／保持；本轮没有STA／CDC证明，也没有自动CDC。核心rst_n同步低有效，disable／cfg_clear取消资格。注册TP／参考／RA使能不是门极级复位／evaluate时钟，真实保持、非交叠、参考与放大器建立由模拟宏独立验收。

默认工程schedule在旧phase0沿quiet转交，1沿advance，2–8消费已决RDAC更新，8粗截止、9residue capture、14fine截止、15条件启动重构。消费者读旧phase，NBA后安装新状态；重复已决码仍算独立事件。复位时phase0注册使能全低，pool与context须预热；具体沿关系以冻结源与实际事件记录核对，不照搬原芯片时序。

Fig.19受限静态绑定把六位字按`D6…D1`解释。归一化Q32单位下，粗码步长`Δq=2^24`，`U=4D6+D4`，`Lq=2D5·Δq+9586981D3+4793490D2+2396745D1`，`Tq=Lq+U·Δq`。两采集偏移分别为−Lq／−Tq，实际码和每个已决prefix都减U。负极性、4:2:1/7的Q32取整与18/8 common-voltage目标等效是工程假设。

RDAC候选码必须在0..511内；最终码合法不能抵消早事件非法，非法mask不加载，坏资格不恢复成好residue。默认工程模型的SADC／ADC2按half_open判断电压范围，等于上端点会返回末码并保留越界；RA达到±限幅即标饱和。它们与RDAC整数事件拒绝、最终20位码的clip是不同检查，不能用最后一个标志推断前面节点全部有效。

## 3. calobs v2

从MSB到LSB的字段顺序如下，slot0及unit0按schema处于各打包数组低位。payload使用hex_msb_first；软件必须按实际schema解包，不能猜旧位域。

| 字段 | 位数 | 单位／用途 |
| :--- | :---: | :--- |
| sample_id | 32 | 原始样本身份；不能用输出序号替代 |
| coarse | 9 | 本笔实际SADC决策 |
| state | SW | 绑定声明的数字状态 |
| control_word | CW | 本笔采集时冻结的原始控制字 |
| adc2_code | N2 | fine原码 |
| known_common_q | VB | signed归一化V/Vfs，等于实际绑定的RDAC采集偏移 |
| slice_ids | 5A | A个有序物理slice ID |
| main_masks | A·NM | 实际已接受主mask；1高轨、0低轨 |
| sub_masks | A·NS | 实际已接受子mask；0不表示电容未参与 |
| analog_bad | 1 | quiet时的analog_bad与旧residue_bad合并后，随本笔fine context冻结的模拟异常资格 |

总宽度`32+9+SW+CW+N2+VB+5A+A·NM+A·NS+1`。Fig.19的[32,9,1,6,12,64,40,504,64,1]为733位；2+1／CW2／SW3／N2=6的数学夹具为181位。它们是两个明确实例，不定义原芯片观察宽度。

观察在reconstructor接受样本的同一沿有效；reset／epoch clear优先取消。analog_bad不是控制错误的完备编码；绑定坏资格通常抑制有效packet，由conversion_fault／status另报。报文不含a／电压项数组、模拟C／G／heldV真值、配置epoch、ready、FIFO或外部输送。解析器要求调用方显式声明捕获期间配置／权重／binding固定，并校验binding与schema的SHA及ID顺序。packet没有epoch字段，这些检查不能证明硬件epoch未变；硬件捕获／带宽、独立参考、缺样与epoch仍由系统提供。

## 4. 数字观察与整数算术

静态物理关系为`F_true = Σ W_i·(a_i·x_ref+t_i)+O`；数字观察使用`F_hat=ADC2名义码仓中心=F_true+量化残差`，不将码仓中心当作精确模拟真值。W_i是有效C_i/Cf，a_i为明确Signal资格；t_i是由实际数字连接与显式名义参考绑定得到的已知项`b−VD_nom`，受控静态关系须另证明适用。x_ref由独立已知输入给出；学习器不读模拟电容、真实增益或保持电压。

一般Python观察可以明确给a／t；当前RTL与calobs恢复只接受已声明的allSignal、对称±Vfs、共同采集偏移fold。此时`a_i=1`，归一化整数项`term_i_q=I+(1−2·mask_i)·2^V_FRAC`，I为known_common_q。每unit项在所声明舍入规则下必须精确恢复；不能仅证明某一权重加权总和近似相等。

| 项 | 当前RTL支持子集 |
| :--- | :--- |
| 权重W | 正有效C/Cf，Q30／48位存储，配置要求0<W<2^47 |
| 电压F／O／I | signed64位Q32，归一化V/Vfs；F／O属于RA输出域，I属于RDAC采集输入域 |
| 系数量化 | 系数及明确电压项round-to-nearest、ties-to-even；固定系数只量化一次 |
| ADC2码仓中心 | 在声明min／max、N2下用整数half-even求中心；原模拟越界与数字译码标志分开 |
| 中间结果 | 96位有符号受检域，超界报错，不回绕；迭代除法负数向负无穷取整 |
| 输出 | floor量化到20位offset-binary并饱和[0,2^20−1]，码中心解码；不声明20位ENOB |

下式W／F／O／I均为量化寄存器整数：W包含2^30比例，F／O／I包含2^32比例，与前面的实数观察式区分。令T为本笔活动权重和、R为`ΣW−2Σ(W·mask)`、G为`Σ(W·a)`，则当前fold下G=T。整数分子`N=(F−O)·2^30−R·2^32−T·I`，输出未饱和字为`floor(((N+G·2^32)·2^20)/(2·G·2^32))`。Fig.19的upper已在实际mask的k=c−U中体现；I=−Tq对应真实已冻结采集偏移，不再额外扣一次U。

此处权重和T与Fig.19的total电压T是不同对象；实现字段分别为权重归约量和known_common_q，使用时按单位辨别。完整规格、ADC2窗口／端点和观察域须一致。FrozenCalibration模型文件采用schema2并拒绝旧schema1模型；现行定点寄存器镜像仍使用独立schema1，并携带完整的新CalibrationSpec，两者不能混称版本。静态资格只支持已经被核验的关系，不把动态、漂移或ADC2非线性放进同一验收。

## 5. 退休、故障与观测范围

每个结果同时提交code／sample ID／五flags`{clip_high,clip_low,adc2_ovf,gain_err,acc_ovf}`及dout_analog_bad。acc／gain异常可保留上次合法码而仍有本次退休事件；valid不是无错承诺。sticky状态和encoded error不是逐笔历史队列。

conversion_fault以有效核心配置下的controller_error或CAAZ pair fault产生，独立于merged status总线优先级，经system sar_conversion_fault进入DSP live／sticky锁停。无raw不生成虚构rejected ID；之后good raw可用真实ID和原flags0被拒。DSP restart不能越过持续的上游fault。已验证的恢复顺序是core epoch clear、完整重载并重新预热；该顺序会取消FIFO／滤波，不保留在途记录。

配置运行中禁止改写；cfg_clear撤销ready、完整位图和在途／pair，保留系数／行和数值但下一epoch仍须全重载。0x1018旧模式地址不存在，source word是采集边界输入，不是旧四控制bit的改名。

Python静态producer只执行其N个实际样本，case格式兼容不等于SV捕获。实际RTL有限窗另声明N+3程序行、trace中N+2物理采样、前N个目标中expected_good笔退休及3条未退休程序尾；正常例expected_good=N，命名prefix负例N=6只有4笔正常退休。不能伪造Python尾样本混算。有限观察结束、DSP pending0或fault锁停都不证明真实停采、排空、park或模拟idle；这些仍OPEN。

两位owner对本页字段、整数规则的复核与最终联合运行分开。实际案例的正常／负例、完整状态、源／工具／输入／记录身份见[B表](../archive/digital_status_matrix.md)及公共producer／same-plan verifier；源码owner的ABI冻结不等于全部实验或物理层级通过。
