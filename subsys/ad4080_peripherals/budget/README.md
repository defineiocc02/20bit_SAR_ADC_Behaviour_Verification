# 数字功能 IP 面积门槛与交接

功能 IP 在 1.500 × 1.500 mm、40 nm 项目规划中分得 **0.250 mm²** 毛占地。扣除 **0.025 mm²** 已规划行禁放，剩余 **0.225 mm²**。利用率上限暂取 **60%**，目标工艺上已映射的 **pre-CTS 行单元面积须 ≤0.120 mm²**，**最终行单元面积须 ≤0.135 mm²**，中间保留 **0.015 mm²** 给 CTS、时序修复和 ECO。现行 IP 的 40 nm 同版面积报告与实际摆放结果待 IP 方交付。[统一规划](../floorplan/plan.json)

## 1. 黑箱接口与面积归属

| 交接面 | IP 负责人提供 | 顶层集成人员核对 |
| --- | --- | --- |
| 实例身份 | 顶层模块名、冻结版本、参数及模式、接口声明、适用工艺及库版本 | 面积和时序报告采用与接入接口相同的版本和配置 |
| 时钟与复位 `CLK_RST` | 时钟域、频率及相位关系、复位极性和释放次序，以及测试模式和时钟源切换 | 时钟与复位从规划西侧接入，提供真实时钟约束和复位传播条件 |
| 粗判决 `Q_RETURN`、RDAC 切换 `SADC_RDAC`、二级回传 `ADC2_RSP` | 端口位宽和位序、每位/每组的物理槽对应、交易标识、采集及建立窗口，有效沿和异常退场 | 前级、RDAC、RA 和 ADC2 接口所有者共同冻结最晚判决到达、开关控制接收和建立、捕获确认和二级码到达 |
| 结果 `RESULT` | 出码宽度、编码、吞吐与延迟、有效标志、是否允许反压及逾时行为 | 接入外围、缓冲及数据接口，确认采样域和读出域的 CDC、复位与溢出边界 |
| 配置与测试 `CFG_TEST` | 加载和提交时序、运行期更新限制、超时与出错恢复，以及测试引脚和模式 | 确认北侧引脚、供电域和电平、OTP/配置源，验证上电、掉电、测试和正常运行的交接 |
| 物理交接 | 建议引脚层、分组、访问方向、引脚数、供电网和最小通道，附带硬宏的物理及时序视图 | 检查引脚可达性、布线、电源网格、时钟树及模拟隔离与规划边界相容 |

功能 IP 合同范围内的组合、时序、配置存储、扫描等行单元归到 `IP`。独立 **结果 SRAM `FIFO`、小 SRAM 预留 `SR2`、OTP、数据接口 `DIO`、数字外围 `CTRL`** 各自占用规划台账中的窗口，记录各自边界内的外围单元。若 IP 自带局部系数 RAM 或 ROM，仍位于 `IP` 且由 IP 台账扣除其行禁放足迹。若一个功能的实现跨了 `IP` 与外围，交付一张实例/宏到分区的唯一归属表。[统一规划](../floorplan/plan.json)

## 2. 目标工艺的阶段验收

| 阶段 | 面积门槛 | 同 `report_area` 一起提交 |
| --- | ---: | --- |
| 规划 | 250,000 µm² 毛占地，225,000 µm² 基准净行区 | IP 和外围归属、引脚组、宏及预估隔离。缺实际单元报告时为 `LIMITS_ONLY` |
| 目标 40 nm 综合，随后重复检查实际 pre-CTS 摆放 | 120,000 µm²，按新增行禁放收紧 | 冻结 IP 的独立顶层面积报告，叶级汇总及宏/存储映射，库/工艺档位、PVT、SDC、时钟和 I/O 时限、未映射或黑箱实例清单。摆放后补合法化、真实 LEF/site、局部拥塞、I/O pin access 与时序 |
| CTS、详细布线和 ECO 后 | 135,000 µm²，按最终行禁放收紧 | 更新单元面积、CTS/ECO 实际增量、宏及禁放几何、路由拥塞与抽取后 STA。集成释放还要核对 CDC/reset、时钟偏差、供电/IR/EM、混合信号隔离与 foundry 物理验证 |

