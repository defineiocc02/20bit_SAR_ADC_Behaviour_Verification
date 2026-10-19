# 校准原理、RTL 功能与综合结构独立复核（2026-10-02）

审查基线为 `71935ab54238c7ed638f5fa66c5ffb58e10287bb`。本文件重新核对原始页图和当前源码，不把旧报告的结论当作证明。生产 RTL 未修改；只新增两个限域 bench、精确整数/Fraction 核算和两份系数镜像。该工作不是全顶层回归、模拟电路验证、新综合或 STA。

## 1. 先给出结论及证据边界

1. **静态电荷模型内，当前物理权重归约→残差 MAC→有符号 floor→offset-binary 输出的代数方向一致。** 系数已经包含反馈电容/有效桥接衰减带来的级间增益，名义镜像的总信号增益为 32；重构后不能再乘/除一个 32。
2. **9 个校准模块实现片上系数装载与重构，不实现片上权重估计。** 原始论文 [00] PDF p1 右栏明确外部取得系数、片上权重修正；原文没有披露 Q30/Q32、96 位受检范围、恢复余数除法、全部学习器电路。当前工程离线估计使用独立已知输入和 SVD，这属于工程实现，不能当成原芯片学习算法的复现。
3. **PPT p18 的 lower/upper 双 dither 与论文 p1 的 RDAC dither 范围额外扩大 2 bit 尚未由当前结构核全量实现。** 当前 sampling 与 quantizer 两种模式互斥；同一个整数 dither 在量化器侧注入并从 RDAC 命令减去，或通过采样 mask 产生残差 dither，再按物理权重重构。两种模式各自可以数学闭合，但不能合并命名为原图完整双 dither。
4. **`cfg_ready` 只证明装载完整、基本范围与模式合法，不证明每个满幅输入都不会 `acc_ovf`。** 已构造合法系数镜像：把名义权重全部乘 `2^20`，每个系数仍 `<2^47`、总表和 `<2^60`，但 midscale 的 `shifted` 超出 96 位受检范围。实际 `cal_residue_mac` 在 6 个边界点中正确报告这些溢出。这是受保护的使用域限制，不能误报为 RTL 算术失效。
5. **发现一项必须明确的舍入语义：ties-to-even 作用于相对 `adc2_min_q` 的增量，不是绝对码仓中心。** 两者在奇数 min 且半整数增量时不同 1 个 Q32 LSB。RTL 与冻结合同/Python 一致，默认镜像不会碰到该角落；目前不应单独改 RTL 打破 bit-exact 合同。应统一物理数值表述与合同，或用新 ADR 同时改模型、导出、RTL 和 oracle。
6. **未新增已证实的默认配置 RTL 功能 bug。** 这不意味着全参数域、完整模拟电路、全部专利实施例或 ASIC PPA 已签核。两项新增实证（使用域与舍入）针对现有描述中需要更精确的边界。

原始输入 SHA 见 `input_manifest.json`；本轮数学和 bench 输入身份见 `calibration_evidence_manifest.json`。

## 2. 原文实际披露什么

以下页码均为用户提供 PDF 的物理页号，栏号为专利纸面印刷栏号。扫描专利的 OCR 只用于检索；本次关键论断已看原始 PNG 页图。

