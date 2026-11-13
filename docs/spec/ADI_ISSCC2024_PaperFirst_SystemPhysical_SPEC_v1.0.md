# ADI ISSCC 2024 §9.8 20-bit/40-MS/s ADC：论文优先、专利约束的系统与物理行为规格（v1.0）

**性质：工程实施规格（可编码）；不是原厂晶体管网表或已证实的完整芯片电容规格。**  
**建立日期：2026-11-13。目标分支：`merge/adi-repro`。**  
**优先级：ISSCC 论文明确披露 > 同场 PPT 补充 > 选定授权专利实施例 > 明确标记的工程假设。**  
**用途：阻止 Agent 从端点、INL 或 DR 反推电路；冻结核心系统架构、定义各端口与节点、允许有边界的候选物理模型。**

## 0. 必须先读：三种“正确”不可混淆

1. **论文系统正确**：与 ISSCC 2024 Fig.9.8.1 的两级、双量化器、18 片池/8 片工作组、共享 RA/ADC2 的关系一致。
2. **选定实施例物理正确**：与某一专利图/正文的实体电容、端子、开关、相位连接一致。专利包含变体，不能将它们无说明合并。
3. **数字结果正确**：给定已证实的物理网络，量化/DEM/dither/校准与数字重构正确。端点闭合、512 码单调、INL 小不能代替前两项。

任何未确定但必须运行的电气细节，使用显式 `ASSUMED` 参数和独立 profile；不得标 `PAPER`。

## 1. 原始证据清单与归属

| ID | 来源 | 确切证据 | 类型 |
|---|---|---|---|
| P01 | Bodnar 等 ISSCC 2024 §9.8 正文第1页（印刷p182）和 Fig.9.8.1（第2页印刷p183） | 两级SAR；量化器/RDAC分离；18 片池、8采集+8转换+2备用；两量化器、共享RA及ADC2；前级9b，3b Flash，32×标注 | PAPER |
| P02 | 同论文 Fig.9.8.2 和正文 | 共用关键采样边沿、同址时钟、低压快速采样FET、孔径误差抑制 | PAPER |
| P03 | 同论文 Fig.9.8.3 与正文 | 粗预充参考用于 SAR bit trials，精确 REF_IN 在 RA 阶段接入，RA 开始时参考未完全稳定；二维单元打乱示意 | PAPER |
| P04 | 同论文 Fig.9.8.4–9.8.6 | 40 nm、40MS/s、±3V差分、20b、DR94.2dB、NSD9.3nV/√Hz、典型INL+2.2/-1.8ppm、DNL±0.27ppm、SFDR119dB | PAPER |
| S09/S10/S12 | PPT 第9–12张 | RDAC follower、18片池、双量化器与共享RA；3b Flash/9b前级和并行结构 | SLIDES |
| S19 | PPT 第19张 | 片间offset/gain、带宽和时偏分离；15× f_NYQ是前端DAC采样带宽，不是ADC2采样网络带宽 | SLIDES |
| S27 | PPT 第27张 | RDAC kT/C标注20.5 pF；粗/精参考阶段；REF_IN电流曲线 | SLIDES |
| S31 | PPT 第31张 | 18选8；3b横向和3b纵向、二进制→一元跨段；DEM与dither分工 | SLIDES |
| S35 | PPT 第35张 | gmR+OTA RA、普通AZ、ADC2宽/窄带采样切换 | SLIDES |
| PS | US10516408B2（授权版） | 可匹配的采样 slice、SAR量化slice/多个残差slice、控制可随已决码更新、可选31或63单元例子 | PATENT_OPTION |
| PD | US10511316B2（授权版）Fig.11–21与说明 | 主/子分段、S4真实1C单元、子DAC不参加输入采样、可选B5–B7跨片分配、两级shuffler、保留列与MUX/gates | PATENT_OPTION |
| PT | US10505561B2（授权版）Fig.19与正文 | 六位dither例子，七片分数样值、分路位组、前级码调整与后级剩余去扰；另有Fig.20不同实施例 | PATENT_OPTION |
| PI/PA/PR | US10707889B1、US10541702B1、US10826519B1 | TRACK、AUX、参考电路可选设计；**不自动证明论文芯片使用了这些专利全部实施例** | PATENT_OPTION |