报告中的输入面积使用本次实现的**一次性叶级汇总**，包括时序、组合、tie、时钟缓冲、hold 和 ECO 单元，以及占用摆放行的常驻 tap/endcap、专用 decap/spare 等。普通收尾 filler 填的是空行隙，核对利用率时排除。若某个占位已建模为禁止放置的行区，则把对应单元从面积分子中剔除。工具将任何 “Total cell area” 中的硬宏计入时，先用报告的层级/单元类型明细拆出行单元。源报告需能核实面积单位，保留报告哈希、实现阶段、工艺和库版本、top/配置及同版宏平面图。

引脚可用量按实际 LEF 端子、允许金属层和走线轨道核算。规划的 `Q_RETURN` 面为 34 µm、31 根，Q 通道最窄横截面 24 µm。`SADC_RDAC` 面为 325 µm、522 根。若全部中心排在单层同一直线上，且暂取零端部裕量，则 `N` 根需满足 `(N−1) × 轨距 ≤ 可用长度`。对应三处的理想轨距上界分别为 1.133、0.800 和 0.624 µm。实际可用轨数还要扣除两端间距、PG、屏蔽和过孔占用，并按可用方向分层核对。GDS 的 1 µm 标记是端口几何导引。[规划坐标与分线数量](../floorplan/plan.json)

## 3. 硬宏、halo 与新增行禁放

给定 `IP` 矩形集合、原有行禁放 `Bplan`、IP 内新增硬宏连同 halo 的外廓 `Bmacro`、真正切断行的新增 PDN 或隔离带 `Bpd`。以下 `A()` 计算几何并集面积。

```text
ΔA     = A(IP ∩ (Bplan ∪ Bmacro ∪ Bpd)) − A(Bplan)
Arows  = 250,000 − 25,000 − ΔA
Afinal = 0.60 × Arows
Apre   = min(120,000, Afinal − 15,000)              面积单位 µm²
```

- 宏实体和 halo 用同一组外廓覆盖。两个禁放 halo、已有规划条带和 PDN 禁放重叠时，各位置只记一次。硬宏实体和外廓需要完整落入 IP 联合区。宏实体彼此重叠、外廓超出 IP、或 halo 缺失部分实体时，脚本报告几何输入错误。宏间实际最小净距、禁放区对硬宏的独立限制、布线访问和供电需要实体图复核。
- `row_blockages` 可用完全在裸片内的顶层长条，门禁只计位于 IP 内的投影。单层走线受限、PDN 穿过上层或只引起拥塞时，保留行面积并在实际布局阶段验可布利用率。零散栅格、行端及通道的有效空间以目标 PDK 的合法化结果为准。
- 若用 SRAM 替换原来映射为标准单元的寄存器阵列，用替换后同版的标准单元报告作分子，并更新宏和 halo 的位置。对 IP 内替换，面积余量变化为省去的净行单元面积减去 `利用率 × 新增净行损`。主结果 SRAM 则归独立 `FIFO` 窗口。新旧报告层级和配置需一致，确保容量、读写端口、时序及复位和初始化语义已验收。

下表为固定 0.015 mm² CTS/ECO 预留的灵敏度。更改统一规划的利用率时，若要保持这笔预留，应同时修订规划里的 pre-CTS 目标。