| 原始证据 | 页/图/条款 | 对校准链路的直接约束 | 不能据此宣称的内容 |
|---|---|---|---|
| [00] ISSCC 2024 正文 | PDF p1 左栏，p2 Fig. 9.8.1 | quantizer 与 RDAC 分开；8 个 slice 生成残差，8 个采样，18 个池中有 2 个备用；共用 RA；RA 示意增益 ×32 | 没有给出整数流水、系数位宽和单元逐项拟合算法 |
| [00] 正文 | PDF p1 右栏关于 DEM/dither 与权重修正的两段 | 两维 DEM；转移量化器结果到 RDAC 时 dither 范围扩大 2 bit；片上修正、外部产生系数 | 不能推断已知 `d_R=4*d_Q` 为唯一电路公式；该比例须由明确定义的单位/上下位映射及原图实施细节确认 |
| [00_1] PPT | p18 lower/upper dither 框图 | dither 有进入 SADC、RDAC 输入及 RDAC 数字控制的不同路径，需分辨注入及移除位置 | 用 sampling/quantizer 两个互斥枚举模式不能构成同一时刻的完整双路径 |
| [00_1] PPT | p19–20，p33–35 | gain/offset 修正；RA 采用 GMR+OTA、AZ 处理 offset/drift；ADC2 采样带宽随时间调整 | 静态 W/O 无法自动吸收时间误差、AZ 噪声代价、动态参考未稳定等效应 |
| [09] US10516408B2 | PDF p32 栏26 claim 1/3/9/14；p33 栏27 claim 17/19 | 采样时间常数匹配的多个 DAC 跟随数字估计；合并残差；数字操作块可修改各 slice 控制词；多个 slice 降低热噪声 | 没有规定当前 Q30/Q32、固定 16 相位或片上 SVD；数字逻辑不能证明模拟时间常数匹配 |
| [10] US10505561B2 | PDF p35 栏24 claim 1–12；p36 栏25–26 claim 13–24 | RDAC 采样注入与量化器注入可分开；第一/第二 dither 可有整数/小数分量；部分分量可在后级数字结果移除 | 必须逐项画注入/粗码修改/后级移除的路径，不能只验证最终数值便宣称所有 claim 均已实现 |
| [11] US10511316B2 | PDF p46 栏29–30 claim 1/2/7/8；p47 栏31–32 claim 15/17/20 | shared/additional 部分在合作 DAC 之间分配；main/sub bridging；已知注入可用于 dither/calibration；动态选 K/L slice；二维索引 | 仅打乱粗码逻辑位置不能代替物理单元的选择记录；没有规定完整系数训练电路 |

原始页图：`reference_pages/00_p1.png`、`00_p2.png`、`00_1_p18.png`、`09_p32.png`、`10_p35.png`、`10_p36.png`、`11_p46.png`。对应 OCR 为 `reference_text/*_p*.ocr.txt`。纸面 claim 与工程覆盖的对照是技术范围审查，不构成法律意见。

### 2.1 对 dither 覆盖的精确判断

当前 `sar_structural_ctrl.sv:199–202` 输出量化器 dither；`:214–218` 只有 `quantizer_en=1` 才从粗码反号减去 `current_dither`。采样轨由 `:204–206` 与 `unit_therm` 产生，实际采样 mask 的四列物理权重参与校正。`calib_regs.sv:116–118` 拒绝 sampling 和 quantizer 同时开启。

因此：量化器模式与 [10] claim 13 的一般方法具有部分对应；采样 dither 经残差后在后级重构移除，与 claim 21/23 的一般路径具有部分对应。但 sampling 模式没有根据该 sampling dither 同时改变粗码的现行控制，第二分量的小数位及后级移除也没有独立接口。不能把这种部分对应升级成 claim 1–12 或 PPT p18 全部拓扑复现。

精确有效的设计动作是先定义 `d_Q`、`d_R`、低/高分量、每个分量的物理单位和符号，再列四个边界：SADC 输入、RDAC 采样输入、RDAC 数字命令、ADC2 数字重构。只要该分量已经通过实际 RDAC 开关命令计入 `rails`，就不能再无条件加入公共 `I` 做第二次扣除。当前量化器模式应让公共 `inj_q` 不再包含已移除的量化器 dither。

## 3. 从电荷守恒重新推导重构式

### 3.1 物理残差，不从数字粗码直接猜电压

对固定物理 slice 的一个有效单位，定义正权重 `w_j=C_j,eff/C_F`。main 单元 `C_j,eff=C_j`，sub 单元的权重含桥接衰减 `β`。这是 RA 处在理想虚地/静态稳定边界内的有效系数；其值可以经过片外标定，不能从原文图直接读出全部实物数值。