附图本地相对路径：`figures/Fig_9_8_1_ADC_architecture.png`、`Fig_9_8_2_sampling_switch.png`、`Fig_9_8_3_ref_DEM.png`、`Fig_9_8_4_INL_DNL.png` 和 PPT 图。图片仅用于用户授权的本地研究审阅，不要自动上传公开仓库。

## 2. PAPER_CORE：论文级系统规格（主工程不可修改）

| 字段 | 冻结值/约束 | 证据级别 | 注意 |
|---|---|---|---|
| `resolution_out_bits` | 20 | PAPER | 20b为最终输出位数，不等于 ADC2 位数 |
| `sample_rate_hz` | 40_000_000；`period_s=25 ns` | PAPER/推导 | 40 MS/s 的数据率；并非每个内部动作都持续25ns |
| `process_nm` | 40 | PAPER | 行为模型不自动拥有真实PDK |
| `input_diff_peak_v` | 3 V，差分输入 ±3 V，跨度 6 V | PAPER | **不代表单侧底板驱动为±3V** |
| `input_diff_lsb20_v` | 6/2^20 ≈5.722 µV | 推导 | INL ppm FS ↔ LSB20需明确跨度 |
| `n_physical_rdac_slices` | 18 | PAPER | 不是 18 个时间交织独立输出ADC |
| `n_active_rdac_slices` | 8 | PAPER | 8片同一笔组合构成一个 RDAC |
| `slice_residency` | 8 ACQ、8 CONV/RA、2 SPARE | PAPER | 每笔选片身份随 DEM/交织变化 |
| `n_first_quantizers` | 2 | PAPER | 两条错相前级量化路径；Flash可共享 |
| `first_stage_resolution_bits` | 9 | PAPER | 3b flash参与；其余SAR试探次数请勿凭推测宣称 |
| `flash_bits` | 3 | PAPER | 参考正文，不等于Flash独立产生整个9b |
| `ra_nominal_gain` | 32× | PAPER Figure 9.8.1 | 真实增益由信号网络/Cf定义，有校准与动态误差 |
| `second_stage_adc_exists` | 是，共享 | PAPER | **ADC2具体12/14位不是论文明确参数** |
| `rdac_follower` | 随前级已决结果更新残差DAC | PAPER | 禁止查看最终码生成历史命令 |
| `reference_scheme` | 转换粗预充，RA前转精确REF_IN | PAPER | 动态精参考与一个普通静态Vref不同 |
| `dem_scheme` | 18选8＋跨片/片内二维，配合dither | PAPER/SLIDES | 详细开关位图必须选择特定专利实例 |
| `calibration` | DAC权重片上应用；系数片外求得 | PAPER | 不指定LMS、SVD或特殊校准态 |
| `az_scheme` | 普通RA AZ，非CAAZ | SLIDES | CAAZ始终在 extensions/caaz |

**论文性能参考，不作为调参约束**：INL典型 +2.2/-1.8ppm（正负端不同），DNL约±0.27ppm，DR94.2dB，低频SNR93.7dB，1MHz SNR92dB，SFDR119dB，NSD9.3nV/√Hz，核心50mW、系统69mW。对比时应列源文件/测试点/温度/输入频率；达不到必须如实报告。

**不能声称为PAPER的项目**：ADC2恰好12bit、具体ADC2输入码制、3b Flash后恰好6个SAR时钟、S4与末端1C的所有相位底板连接、20.5pF是每差分侧还是总和、精确VREF_H/L电位、Cf实物尺寸、各子相位ns数值、32×是否逐P/N端使用统一器件比、TRACK/AUX专利全部被芯片采用。

## 3. 电压与节点坐标定义（强制）

**用户/系统接口差分输入**：`vin_diff_v = VINP - VINN`，约束在[-3,+3]V；`vin_cm_v`为独立输入。各支路实际底板输入只能是 `vin_p_v = vin_cm_v + vin_diff_v/2`、`vin_n_v = vin_cm_v - vin_diff_v/2`（适用于所选对称输入定义），不能直接在两侧都接一个 `vin_diff_v`。

**参考**：`vref_high_p_v, vref_low_p_v, vref_high_n_v, vref_low_n_v`为真实单侧轨电位。`vin_diff_peak=3V`不意味着 `vref_high=+3V, vref_low=-3V`；原文未给出实际值时，必须独立命名、设置 `ASSUMED`，验证差分跨侧极性与器件电压限制。