| 可用利用率 | 额外净行损 `ΔA` | pre-CTS 上限 | final 上限 |
| ---: | ---: | ---: | ---: |
| 50% | 0 | 0.09750 mm² | 0.11250 mm² |
| 55% | 0 | 0.10875 mm² | 0.12375 mm² |
| **60%** | **0** | **0.12000 mm²** | **0.13500 mm²** |
| 60% | 0.010 mm² | 0.11400 mm² | 0.12900 mm² |
| 60% | 0.020 mm² | 0.10800 mm² | 0.12300 mm² |
| 60% | 0.025 mm² | 0.10500 mm² | 0.12000 mm² |

门槛超出后先追查 top/配置、报告单位、宏重复计数、实际占行的实例和最拥塞区域。确认口径后，按带宽和时序约束比较 IP 方的存储或算术改法，以及顶层的分区形状、宏放置和额外窗口调整。举例，pre-CTS 实测 0.133 mm²、预留 0.015 mm²、60% 利用率时需 0.148 / 0.60 ≈ 0.24667 mm² 净行，计入现有 0.025 mm² 禁放即需 **0.27167 mm² 毛占地**，较当前多 0.02167 mm²。先用公式量化缺口，再以实际通道、隔离和端口访问复核腾出的几何能否使用。涉及合同功能或模拟边界时由各接口所有者先确认，再复跑同目标工艺的映射、摆放和时序。

## 4. 结果 SRAM 的窗口核对