令 normalized 输入 `x=V_in/V_fs`、已知公共注入 `i=V_inj/V_fs`、`on_j=1` 表示转换底板接正参考。转换底板 normalized 电压为 `2*on_j−1`。普通信号单位在采样时接 `x+i`，因此该单位的残差贡献为

`w_j * [x+i−(2*on_j−1)] = w_j*x + w_j*i + w_j*(1−2*on_j)`。

若单位用于 sampling dither，它不接信号，采样底板为 `r_j+i`，其中 `r_j∈{−1,+1}`。此时贡献为

`w_j * [r_j+i−(2*on_j−1)] = w_j*i + w_j*(1−2*on_j+r_j)`。

合并以后得到

`F_phys = O + G*x + T*i + R`，

`T = Σw_j`，`G = Σa_j*w_j`，`R = Σw_j*s_j`，

其中普通单位 `a=1,s=1−2*on`；sampling dither 单位 `a=0,s=1−2*on+r`。由 `r` 与转换底板轨相减，sampling 单位的 `s` 是 `−2/0/+2`，不是只取 ±1。

因而

`x = (F_phys−O−R−T*i)/G`，条件是 **G>0 且测得的 F 属于同一物理开关/epoch/样本**。

这解释了为什么必须记录 DEM 之后的物理 ID 与开关 mask，而不能只保存粗码。不同 active slice 的失配权重总和不同；`T/G` 可以随选择改变；不应把全部转换的 gain 固化成 32，也不能用下一组 slice 的系数解释上一组残差。

### 3.2 桥接消元为什么可用逐单位有效权重

在 main 顶板 P 近似虚地、sub 顶板 Q 浮置的理想边界，令 sub 电容和为 B，桥接电容 C_C，sub 寄生为 C_p：

`(C_C+B+C_p)*V_Q = Σ_sub C_j*(V_convert,j−V_sample,j)`；

`C_F*V_R = Σ_main C_j*(V_sample,j−V_convert,j) − C_C*V_Q`。

消去 Q 后，sub 项变为 `β*C_j`，其中 `β=C_C/(C_C+B+C_p)`。所以静态 `w_j=β*C_j/C_F` 能同时承担 main/sub 比例与 RA 增益。该结论是对明确边界的电荷推导，不是对动态真实 GMR/OTA、漏电、参考瞬态或开关寄生注入的全电路证明。

当前 `src/adi_model/weight_calibration.py:1–12` 的线性模型与 `:176–201` 的逐单元 terms 和此推导一致：`b_minus_dac` 包含公共注入与采样轨减去实际 DAC 轨；`signal` 对 sampling mask 置 0。该模型依赖静态条件；它不能拟合掉所有频率相关误差。

### 3.3 定点化与最终码

权重整数 `W=round(w*2^30)`；电压整数 `F/O/I` 以 Q32 承载；`R=ΣW*s`、`T=ΣW`、`G=ΣW*a` 都仍以 Q30 承载无量纲量。于是

`num=(F−O)*2^30 − R*2^32 − T*I`，

`x_hat=num/(G*2^32)`，

`word=floor((x_hat+1)*2^19)`，

等价于

`shifted=(num+G*2^32)*2^20`，

`word=floor(shifted/(G*2^33))`。

因为 G 为正，整数嵌套 floor 恒等式允许 `A1=floor(shifted/2^33)`，然后 `word=floor(A1/G)`，**对负 shifted 也成立**。不能用向零截断代替 floor；输出需要先与有符号 0 和 `2^20` 比较，最后饱和到 20 位。

当前镜像 `sim/vectors/registers_paper_literal.json`：main W=`67108864=2^26`，sub W=`8388608=2^23`，8 个 active slice 的名义 G=`34359738368=32*2^30`。因此 `G/2^30=32` 已经承载论文 Fig.9.8.1 的 ×32。它不是要在数字结果之后再乘 32 的信号放大器。

### 3.4 系数学习与在线重构必须分开评价

本工程学习器的设计矩阵来自已知输入和数字记录：对每个样本，每个物理系数的 feature 为 `a*x_known + b−V_DAC`，再加 offset 常数列。默认有 `18*71+1=1279` 个未知量；至少要求 **样本数>1279、矩阵满列秩、合理的条件数与独立 holdout**。重复采集同一个不可辨识状态不会自动增加秩。