**电路节点**（每支路）：`main_top_v`、`sub_top_v`、`main_bottom_v[p,unit]`、`sub_bottom_v[p,unit]`、`ra_sum_node_v`、`ra_out_v`、`adc2_in_v`。物理 `C`、输入折算 `V`、量化输出 `code`三类变量不得混用。

**正负支路组合**：先每侧求电荷/节点响应，再在定义好的极性下做差分；**不能直接把一个单侧两节点oracle的输出称为整机差分RA输出**。

**信号输入采样**、**转换参考驱动**、**固定终端电容**分别表示：某器件是否存在、采样期底板连接、转换期底板连接、是否响应某数字位。所有字段必须分离，禁止把固定/无位直接设为零电容。

## 4. 物理实体清单：`PATENT_FIG11` 专利候选，不得冒充原芯片完整网表

下表是 **US10511316B2 Fig.11** 下的可编码参考候选（不是从原芯片显微照片反演出来的网表）：

| 位置 | 名义实体与权重 | 采样VIN | 转换期作用 | 证据 |
|---|---|---|---|---|
| Main 0…14 | 十五个2C单元，共30C | 选定参考候选中接VIN | 横向温度计/轮转，所选码驱动开关 | PATENT |
| Main B4 | 一只1C | 采样主阵列 | 对应B4或实施例规定的混合一元编码 | PATENT |
| Main S4 | 另一只1C，**真实物理电容** | 主阵列采样（与选择的顶板/采样方案一致） | 可随跨边界/纵向编码切换；不能永远称padding | PATENT |
| Sub binary | 8C、4C、2C、1C | **该专利实施例不参与VIN采样** | 处理对应小权重，未被跨边界编码移走者可用 | PATENT |
| Sub termination | 图中无独立位号的额外C（总数是否为5据原图核对） | 不将其默认为VIN采样 | 其连接及是否形成终端需从图/正文逐端确认；工程候选可以固定REF_LO | 图形证据＋ENG连接 |
| Bridge | 主/子顶板之间的Cb，名义1C作为一个候选 | 不为一个数字数据位 | 决定子节点耦合衰减 | 专利图＋ENG取值 |
| Parasitic | Cp_main、Cp_sub | 依节点存储 | 进入相应节点矩阵与动态响应 | ENG/PDK |
| Feedback Cf | RA反馈器件 | RESET/AZ取明示初态 | 反馈电荷→RA输出 | PAPER架构＋ENG |

若采样主网络包含`15×2C+B4×1C+S4×1C`，其名义物理主阵列电容和为 **32C**；但**码本可切换电容总和、有效输入采样C、参考驱动C并不自动等于32C**。不能强制512码的端点等于±3V反推删除S4。

对默认候选若子阵列包含8+4+2+1+1=16C、桥=1C、Cp_sub=0，在某个理想浮置子节点边界下，

`beta = Cb / (Cb + sum(C_sub) + Cp_sub) = 1/17`。

这是该**具体连接及电容数值**下的推导，不是论文给出的所有电路通用值。如果第五个C未实装，beta会变化，必须作为**不同拓扑profile**，不能仍命名原版Fig.11。

**关键编码要求**：Main S4可受来自子段的位编码控制。US10511316B2 Fig.14/15举例把B5/B6/B7分配到各slice的S4；Fig.16/17/19/20又给出其他两级打乱版本。**同一转换不得把Fig.14、Fig.17、Fig.20的不同控制细节混成一个未核对的映射。**

**关于 `raw.sub_masks (N,8,5)` 与 `chip.C_sub (18,4)`**：这是当前软件接口与已装配电容的分歧。用户批准的目标 `PATENT_FIG11` profile不得静默 `[..., :4]`，更不得用`2/2^30`伪造第5只电容。应使 physical device list 与端口一一映射：真实固定电容有实体和连接、无开关位；纯padding由`present=False`且不进入任何电容求和。

## 5. 唯一允许的电荷与RA基础方程

### 5.1 正确的物理计算顺序

`器件及节点→采样期相位和实际底板→保存自由节点电荷→转换开关切换→对真实节点写KCL→RA反馈输出→输入等效电压→数字重构`。

**不可逆转顺序**：不得先用码本目标`vin-vdac`计算“正确余差”，再把实际库仑按`Cconv/Csamp`缩放以制造它。

### 5.2 一般性节点矩阵验收