AD4080 Rev. B 公开 FIFO 深度为 **16,384 笔**，单端口以采集和读回分阶段运行。项目暂按 20 位净有效数据规划 **327,680 bit**，拆成 **4 × 4,096 × 20 bit**。bank 数、字宽对齐、测试和端口配置由目标编译器及项目实际功能决定。[AD4080 Rev. B，第 1、56、58 页](https://www.analog.com/media/en/technical-documentation/data-sheets/ad4080.pdf)

| 项目 | 几何或容量 | 规划判断 |
| --- | ---: | --- |
| IPBrain 照片上主 SRAM 框 | 453 × 165 px | 图像 1126 × 1125 px，按两轴归一至 1500 × 1500 µm，得 **603.5 × 220.0 µm，0.1328 mm²** |
| 照片标边灵敏度 | ±3 px/边 | 同一归一口径为约 **595.5–611.5 × 212–228 µm**，面积 **0.1262–0.1394 mm²** |
| 本方案 FIFO 大窗 | **605 × 200 µm，0.1210 mm²** | 宽度与图标同量级，短边比图中归一估值小 20 µm，周边留有接口及总线通道 |
| 2 × 2 bank，外边各 8 µm、bank 间 8 µm | 每 bank 最大 **290.5 × 88.0 µm**，**25,564 µm²** | 以 81,920 个有效位计，宏含外围电路最多 **0.3121 µm²/有效 bit**，宽、高须分别满足 |
| 外边及 bank 间都调至 12 / 16 µm | 每 bank **284.5 × 82.0 / 278.5 × 76.0 µm** | 降至 **0.2848 / 0.2584 µm²/有效 bit**，短边和引脚访问先受影响 |
| 左侧小 SRAM 照片框与规划 | 照片 99 × 45 px，约 131.9 × 60.0 µm。规划 130 × 45 µm | 分设 `SR2` 窗口和待绑定接口。其用途、位数和宏面积由负责人定标 |

照片框和透视、裁切的影响只能支持相对布局，归一尺寸服务于本项目初始规划。主大窗的宽度与照片相符，高度及单 bank 的矩形约束偏紧。用目标 40 nm compiler 的实际 LEF 核对 bank 宽高、外围、pin/OBS、供电、BIST/修复、读写冲突、周期和布线。若编译器将 20 位补齐到 24 或 32 位，25,564 µm² 仍为 bank 的几何上限，对物理位的等效密度要求分别约 **0.2601** 和 **0.1950 µm²/bit**。总面积满足但尺寸超出时，可评估换长宽比、bank 排布或扩大独立 FIFO 窗口，按实际宏长宽和净通道重新核算。[原照片与图源说明](../evidence/inputs.md) · [统一规划](../floorplan/plan.json)

替换时核对实际宏的读延迟。`result_store` 在接受读请求的沿登记银行号和 `read_valid_o`，数据须在下个消费沿之前到达。若宏再多一个读流水级，需一并调整银行号、有效位、回读容量预留与 MBIST 对齐，再回归背靠背换银行和读写交替。[结果存储 RTL](../rtl/result_store.sv) · [存储交接](../memory/README.md)

### 4.1 实际编译宏接收

从 `subsys/ad4080_peripherals` 运行。两份清单和原始供应视图可以放在本地获准的目录，LEF 和各角 Liberty 的相对路径以供应方清单所在目录为准。

```sh
python3 -B budget/sram_macro_gate.py \
  --requirements /authorized/project/sram-required.json \
  --candidate /authorized/vendor/sram-candidate.json
```

| 输入 | 字段与来源 |
| --- | --- |
| 项目批准清单 `--requirements` | `technology_node_nm` 为 40。`process_id` 和 `release` 对应批准工艺与具体发布版。`allowed_physical_layers` 列出允许的 pin/OBS 层。非空 `required_pvt` 以项目角 ID 为键，每角给 `operating_conditions`、`process`、`voltage_v`、`temperature_c`、`clock_period_ns`、`max_clock_to_q_ns`。时钟至读出预算需给下一个消费沿留下余量，40 MSPS 要求银行周期不超过 25 ns。 |
| 供应方清单 `--candidate` | `source_kind` 填 `supplier`，`process_id`、`release`、`technology_node_nm` 由供应方与项目确认一致。给 `name`、`compiler_id`、`macro_revision`、`words` 4096、`bits_per_word` 20、`port_model` `1RW`、`write_width_bits` 20、`clock_edge` `rising`、`read_cycles` 1。`lef` 和 `liberty_by_pvt` 指向同版原件，后者按必需角 ID 映射文件。 |
| 朝向和落位 | `allowed_orientations` 列出供应方允许的 DEF 朝向。`banks` 顺序为 B0、B1、B2、B3，逐条填写 `orient`、旋转后物理框左下 `ll_um`、包住实体及所需 halo 的 `footprint_um`。接收门用实际 `SIZE` 换算外框，要求每个外廓完整落入规划银行框。 |
| 端口绑定 `pins` | `clock`，`chip_enable` 和 `write_enable` 各含 `name` 与 `active`（`high` 或 `low`），`address`、`data_in`、`data_out` 分别列 12、20、20 个物理引脚，从 RTL 低位到高位。`pg` 将物理 pin 映射为 `power` 或 `ground`，可选 `tieoffs` 列额外已批准输入的 0/1。所有引脚都要能在同一宏的 LEF 和 Liberty 对上。 |

接收门回读 LEF 的数据库单位、`SIZE`、`SYMMETRY`、`PIN/PORT` 与 `OBS`，逐角核对 Liberty `operating_conditions`、`pg_pin`、时钟、最小周期和每个读位的上升沿至 Q 表值。Liberty 支持文本和 gzip，位名支持明确 bit pin 或可展开的 `type` 总线。当前 LEF 读取支持明确零 `ORIGIN`、`RECT` 和 `POLYGON` 的宏。无法读取的几何、视图格式或缺少的字段会停在 `UNKNOWN`。1RW、有效极性和读拍数来自供应方规格。对实际功能、模式条件、合法化、引脚访问、版图规则及 STA，按真实视图继续验收。

输出含所读源文件 SHA-256 和逐角数值。`PASS` 退出 0，表示该实物资料满足静态接收门。`FAIL` 退出 1，表示读到与合同矛盾的事实。`UNKNOWN` 退出 2，表示缺资料或不可读。当前独立检出的交付路径只有几何规划 LEF，尚缺批准工艺角和同版编译宏资料。空参数实测退出 2。

公开回归使用自生成的几何和时序文件，成功状态为 `FIXTURE_PASS`。包的预算 `unittest discover` 同时运行接收门夹具，项目宏实物状态单独从上述命令读取。[公开夹具和完整字段示例](test_sram_macro_gate.py)

## 5. 历史面积和公开原型

| 证据 | 数字 | 本次用法 |
| --- | --- | --- |
| ISSCC 2024 9.8 原型论文与讲稿第 40 页 | 论文公布裸片 **2.25 mm²**，讲稿标注 **40 nm ULP**、核心 **0.77 mm²**、核心含参考缓冲 **0.9 mm²** | 用第 40 页对应的 **1050 µm、820 µm** 两个箭头复算 “Digital” 红框，得 **0.060335 mm²**。正文引用约 **0.060 mm²** 的图上区域占地 |
| 28HPC+ 整核历史报告 | 旧网表 `485abbbd4909104c` **106,574.71 µm²**，另一旧路径记录 **108,951.11 µm²** | 旧位宽、存储实现和约束下的综合量级。后续归档将约 108,951 µm² 撤出该版当前证据。现行 IP 的 40 nm 行面积由 IP 方同版重新映射确定 |
| 28HPC+ 独立 `wsum_v21` | **102,201.55 µm²** DC 单模块，独立 FC 实现在运行预算内未完成 | 说明存储和权重数据路径可能主导面积。旧模块表中 `recon_v21 → recip_rnd_v21 → recip_q60` 包含重复统计的层级 |
| 现行状态与早期项目估计 | 现行顶层同版整核网表、门级等价和 40 nm 物理结果待交。早期可行性报告另有 **0.3–0.5 mm²** 校准引擎估计 | 以冻结接口范围、目标库和同版实现消解估计差异，40 MSPS 吞吐另验目标库和模拟宏接口时序 |

第 40 页以 PDF 左上角为原点，x 向右、y 向下。沿矢量中心线量得 Digital 左右边 **x = 256.739990、293.940002 pt**，上下边 **y = 138.299988、267.779999 pt**。右边取与 Quantizer 区共界的下段，右上笔划略向外斜，计入其楔形面积仅增加约 **0.00026 mm²**。

| 方向 | 标尺两端的轴坐标 | 物理比例 | 红框折算尺寸 |
| --- | ---: | ---: | ---: |
| x | 214.860107–529.860229 pt | 1050 / 315.000122 ≈ 3.333332 µm/pt | 37.200012 pt → 124.00 µm |
| y | 213.899811–432.105804 pt | 820 / 218.205994 ≈ 3.757917 µm/pt | 129.480011 pt → 486.58 µm |

两轴折算尺寸相乘并除以 10⁶ 即得 **0.060335 mm²**。将红框每边移动半个笔划宽度（±1.5 pt），得到 **0.05418–0.06671 mm²**。再允许箭尖各端偏移 ±1 pt，联合取点灵敏度为 **0.05335–0.06776 mm²**。该范围固定原图物理标签，反映选边和选点对结果的影响。论文 Fig. 9.8.7 的左上 Digital 红框在原生 711 × 715 px 图内约为 67.5 × 229.5 px，与讲稿的狭长位置、形状和 Quantizer 共边一致。论文图支持边界核对，物理尺度采用讲稿第 40 页箭头。[量测记录](../evidence/isscc_digital_area.json) · [复算脚本](../evidence/isscc_digital_area.py) · [讲稿第 40 页](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_SLIDES.pdf) · [论文 Fig. 9.8.7](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_PAPER.pdf)

讲稿第 17–18 页给出 RDAC 分段、DEM 和上下段 dither 路径。第 18 页定性说明较小 DAC 电平转换器的面积收益。第 19–22 页是失调与增益校正、带宽及时钟匹配、交错随机化及其失配仿真。第 29–31 页给出 8/18 片选择、横向和纵向各 3 bit，并在第 31 页画出 50% 概率桥接。第 32 页给出整机 INL/DNL。论文正文进一步给出 6 bit 二维 DEM，及 dither 从量化器转移至 RDAC 时范围增加 2 bit。原件未披露 DEM 或 dither 各自的面积，红框内部各功能的布局归属也待原作者资料核实。[讲稿第 17–22、29–32 页](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_SLIDES.pdf) · [论文第 182–183 页与 Fig. 9.8.3](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/references/ISSCC2024_PAPER.pdf)

项目的转换 IP 由另一团队交付。转换相关 DEM、dither、切换控制和合码的具体覆盖范围由 IP 方确认，项目外围以结果、配置、时序和测试接口接入该黑箱。结果 FIFO、数据接口等外围按所占分区单独验收。**0.250 mm² 毛占地、0.120 mm² pre-CTS、0.135 mm² 最终上限**来自项目规划和阶段预留，转换 IP 的 40 nm 同版面积与实摆报告仍待提交。历史 28HPC+ 面积用于提示存储和算术规模风险，跨工艺面积由目标库实测建立。[统一规划](../floorplan/plan.json) · [旧整核报告](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/_history/v21_legacy/GATE_EQUIV_superseded.md#L152) · [撤回记录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/_history/v21_legacy/FINAL_D1_D6_superseded.md#L39) · [13 模块复核](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/audit/ALL_13_MODULES_FINAL.md#L15) · [现行状态](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/7408edf/docs/STATUS.md#L23) · [早期可行性报告](../evidence/inputs.md)

在交付根目录，用 PyMuPDF 和两份原 PDF 复现量测。

```sh
python3 -B evidence/isscc_digital_area.py /path/to/ISSCC2024_SLIDES.pdf /path/to/ISSCC2024_PAPER.pdf
```

## 6. 复算命令与输入格式

从交付目录运行，以下实数是用于展示门槛的示例输入。

```sh
python3 -B budget/area_gate.py
python3 -B budget/area_gate.py --stage pre-cts --cell-area 120000 --area-unit um2
python3 -B budget/area_gate.py --stage final --cell-area 0.135 --area-unit mm2
python3 -B budget/area_gate.py --occupancy budget/example_occupancy.json
python3 -B -m unittest discover -s budget -v
```

`--plan` 可指定另一份统一规划，默认采用同包的 `floorplan/plan.json`。规划须给出 `die` 和 `core`，IP 全部矩形位于 `core` 内。对附带硬宏或额外行禁放的实报，先按示例创建一份实际占地 JSON。规划和占地文件的各对象字段须唯一。

- `units` 为 `um`，矩形为裸片坐标 `[x0,y0,x1,y1]`。用已选择的真实 LEF 方向与放置坐标换算，面积单位通过 `--area-unit um2` 或 `--area-unit mm2` 明确给出。
- `macros` 每项填唯一 `name`、实体 `body` 和可选 `halo`。其中 `halo` 是包含整个实体的外接禁放矩形，省略表示只按实体扣行。`row_blockages` 是另外需要剪掉的行区。
- 几何算例中外廓 9,900 µm²、条带 2,000 µm²，二者交叠 450 µm²。其并集 11,450 µm² 与已有禁放交叠 2,875 µm²，实际新增 **8,575 µm²**。pre-CTS/final 门槛随之收紧至 **0.114855 / 0.129855 mm²**。该算例覆盖并集扣减。宏的合法放置还要用真实 LEF 对照 DEF 全局硬禁放和独立宏约束。
- 输出用十进制字符串保存面积。退出码 **0** 表示 `AREA_PASS` 或 `LIMITS_ONLY`，**1** 表示 `AREA_FAIL`，**2** 表示输入单位、字段或几何无效。等于门槛可通过。面积证据归档时同时保留该 JSON、来源报告、plan 版本及占地输入。