`src/adi_model/weight_calibration.py:299–381` 执行 rank-revealing SVD、检查有限性/秩/正权重，再冻结系数；RTL 不含这些操作。SVD 仿真训练的受控已知输入精度、参考不确定度、码量化偏差和噪声条件也不是原芯片在线校准电路的证明。应保留训练/验证不同数据、不同激励、已知输入来源及温度漂移信息，不用待校准 DUT 的真实电容、输入真值或理想残差当作硬件可读量。

## 4. 逐模块功能、状态及推导电路

以下行号是当前基线源码的精确行号。寄存器 bit 数为代码可推导数量，不是新的综合实测资源；综合会常量传播、删冗余或重映射。

| 模块与当前源行 | 接收/输出与本质功能 | 状态、时序、错误资格 | 可推导电路及主要风险 |
|---|---|---|---|
| `weight_store.sv:32–149` | 保存 18×71 物理 W；给出全表、行总和、选中写地址读回和完整性 | `:95–111` 拒绝非法地址/0/≥2^47/活动配置写入；`:113–126` 用本行旧值更新缓存；`:130–145` reset 清数值，clear 只清 written，换 epoch 重写所有项 | 1278×47=60,066 个非恒零系数 bit；written 1,278 bit；18×54=972 行和 bit，合计 **62,316 FF 候选**。18 路 71:1 old-word mux、行更新减加及配置读回。全表并行输出导致 FF/宽连线，不能直接替换成单口 RAM |
| `calib_regs.sv:52–160` | 三个 Q32 标量+四控制位；validate 提交配置 | `:109–128` clear 优先；忙/写冲突拒绝提交；sampling+quantizer 互斥，min<max，权重/标量完整。活动 epoch 禁止改值 | 192 标量+3 完成+4 控制+ready+32 错误≈**232 bit**，少量比较器/资格 mux。`P_ADC2_BITS` 不消费，不能当成完成任意 ADC 后端适配 |
| `cal_sample_context.sv:32–69` | current→residue→fine，保存物理 ID、main/sub mask、采样轨、注入 | phase9 保存当前残差包；下周期 quiet/phase0 把旧 residue 交给 ADC2。reset/disable 丢两包。两 strobe 同沿时 fine 收旧 residue，未实现 capture bypass；生产相位将其分开 | CW=709，两份完整 payload+4 valid/bad=**1,422 bit**；本模块不保存模拟电压，也不按系数 epoch编号，只依赖配置锁定/取消 |
| `cal_weight_reduce.sv:51–185` | 实际物理选中行与 on mask→T/G/R | `:73–85` 维度检查+重复/越界 ID异常；重复ID按物理行OR只计一次并标 invalid。`:67–71` G=T−mask，`:185` rails=total−2*on+dither | 无 FF。18×8=144 个 5bit equality 选行；28 个 ID 重复比较；on 1278 非零候选叶、1277 潜在有效加法节点，深度约11；生产行和缓存树18叶/17节点；4列×18叶/68节点，再mask/rail各3节点。宽叶输入/扇出/拥塞比“for循环长度”更能预示布局风险 |
| `adc2_dec.sv:57–104` | raw ADC2 integer→Q32 相对量程的码仓增量再加min | 组合。`:81–87` 形成 `(2c+1)*(max−min)`；`:91–95` 相对增量RTE；`:98–104` 宽加、溢出钳位。载入器检查min<max | 有效 **13×65** 可变量程乘法（不是按decl名义78×78）；固定右移是连线，RTE低位比较+增一；min加法。合法升序端点下 fine 始终在min/max之内，numeric ovf不可达，不能从raw code判断模拟ADC2 over |
| `cal_residue_mac.sv:33–88` | 同一 sample快照→num→offset-binary偏置→A1 | 组合，op1/op2/op3/num/den/shifted受检范围[-2^95,2^95)。any_ovf时 A1无有效数值保证，不能使用其看似合理的截断值 | 宽差/移位/加减/比较器；唯一可变量乘是 **unsigned total × signed injection**，源码64×64语义范围，经综合可能缩窄，不能把132bit sign extension计成132×132乘法。该级的乘法+宽减法+异常检查要独立看STA |
| `div_floor.sv:50–163` | `q=floor(A1/G)` | 只idle/start接受。P5用13次迭代，P7用9次；busy start忽略。zero denominator定长结束q0/err1；宽分母不截断；负非整除补−1；同步reset取消 | P5每拍**5个63bit恢复减法/借位→选择**串联；P7串7个。除法器寄存器约P5 329bit/P7 325bit，不是展开全部63级的单拍除法。组合级数与busy周期折中，不应只减P_STAGES却忽略16相位容量 |
| `recon_core.sv:81–279` | 归约/ADC2→stageA→MAC/div→提交 | `:250–260` 只有ready/start/!busy采样；`:261–267` 下沿采样本样本MAC异常并launch；`:183–203` divider/busy；COMMIT时busy0，可接受下一start，旧ID由NBA保留；ready下降取消。接受→valid=P5 15/P7 11完整间隔 | stageA 418 payload+5状态≈**423bit**，算术在子模块。系数无需每次拷贝，只要完整epoch冻结。改流水必须同步ID/flags/context，否则数值可对而样本关系错误 |
| `cal_output_stage.sv:34–78` | candidate→dout/ID/current flags，保持三个sticky | complete &&ready发valid。acc/gain错误仍发完成，但保留最后合法dout/clip；ADC2 numeric ovf单独报告不阻止字更新。clear与新错误同沿新错误优先；ready0丢提交 | OUT20+ID32+flags5+valid1+clip2+sticky3≈**63bit**。`dout_valid` 表示完成事件，必须联合当前flags；不能拿sticky历史位当当前结果资格 |