对物理导体节点i，电容支路连接两个端子i/j时，节点电荷为 `q_i=Σ_j C_ij (V_i-V_j)`。对保持浮置的节点，其采样结束电荷与转换阶段电荷应满足 `q_i,conv=q_i,samp + Q_injected,i`，外加明确已建模的reset、charge injection和RA反馈路径。实际控制为理想虚地的主节点，需将反馈电容与输出未知量一起求解。**采样时主/子节点的初始电位不得无证据一律设为0。**

特殊、明确约束下（主节点在RA相位为理想虚地、子节点浮置、所有电容线性、给定采样/转换底板、反馈电容初始值已知），可用以下两种算法交叉检查：

`beta = Cb/(Cb+ΣCs+Cp_sub)`，

`Q_feedback = Σ Cm(Vbottom_sample−Vbottom_convert) + beta·Σ Cs(Vbottom_sample−Vbottom_convert) + Q_initial/extra（若存在）`。

对零初态、无额外注入、符号与这一定义一致时：`Q_feedback = Cf * V_RA`。单侧符号需与实际反馈方向和差分组合一致。

注意：**该闭式式子不自动适用于非零子节点初始电位、实际不同采样相位拓扑、开关动态、有限A0、差分共模耦合**；这些情况直接使用节点矩阵，或推导增加初态项。

### 5.3 4个必须区分的电容

1. `C_sample_signal`：真正对VIN产生采样响应的有效系数，视连接与节点边界定义；
2. `C_phys_total`：所有物理电容（主/子/桥/寄生分类列出），不能当作一个信号增益；
3. `C_reference_load(t)`：参考切换真实见到的等效时间负载，与开关事件有关；
4. `C_feedback`：RA电荷转移增益的反馈电容。

`20.5 pF`从PPT第27页是大RDAC/kT/C量级；论文未明示它是否每差分侧、哪一个等效口径。若仅作条件演算，`20.5pF/(8×32)≈80fF`为每侧8片、每片32C主阵列都对VIN响应时的名义1C，**不是已确认的原芯片版图单元值**。禁止自动用于CF和DEM数组。

### 5.4 禁止行为

- 不允许 `physical_q_c *= (C_convert/C_sample)`，除非已把`physical_q_c`重新定义为非物理、显式带单位的等效坐标（且这个值不得再用于物理电荷守恒）；
- 不允许负载、端点偏差归因于未建模第五C后，删掉它来让码本正确；
- 不允许错误配置静默clip、删坏样本；
- 不允许 `residue_charge_c` 在静态路径表示某种等效C、在动态路径表示另一种等效C。

**强制字段**：`sampled_node_free_charge_c`, `conversion_node_charge_c`, `feedback_charge_c`, `ra_output_v`, `input_referred_residue_v`，分别给出精确定义、符号及域。可保留旧字段作短期只读适配，但其真实含义必须准确重命名。

## 6. 系统时序与控制合同

- 40MS/s→采样输出周期25ns；选中8片可采集长达完整系统周期，但是否完全25ns需据图/控制边缘确认；不能再用每通道50%采样盲算输入带宽。
- 两条前级量化器错相运行，避免一个已完成量化器返回采集时扰动另一个RDAC采样；共享一个RA和ADC2；输入采样和后级余差采样尽量在同一安静时间窗口。
- 一笔样本包括 `sample_id, epoch, physical_ids[8], quantizer_bank, acquisition_start/end, conversion_events, ra_event, adc2_sample_event, valid`。所有记录同样本归属。
- follower：RDAC第k次更新只能使用k次及此前真实已决判决，不能后验用最终粗码生成事件。最终码路径和事件路径分开验证。
- 18片任一时刻不得重复被两个采集/转换所有者占用；8采集/8转换/2备用是资源状态，不是8个子ADC。
- 参考：SAR trial阶段接粗预充，开始RA前切到精参考；RA开始时允许残余参考建立，最终ADC2采样时达标。动态参考和RA状态必须耦合，不可用一个静态低误差数直接抵消。
- ADC2输入采样宽/窄带切换，普通AZ独立于CAAZ；不可将PPT前端DAC `15×f_NYQ`误放到ADC2宽带参数。
- 子相位精确ns值、比较器阈值/冗余次数、ADC2位宽和输入范围若无法取证，全部在 `engineering_assumptions` 带假设标签，不得写PAPER。

## 7. 数字机制合同（选择实施例，分级开发）

### D1. DEM（选定Fig.19/20或Fig.16/17，不能拼接）