### 4.1 row_total 缓存是不是偷偷改变了校准

没有。`weight_store` 仅替换写入所在行的旧值，再加新值，clear epoch时保留数值/行缓存并清完成 bitmap。新 epoch 第一次重写必须减去保留的旧 W。当前 `:118–121` 就做这个操作，未把“本epoch没写过”误认为旧值为零。

生产 `recon_core` 启用 `P_USE_ROW_TOTALS=1`（顶层 `sar20_digital_core.sv:628–637`），仅将总和 T 的归约改为18行缓存树；on-tree和dither列仍取物理W。`cal_weight_reduce.sv:111–112` 明确调用方承担 `row_total[s]==Σw_rom[s,u]`。**独立模块用户若打开该参数却提供陈旧缓存，会静默得到错误T/G/R；顶层使用同一weight_store输出保证其一致。** 这是接口前置条件，不是顶层已证实 bug。

### 4.2 为什么当前存储不能自然变 BRAM/SRAM

配置写入是单地址，重构读出却是物理全表并行接口：568个活跃项、动态on选择，还需dither列权重。即便每帧16拍，按568项全读也平均需35.5项/拍；如果重构归约能用的窗口更短，带宽更高。RAM方案必须给出银行数、每bank读端口、地址冲突、所需位宽和周期，并在保持采样/重构ID的条件下完成。

另一个候选是围绕当前二维DEM的前缀/分组和缓存，但它需证明所有main/sub bridging、二位旋转、slice集合与sampling mask的等价关系，不能用一维63环前缀替换当前8×8跳过占位单元的二维映射。

## 5. 数值边界与新增最小实证

### 5.1 位宽、符号、floor及溢出核对

1. W只允许 `0<W<2^47`，读入时按无符号零扩展；positive“signed Q30”与“无符号 bit型存储”的表述并不冲突，高bit为0。电压、rails、有符号分子必须符号扩展，不能把负电压或负word改成unsigned。
2. `cal_residue_mac.sv:57–66` 的 `F−O` 保留65bit，乘积total×inj先对total零扩展再signed乘；其132bit临时量用来形成和检查精确受检操作数，随后才合法裁位。
3. `:76–87` 的97bit s1可容纳两个已受检量之和，117bit shifted容纳完整乘`2^20`；top22bit检查等价于signed shifted装入96位。若任何前置op非法，A1允许成为截断位型，但any_ovf禁止合法字更新。
4. den=`G*2^33`的受检上界对应 `G<2^62`；这不是shifted的更严格工作范围，不能把该上界当成全链路无溢出保证。
5. `div_floor` 的最小负数用unsigned幅度可保存；大于numerator域的64bit分母保留highbit意义。组内qbit MSB顺序正确，负非整除用非零remainder下修。valid denominator为正时不需signed除数。
6. signed剪裁在 `recon_core.sv:201–206` 先于截取20位；低/高剪裁与acc溢出、gain结构错误和模拟饱和不是同一件事。

### 5.2 完整载入不等于满幅均可工作：6点MAC实证

当 `num=0`（输入等效midscale），`shifted=G*2^52`，故96位上界要求 **G<2^43**。对恰好`x=+1`，`shifted=G*2^53`，要求 **G<2^42**。如果把严格输入域写成`x<+1`，`G=2^42`可在该开区间内通过，但端点或量化后的候选值仍需实际检查；不能笼统给出满幅都安全。

两份镜像沿用原后端量程，offset设为该镜像raw ADC2 code341的精确解码值26215。slice IDs=0..7，每行main物理0..31接正参考、sub全负、sampling关、I=0，则R=0且F−O=0。

| 镜像 | 最大W | 全表和 | 本样本G | `shifted` | 资格 |
|---|---:|---:|---:|---:|---|
| `calibration_domain_nominal_mirror.json` | 67,108,864 | 77,309,411,328 | 34,359,738,368 | `2^87` | 装载谓词合法，MAC合法 |
| `calibration_domain_scaled_2pow20_mirror.json` | 70,368,744,177,664 | 81,064,793,292,668,928 | 36,028,797,018,963,968 | `2^107` | 装载谓词仍合法，MAC acc_ovf |

实际 `cal_residue_mac` bench还测试midscale的 `2^43−1/2^43` 与正满幅的 `2^42−1/2^42` 两对边界；6/6如推导。日志为 `calibration_domain_run.log`，终止标记为 `CALIBRATION_DOMAIN_PASS cases=6 scope=MAC_ONLY_NOT_FULL_TOP`。不使用非法A1作为通过证据。

该bench没有运行全表加载、全顶层或模拟回路，镜像“装载合法”由精确的单系数/总和谓词核算，不应改写成已经在顶层载入并物理运行成功。建议软件导出器同时报告：每种slice集合/采样模式的G极值、原始ADC量程/offset/I边界、6个受检op的最大值与margin。要把更多拒绝搬到validate，先证明拒绝条件不会错误排除允许使用的局部量程，并记录为新的合同。

### 5.3 ties-to-even 的作用点：9点ADC2实证

工程合同和RTL定义为

`C = min_q + RTE(((2c+1)*(max_q−min_q))/2^(N+1))`。

如果“把绝对码仓中心按Q32最近偶数取整”的物理文字定义是目标，则应比较

`P = RTE(min_q + ((2c+1)*(max_q−min_q))/2^(N+1))`。

RTE不满足任意整数平移不变性；min为奇数时，恰好半整数会改变偶数判断。

| min/max/code | 精确中心（Q32整数单位） | 当前C | 绝对中心P |
|---|---:|---:|---:|
| 1 / 4097 / 0 | 1.5 | 1 | 2 |
| 1 / 4097 / 1 | 2.5 | 3 | 2 |
| 1 / 4097 / 4094 | 4095.5 | 4095 | 4096 |
| −4097 / −1 / 0 | −4096.5 | −4097 | −4096 |
| 0 / 4096 / 0 | 0.5 | 0 | 0 |
| 原镜像 / code341 | `107374865/4096 = 26214.566650390625` | 26215 | 26215 |

最后一行用精确有理值计算。当前镜像delta=`644245094`，二进制因子仅2，不能让奇数 `(2c+1)` 的乘积在除8192时落于半整数，因此这一舍入角落不影响当前默认镜像。