1. 输入第一阶段粗码（名义9b）和已冻结物理片ID；
2. B0–B3在主阵列执行二进制→一元，再横向打乱；
3. S4和可能用于跨边界的B5/B6/B7按照**所选实施例**纵向编码；
4. Fig.19/20变体：shuffler350/352、保留的0输入、真正物理列选择、362 signal gate/MX切换，输出主/子真实单元命令；
5. 必须保证名义总权重（计入所选已知修正）不随DEM相位变化；物理单元选择与可达相位应覆盖，不能把slot累计冒充物理片统计。

**工程决议**：先只实现一种已选版本（默认`DEM_FIG19_20`），其他作为资料索引或明确实验profile；不创建并行生产算法。

### D2. Dither（Fig.19与Fig.20不同）

US10505561B2 Fig.19的六位示例取七slice的分数配置（**不是完整原芯片八片布图定律**）：
- 第一dither字全部用在采样residue DAC（Fig.19的222相关路径）；
- 第二值由`D5,D3,D2,D1`组成，在第一级ADC200侧使用；
- 第三值由`D6,D4`组成，按例从第一级数字输出扣减后控制RDAC；
- 第二值中残留dither需在后级数字结果处理；
- `D6`贡献4或0，`D4`贡献1或0；示例把低3位表示0～7的分数（分母7，实际配置范围0至6/7）；
- 7 slice例的5/7采用五片选`k+1`、两片选`k`。8片PAPER_RDAC不能无证明沿用1/7，需另建**一个命名为8片工程映射**，并验证全部开关与数字去扰等效。

`subtract_polarity`仅可作为**明确坐标转换**，不得因代码使用减号而改原图。选定Fig.19版时逐节点记录 `vin / quantizer injection / sampling DAC injection / coarse code shift / RA residue / ADC2 correction`。

### D3. 校准

论文明确的是**片外获得DAC权重系数，片内完成DAC权重修正**。编码实现必须先冻结可观测量、状态和系数格式，再选择数值算法；不得称某个LMS/SVD为原论文指定。

保留“逐码LUT”和“逐物理权重”两条**不同策略**；不要维护两套意义相同却特征不同的`fit_element_weights`/`fit_unit_weights`。

训练只能读取合法数字观测、已知测试输入与配置；不可读取制造真值和隐藏模拟电荷。训练/重构必须一致处理物理片活动掩码、S4固定/可切换项、参考和样本身份。DEM开时训练的系数，不能无标识用于关闭时的分布；独立留出必需。

### D4. TRACK/AUX

作为`OPTIONAL_PATENT_STUDY`，不作为`PAPER_CORE`的必要完成项。若编码，TRACK必须检查真实来源就绪时间和预置因果，AUX必须在真实输入/采样充电节点出现，不能只输出理论电流减少倍率。

## 8. 最小实现范围与API（禁止新大型框架）

只维护**一个生产ADC主引擎**，保留现有`src/adi_model`目录布局。最小职责分离：

- `PhysicalTopology`：只存真实器件、节点、物理片、采样/转换相连接；明确可选profile；
- `SamplingState`：每片的真实电荷初态、已保存sample ID；
- `Controller`：只生成合法可执行开关命令，绑定已决码/DEM/dither；
- `ChargeOperator`：单一物理KCL实现，参考oracle可独立组装矩阵；
- `ResidueStage`：RA、AZ、动态参考及ADC2；
- `DigitalReconstruction`：名义/冻结权重解码；
- `Result`：记录物理C、真实V、数字码及合法性标志。

预期外部行为入口为：

`run_adc(input_diff_v, profile_id, seed, debug_sample_ids=...) -> Result`。

`Result.trace(sample_id)` 必须显示 `8 slice ids / actual sampling connections / SADC events / RDAC mask / physical Qf / RA V / ADC2 code / 20b output`，源码可读。若现有API已有等价入口则复用，**不要新建第二套ADC**。

生产物理单元索引=实际存在的电容；`mask_width`=数字可控位数；映射函数明确可选择固定连接的无位器件。任何真实物理单元不能凭`0 mask`等同不存在。

## 9. 验收 Gates（先过物理门，再测性能）

**G0 取证：**准确关联PAPER/SLIDES/所选PATENT/ASSUMED；若无原文必须显式ENG。

**G1 物理单元：**`physical element IDs`计数正确；实际C数组、固定单元及mask mapping精确对应；任何shape错位必须报错。测试主S4切换及末端C存在/不存在导致的β差异。