`adc2_rounding_semantics_tb.sv` 直接实例化当前ADC2：9案例，6个C/P差异，全部RTL严格符合C。日志为 `adc2_rounding_semantics_run.log`，标记 `ADC2_ROUNDING_SEMANTICS_REPRODUCED cases=9 differences=6`。这是**合同和文字物理定义待统一**，不是新发现的默认RTL偏离oracle。

在Gphys=32时1 Q32 LSB相当于未量化输出 `2^-18 LSB20`，很小；但当最终floor阈值恰好夹在两个数之间，仍可能表现为1code差，不能断言“永远不影响最终码”。若选择绝对中心RTE，应把increment的tie parity改为`(q_shift+min_ext)[0]`等价语义，并同步修改全部模型/导出/回归，而不是单改RTL。

### 5.4 有限独立计算与参数边界

`calibration_math_check.py` 的精确Fraction/整数结果：

- 6组端点×4096码=**24,576** ADC2计算，当前增量RTE与显式合同全匹配；相对绝对中心RTE差8192例，全为1Q32 LSB。
- **5,000** 随机signed大整数，直接有理floor与剥去`2^33`的嵌套floor完全一致。
- 小宽度 WA=2..6、WD=1..8、group=1/3/7，共 **186,744** 计算，含最小负数、比numerator宽的分母、padding及非整除，恢复余数算法与整数floor全一致。该项是独立算法计算，不是任意参数RTL形式证明。
- 两份1278系数镜像的单权重合法域、全表和、实际T/G/R、ADC2code341、MAC资格均由精确整数封存。

几何参数与header常量不能混淆：weight_store维度只支持slice1..32/unit1..128；ID5bit、unit7bit固定。reduce约束`SUM_BITS>=W_BITS+ceil(log2(N_TERMS))`，要求P_DIT_N≥1与有效DIT_BEG；若sampling打开而DIT_END超过实际单元则invalid。recon/cal_mac的格式来自冻结header，不是完整通用宽度算术库；改W_BITS/ACC/V/OUT需重生params并重新证明所有拓扑和常量，当前weight_store的47/48/60以及ROW_BITS有意绑定冻结格式。

### 5.5 Q30 系数的条件误差界及缩位代价

该界只评价**对同一组已确定有效物理系数的定点量化**，不评价训练误差、真实噪声、参考误差或增益漂移。设 `hat(w)=w+δ`，每项 `|δ|≤2^(-F_W-1)`，I=0、输入x∈[-1,1]，普通项`s=±1`、sampling项`a=0,s∈{−2,0,+2}`，因此 `|a*x+s|≤2`。使用精确物理F而改用量化系数重构时

`hat(x)−x = −Σδ*(a*x+s)/hat(G)`。

若另外能证明 `hat(G)≥32`、最多N=568项，则20bit未floor输出的误差上界为

`E_code ≤ 2^19 * 568 * 2 * 2^(-F_W-1) / 32`。

| 权重小数位 F_W | 单项量化误差界 | 条件下的输出误差界（LSB20） |
|---:|---:|---:|
| 30 | `2^-31` | **0.0086669921875** |
| 26 | `2^-27` | **0.138671875** |
| 22 | `2^-23` | **2.21875** |

这说明Q30在上述工作域内有充分量化余量，Q22不能在同一最坏界下支持小于1LSB的数字权重量化预算。不能把三行作为不带前提的所有配置保证：`cfg_ready`不保证hat(G)≥32，也不限制真实输入/I边界；名义sampling四列mask把G从32降为31.75，因此采样模式须用实际G_min重算（Q30界约0.00873524LSB，而不是直接引用0.00866699）。如果I不为0，应把分子界换为`2+|I|`；如果hat(G)趋近0，误差会被任意放大。

这些是**未floor码值**的误差界。只要输入候选恰好靠近整数阈值，0.0087LSB的连续差也可能导致最终floor输出相差1code；不应把小于1LSB的连续界误写成bit-exact全码一致。ADC2量化、offset/RTE和外部标定误差预算需另行相加或按照其统计关系评价。