**G2 独立电荷：**一个主单元单次切换、一个子单元切换、固定单元影响、单片→8片、正负差分，比较独立KCL的`feedback_charge_c`与生产`Cf×RA_voltage`。使用相同真实底板连接及正确初始顶板电位；静态理想情况下要求接近数值求解误差，误差阈值由浮点尺度及矩阵条件数计算，不用5.72μV掩盖代数错误。

**G3 数字链：**SADC9b、follower、相位时序与输入/输出完整；粗码边界、连续输入与可达命令，最终输出单调性/输入重构正确。不以512×64点代替完整20b INL/DNL。

**G4 专利机制：**依次follower→资源交织→所选DEM→所选dither→普通AZ/动态参考→权值校准；每项至少原文源、主链事件、反例与开/关对照；TRACK/AUX另列。

**G5 统计与对标：**无失配→固定制造失配→噪声/动态；INL/DNL单码阈值/适用码密度，FFT等同条件。不得调sigma后把拟合结果称独立复现；DR/NSD等论文指标按完整频带和测量域报告。

**G6 RTL：**只在`G1~G4`涉及的数字接口冻结后进行当前主profile位真对拍；旧63/8、12bit结果不得证明新17/5、14bit数字主链。

**G7 易理解性：**主README一张整机论文图、状态矩阵、5个关键图、一个可运行入口；模块README原图→方程→源码→样本；删除相互矛盾的当前注释，旧结果归历史。不再新增第二套大框架。

**停工门槛**：G1/G2失败时**停止宣称论文性能复现**，但继续修物理与不依赖性能的文档/源码问题；不得用测试数量、假设修正或“端点对齐”跳过。

## 10. 本轮实施顺序（直接执行、无需重复询问）

1. 固定当前HEAD和受测源码；接收本包的两张论文图和专利证据索引。
2. 在当前拓扑候选旁创建一个**source-locked的物理连接表**；不自动删除任何既有逻辑，只改变目标profile的物理事实。
3. 修复S4、末端C和mask/physical mismatch；删除伪权重作为物理量的用法；保留必要的真正数字padding，但确保它不被当作电容。
4. 单位转换与电荷算子：先拿一笔真实8片采样运行独立KCL，修复无来源的`q_free*(Cconv/Csamp)`；分别保存真正Qf和输入等效r。
5. 零失配/零噪声完成连续样本、5条定向电荷反例、跨片状态正确。
6. 接通follower的真实主链对照，按选定专利机制逐项接入其他功能；不继续添加并行系统。
7. 只从已证实物理基线重新出效果图，更新当前README；再按冻结合同推进RTL。

**不允许在编码阶段重新推断源架构。** 若某连接原文确未披露：按照本SPEC选定且明确命名的ENG连接继续实现，并保留一组替代profile作敏感性比较；不能无界追问或把ENGINEERING模式说成论文实测。

## 11. 码库修复点（已核到`f52d22a`，执行时重新检查HEAD）

- `src/adi_model/topology/capacitors.py`：同一文件既写S4不进、又写要进分母；同时声称第5C不存在/存在；需要改成源图器件与选定profile明确映射。
- `src/adi_model/defaults.py`：S4与子padding使用 `2/2**weight_fraction_bits` 极小正数绕过正值检查，必须移除其物理含义。
- `src/adi_model/topology/dac_arch.py`：mask允许5、实际C只4并截断，不能作为精确专利物理模式。
- `src/adi_model/simulation/pipeline_engine.py`：`-q_free*(Cconvert/Csample)-QDAC`不是物理电荷KCL的自然式；另需修统一`residue_charge_c`定义。
- `src/adi_model/control/fig11_dem.py` / `dem3d.py`：相位别名、S4命令、共享横向/片间纵向、Fig19/20跨列门控需按**同一选定实施例**对齐。
- `src/adi_model/control/dither.py`：`4:2:1/7`是专利七片示例；当前8片不得直接称原芯片同物理实现。
- `verification/contracts/rtl_params.vh`：63/8、ADC2=12与当前Python新候选不统一。物理G1/G2完成后处理。

## 12. 知识边界声明

本SPEC把“已经核实的论文高层架构”与“具有明确电容的**专利实施例候选**”联结为可测试行为平台。它不宣称取得原厂全部底板开关版图、真实工艺电容、差分具体参考轨、ADC2精确位数或内部钟控完整网表。上述信息只有原件/PDK/硅测可以确定；在缺失情况下，工程等效假设要独立命名和验证，不得虚构。