## 6. 结果资格及模型覆盖仍需注意

`dout_valid` 是完成通知，不自动等于“本样本有效测量”。acc/gain错误时word沿用前一合法样本，当前ID/flags仍更新。上位机必须保留ID与flags，否则一笔无效转换会看起来像前一笔数据重复。历史sticky也不能代替当前flags。

模拟 `adc2_over/rdac_ovf/ra_sat` 与 `adc2_dec` 的numeric ovf不同。当前顶层 `sar20_digital_core.sv:680–706` 把模拟故障与被捕获context关联后进入sticky状态，但五位 `dout_flags` 不含单独的本样本模拟故障位。若未来要逐样本评分、discard、闭环训练或设备在线故障诊断，应增加明确的同ID模拟质量metadata，或规定能无歧义恢复其关联的事件接口。当前sticky说明“发生过”，不够说明“具体哪笔数字输出可用于标定”。这是接口/验收建议，本次未将它标为新的已证实默认RTL bug。

静态校正无法替代输入settling、参考settling、时钟skew、泄漏/记忆、瞬时开关注入、非线性RA以及AZ/ADC2带宽噪声的验证。原文[00] p1、PPT32–35已指出这些误差；W/O恒定拟合把它们“吸收掉”只在特定训练信号条件下偶然成立，并不能支持全频率/温度/输入幅度结论。

## 7. 综合风险优先级与可评估改进

### 7.1 已可推导的主要成本

权重FF候选62,316；上下文约1,422；配置约232；重构stageA约423；除法器约325/329；输出约63。重量级成本是系数保存和物理归约，不是有限状态机的状态bit。on归约物理树按1278项布线，生产总和采用18行缓存，但on-tree仍约11层组合加法；物理active/mask分发、权重叶子扇出、adder placement和rail最后加减都可能构成长互连。

这些估算不包含RTL顶层其他模块，也不能直接换算为ASIC面积或功耗。`wire [63:0]`加法的声明不等于每个节点都真的需要64bit全加器，零扩展/固定padding可使部分节点缩窄；反过来，系统数学上的“最多8行活跃”不一定被综合器从二进制decode推导为每条进位的范围约束。应查mapped cell/netlist而不是用数学上限冒充映射资源。

### 7.2 建议顺序

1. **先收紧语义说明**：写明片外训练/片上重构、增量RTE、真实有效W包含级间增益、cfg_ready的含义、sampling/quantizer/双dither区别、同ID质量标志。
2. **导出侧建立范围证据**：对实际校准系数、合法slice集合、sampling mask和I边界，报告minG/maxG及6个op的range margin；小G需同时检查Q30系数误差放大，不能只验G>0。
3. **用既有真实STA定位数字结构**：先检查 ID→active/physical_on→on_tree/row_tree/column→rails_s 路径及fanout；把物理行相邻放置、局部归约、mux/adder结构作为单变量实验。新增pipeline需把ID、F、O、I、G、T、R与异常全对齐，并重新验证P5/P7的16拍预算。
4. **优先评估存储带宽**：FF全表方案简单且可读，但成本很大。banked SRAM/BRAM、系数压缩/差值、精确前缀缓存必须分别给出误差证明和端口/帧预算；禁止靠删高位、减弱overflow或近似除法获得看似低面积。
5. **优化divider不能只改展开数**：P5/P6/P7要相同输入/时钟/约束对比，记录局部critical path、busy周期、重构接受间距、全顶层开关与flags对齐；P6需要覆盖当前runner未默认注册的中间选择。
6. **双dither应作为明确的新功能里程碑**：先用原始图把各分量单位/注入节点/数字移除边界写成契约，独立电荷求解器与holdout给出闭合，再扩RTL上下文/接口和拟合激励。当前已有单路径计算结果不构成这个里程碑的证据。

本轮结论足以把目前工程的校准数学、功能与潜在综合电路解释清楚；它不把原文未披露的细节填成事实，也不以CI成功代替物理实现验收。
