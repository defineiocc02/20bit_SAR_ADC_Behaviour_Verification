# ISSCC2024片上数字IP合同 v2.1：B1–B9裁定及执行补充

日期：2026-11-14。
评审基准：`7157ae32d54053afdc7f7ff99c7779138486f4f4` 的 `docs/CONTRACT_V2_REVIEW.md` 与 `tools/contract_v2.py`。
本补充与 v2 一起使用；同名定义冲突时以本补充为准。它不修改远端仓库。

## 0. 本次决议与来源边界

接受评审关于接口不充分的意见。v2没有把SADC码权、guard分支、交换目标、推进时刻等写成足够精确的可执行定义，这是合同问题，不应要求开发者猜测。

不接受两个进一步推论：
1. SADC与RDAC的宏单元数不同，不等于归一化D(k)不同。
2. 内部guard命令超过511，不等于外部输入范围扩展，也不等于需要超出电源轨的参考。

原文确认的是独立量化器、两个SADC、18个RDAC slice、8片并行、3-bit横向/纵向DEM、名义RA增益32和片外系数/片上修正。**本补充中的电容细分、桥值、guard编码、边界交换、ADC2窗口和PRNG规则仍是[E]工程补全，不是ADI精确网表恢复。**

来源：
- [A] ISSCC2024 9.8，p.182，Fig.9.8.1–3；PPT9–12、16–18、29–35。
- [P] US10516408B2 Fig.12、16–19，支持独立量化器/并联slice/双SADC框架。
- [P] US10505561B2 Fig.19，支持分路扰动机制。
- [P] US10511316B2 Fig.19–21，支持横纵打乱/保留列/MUX思想。
- [E] 本文件以及v2具体接口实现。

本轮保留原有工程网络，不因文档缺项重新设计模拟电路，不增加片上LMS/SVD，不扩展到APR。

## 1. B1：SADC物理编码、D_sadc(c)与匹配边界

### 1.1 资源与电容表

每个SADC有自己独立的主采样阵列，总计64Cu，不共享RDAC的电容实例。

| SADC单元 | 物理电容 | 普通转换控制 | 对粗码c的名义贡献 |
|---|---:|---|---:|
| 主宏0..6 | 每个8Cu | H个1的七位热码 | 每个64 |
| 主二进制4 | 4Cu | c[5] | 32 |
| 主二进制2 | 2Cu | c[4] | 16 |
| 主二进制1 | 1Cu | c[3] | 8 |
| 主平衡/guard | 1Cu | 普通区0，guard区1 | guard时8 |
| 子4 | 4Cu，经beta=1/8 | c[2] | 4 |
| 子2 | 2Cu，经beta=1/8 | c[1] | 2 |
| 子1 | 1Cu，经beta=1/8 | c[0] | 1 |
| 子分数支路 | 1/2、1/4、1/8、1/8 Cu | 普通post均为0 | 用于采样Lower及平衡 |

主阵列的4/2/1/1属于**主采样阵列**，不能误写成另一个子阵列。主阵列总和为7×8+4+2+1+1=64Cu。子阵列总和为4+2+1+1/2+1/4+1/8+1/8=8Cu。

桥Cb=8Cu/7，beta=Cb/(Cb+Csub_total)=1/8。SADC不使用RA的Cf来定义其比较门限。

### 1.2 普通码0..511

令：

```
H = c // 64
V = (c // 8) % 8
L = c % 8
```

SADC选中的主电容为 `(8H+V)Cu`，选中的子阵列有效电容为 `(L/8)Cu`，所以：

```
Csel_Q(c) = (8H + V + L/8)Cu = (c/8)Cu
Csample_Q = 64Cu
D_Q(c) = -3 + 6*Csel_Q/Csample_Q = -3 + 6c/512  [V]
```

RDAC活动组的8片总主采样电容为512Cu，其DEM后选中有效电容为 `Csel_R(k)=k Cu`，所以：

```
D_R_nom(k) = -3 + 6*Csel_R/(512Cu) = -3 + 6k/512 [V]
```

故对于相同码、名义器件和相同参考：

```
D_Q(k) - D_R_nom(k) = 0
```

不要为了使宏单元数量看上去一致，把SADC的7宏结构改成RDAC的8宏结构。

### 1.3 独立节点模型与判决

采样期主顶板钳位到差分共模0；子节点释放后自由电荷为0。若主/子物理电容向量为Cm、Cs，桥为Cb，子预置位为p：

```
X_acq = sum(Cs * 3*(2p-1)) / (Cb + sum(Cs))
Q_T_acq = -sum(Cm)*x - Cb*X_acq
```

转换期通过以下两节点方程求SADC主顶板T：

```
[ sum(Cm)+Cb,    -Cb         ] [T] = [Q_T_acq + sum(Cm*Vpost_main)]
[ -Cb,           sum(Cs)+Cb  ] [X]   [sum(Cs*Vpost_sub)]
```

比较器保留条件为 `T <= 0`（本工程极性）。名义、无Dither时T的零点正是D_Q(c)。不要用生产D5返回的D(k)计算比较结果。比较器误差须单独注入并记录，不可读取RDAC实际失配修正SADC决定。

Lower预置位按子支路 `[4,2,1,1/2,1/4,1/8,1/8]Cu` 的顺序为：

```
p_Q = [0, D5, 0, D3, D2, D1, 0]
```

其名义输入折算扰动为 `(2D5+(4D3+2D2+D1)/8)*Delta`，其中Delta=6/512V。

### 1.4 SADC guard及命令范围

SADC的c容器为10位，允许物理转换码0..519。对512..519：主11个单元全选，子4/2/1表示c-512，分数子支路post保持0。

于是 `Csel_Q=(64+(c-512)/8)Cu=(c/8)Cu`，D_Q(c)继续为同一条直线。这个guard是[E]，不是论文公开的比较次数。

普通事件协议仍为Flash3位区间初始化，加六次SAR判决。最高区间允许单独测试512门限；进入guard后解析低3位。最终输出应为已决码，不发布试探码。

即使c本身可表示，`k=c+U`仍须满足RDAC范围0..520；超出则报告命令范围错误，禁止回绕。SADC的下/上越界也必须通过验证环境/宏接口明确报告，不默认为首末码。

### 1.5 非理想匹配与ADC2范围

名义相等不等于任意失配下仍有[0,Delta)余差。定义每笔实际：

```
delta_Q = SADC实际采样扰动（输入折算）
delta_R = 6Wp/G
D_R(k,mask) = 6Wm/G - 3
qerr = x + delta_Q - D_Q(c)
eta = D_Q(c) - D_R(c+U,mask) + delta_R - delta_Q
r = qerr + eta
```

在比较门限严格递增且正常floor量化时，`0 <= qerr < Delta_Q_max`。比较器失调/判决误差必须计入eta或另列预算；不能在存在不正确Flash区间或非单调门限时使用该界。

对输出 `y=alpha*G*r+b`，正常工作必须证明：

```
YMIN <= b - alpha*G*E
b + alpha*G*(Delta_Q_max + E) < YMAX
```

这里E是所声明输入/失配/相位/片组/扰动范围内 `abs(eta)` 的上界；实际检查可使用更紧的逐码区间上下界。

对名义G=32、alpha=1、b=0、Delta_Q_max=Delta，窗口[-0.1875,0.5625)给出的对称eta边界余量为Delta/2=5.859375mV；使用上述保守严格不等式时E应小于此边界。实际器件须代入实际G、alpha、b、步长上界后复核，不引用“>11-bit matching”替代数值预算，也不自动扩大窗口。

## 2. B2：单位必须区分

禁止写 `D(k)=k Cu` 或不加限定地写 `1 LSB=1 Cu`。Cu是电容，D是输入折算电压，LSB还必须注明粗码或最终20位。

```
RDAC有效被选电容： Csel_R(k)=k Cu
SADC有效被选电容： Csel_Q(c)=c Cu/8
粗码电压步长：     Delta=6/512 V
最终20位电压步长： LSB20=6/2^20 V
真实切换电荷：     DeltaQ=Ceff*DeltaV_reference
```

“每个RDAC组级粗码LSB对应Cu有效切换量”可作为上述归一化关系的简称。单个RDAC宏8Cu对应8个粗码步进；SADC的8Cu宏由于分母是64Cu，对应64个粗码步进。

## 3. B3：guard完整码表及外部满量程

RDAC合法命令范围**0..520，两端均包含，共521码**。普通区是0..511；guard是512..520。

普通区的H/V/L只定义于0..511。guard使用独立译码分支，禁止把H=8截断为三位0，禁止只修改统计计数N而不修改真实mask。

以下是bridge交换**之前**的基础post-mask，每个guard的128个主半单元bit均为1：

| k | guard_sub=k-512 | 每片子post `[4,2,1,1bal]` | 组级Csel_R/Cu | D_R_nom/V |
|---:|---:|---|---:|---:|
| 512 | 0 | 0000 | 512 | 3.00000000 |
| 513 | 1 | 0010 | 513 | 3.01171875 |
| 514 | 2 | 0100 | 514 | 3.02343750 |
| 515 | 3 | 0110 | 515 | 3.03515625 |
| 516 | 4 | 1000 | 516 | 3.04687500 |
| 517 | 5 | 1010 | 517 | 3.05859375 |
| 518 | 6 | 1100 | 518 | 3.07031250 |
| 519 | 7 | 1110 | 519 | 3.08203125 |
| 520 | 8 | 1111 | 520 | 3.09375000 |

D_R是输入折算的相减门限，不是外部可测输入规格，也不是某个必须超轨的物理底板电压。guard状态全部只使用既有参考轨连接；大于3V的等效D用于抵消正向采样扰动，外部输入范围保持[-3,3)V。

名义外部输入与64字组合：Ld最大2.875粗LSB、U最大5、T最大7.875；因此c最大514，k最大519。520是可表达的内部余量命令，不表示正常名义运行必定用到它。它能否覆盖某个非理想范围，仍由§1.5判定。

G1数量：521×8×8×2=66,688覆盖一种half路由；两种half都测为133,376。计数只是覆盖说明，不能替代从实际mask计算电荷。

## 4. B4：保留列规则

```
base_u = [1]*H + [0]*(7-H) + [0]      # 索引0..7
h_word[u] = base_u[(u-h_phase) % 8]
reserved = (7+h_phase) % 8
v_word[s] = int(((s-v_phase) % 8) < V)
macro[s,u] = v_word[s] if u==reserved else h_word[u]
```

H、V均为0..7。保留列是原始索引7的结构零位，不是搜索“第一个数值为0的bit”。h=1时base索引0移到物理索引1。

guard绕过普通热码，直接主全1；但reserved仍按同一公式生成，供bridge路由使用。

## 5. B5：bridge交换的唯一可执行定义

### 5.1 冻结的路由

每笔采样准备时冻结：

```
bridge_slot  = v_phase
bridge_macro = reserved
bridge_half  = (acq_seq >> 3) & 1
bridge_j     = 2*bridge_macro + bridge_half
bridge_sid   = frozen_slice_ids[bridge_slot]
```

acq_seq从0开始，每个acq_prepare_fire递增一次。这里的slot是冻结的8个活动片ID表的位置，不等于物理slice ID。测试接口可强制half用于覆盖，但生产按上式。

coin由独立状态发生器产生并在同一时刻冻结。目标选择不能依赖未来判决、最终码或Vin。具体路由是[E]，不声称为ADI原始路由。

### 5.2 完整物理mask

先从普通/guard译码得到base_post，形状固定为8×20：前16位是8宏×2半，后4位是子阵列。交换前所有slot的j=16相同。

```
a = base_post[bridge_slot][bridge_j]
b4 = base_post[0][16]
assert all(base_post[s][16] == b4 for s in range(8))
post = deep_copy(base_post)
if bridge_coin == 1:
    post[bridge_slot][bridge_j] = b4
    for s in range(8):
        post[s][16] = a
```

其余位完全不变。pre-mask完全不变。post始终为同一个8×20数组；Wm的求和域始终为8×20，只有哪些项乘1发生变化。

名义目标主半单元为4Cu；八片子4Cu支路有效总值为8×(4Cu/8)=4Cu。因此名义电荷守恒，但不保持无权重的1-bit数量，也不要求仍然每对主半单元相等。

G统计ACQ阶段采样Vin的全部主单元，不统计post-mask为1的单元。因此对同一片组和同一系数epoch：

```
G_coin0 == G_coin1
Wp_coin0 == Wp_coin1
```

失配下：

```
DeltaWm = (b4-a) * (w_target_main_half - sum_s w[s,16])
Delta_y = -6*alpha*DeltaWm
```

必须按交换后的实际post重构，不能用名义守恒把真实电荷变化抹掉。

### 5.3 最小必测实例

k=8、h=0、v=0、half=0：reserved=7，目标为slot0、j14。交换前目标主半位a=1，八个子4位b4=0。coin=1后目标主半位变0、八个子4位变1，共9个物理bit改变；名义Csel仍为8Cu，G仍32。

k=4提供反向例：目标主半位0、子4位1；交换后反向改变。若a=b（例如一些guard状态），coin=1合法地不改变mask，不能要求所有输入都发生翻转。

禁止用coin=1的`pass`、直接返回coin=0结果或仅比较总码权来通过bridge测试。`8N+L==k`只用于基础译码；交换后应检查所有半单元和子单元的加权总和。

## 6. B6：采样预置与Wp

对于本笔被冻结的八个物理slice：

```
Wp = sum(slot=0..7) sum(j=16..19) w[frozen_sid[slot],j] * pre[slot,j-16]
```

既不是仅一片，也不是18片全部求和。只纳入本笔参与采样的8片的子阵列，因为本网络主阵列的采样状态固定为Vin，其贡献已在Gx中。

w已经包含beta及1/Cf，不能再乘beta、再除8或重复除Cf。名义Wp=T/16，因此 `6Wp/G=T*Delta`。

本工程正向采样Dither的符号：

```
y0=Gx+3G-6Wm+6Wp
y=alpha*y0+b
xhat=((yhat-b)/alpha -3G+6Wm-6Wp)/G
```

固定x和post时，提高Wp使y0增大；重构时减去对应贡献一次。禁止同时扣名义TDelta与实际Wp。

预置slot轮转固定为dither_rotation=v_phase。令J=U+2D5、f=word&7：

```
pre_value[s]=J + int(((s-v_phase)%8) < f)
```

pre_value0..7用4/2/1二进制，8用1111。预置在本笔ACQ开始前建立，至采样完成保持。以后bridge只改变post，不追改pre。

## 7. B7：alpha、b的归属、标定与合法域

1. 本轮共享RA线性模型：alpha为全局正增益因子，b为全局输出坐标偏置，单位V；均由片外标定和加载。G1/G2名义默认alpha=1、b=0。不增设片上估计器。
2. 模型顺序唯一：`y=alpha*(Gx+3G-6Wm+6Wp)+b`。b位于alpha之后；先减b再除alpha。不得把b同时放到采样预置和输出端。
3. W仍定义为Ceff/Cf；在已独立固定W归一化的条件下，片外从真实参考输入及ADC2观测获得：`z_i=G_i*x_i+3G_i-6Wm_i+6Wp_i`，拟合 `yhat_i=alpha*z_i+b`。alpha为去均值最小二乘斜率，b为截距。需要两个以上不同z，宜多点平均；训练/验证分离，越窗样本数量单列。
4. 若W与alpha都未知，仅凭同一组转换观测无法唯一分离其公共缩放。必须有独立归一化依据，或改用有效权重alpha*W并固定alpha=1；后一种是受控系数格式变更，不能静默重复补偿。ORACLE真权重验证不算系数提取验收。
5. 当前数字表示建议alpha为unsigned Q2.30（32位，0<alpha<4），b为signed Q8.24（32位）。这是表示域，不是模拟精度保证；alpha=0、NaN/Inf、无法表示的配置拒绝。
6. 正常工作合法域还必须满足§1.5窗口不等式。对一个已确定的实际残差范围[r_min,r_max]及alpha>0，允许b的区间由 `YMIN-alpha*G*r_min <= b < YMAX-alpha*G*r_max` 决定；跨片组取交集，空集即规格/器件域不成立。不得随意指定“任意b都能校准”。
7. 默认不增加每片组alpha/b表。若后续模型明确含qid相关偏置或组相关非线性，须单独扩展合同，不能塞进共享RA全局参数里掩盖。
8. 定点顺序：先把 `(yhat-b)/alpha` 变为Q24，再使用v2的G、Wm、Wp重构。alpha=1走精确旁路。额外舍入误差计入原算术预算。

## 8. B8：事务事件、相位及PRNG推进

定义两个不同事件，不再混用：

- `acq_prepare_fire`：有空闲资源、有效系数且准备请求被接受，正式预留片组与qid，建立pre-mask和ACQ控制。这发生在真实输入采样之前。
- `sample_fire`：本笔ACQ完成，在公共采样边沿实际锁下输入。原`launch_accept`若继续使用，仅作为sample_fire的别名。

相位在acq_prepare_fire生成并写入不可变的事务描述符：

```
h = acq_seq & 7
half = (acq_seq >> 3) & 1
v = next(v_rng) & 7
coin = next(bridge_rng) & 1
```

定义next=xorshift32(13,17,5)，每次移位/异或按32位无符号截断。独立寄存器与种子：

```
pool_rng   = 0x13579BDF
v_rng      = 0x2468ACE1
bridge_rng = 0x9E3779B9
dither_rng = 0xC001D00D
```

独立状态和不同seed不构成数学独立性的证明；这是固定且可复现的[E]实现，不以统计美化为本轮任务。

pool选择沿用v2：候选物理ID升序，消耗两次pool_rng更新；用乘积高32位映射到10和9，再跳过第一位置选两个不同备用片。v/bridge各消耗自己的状态一次。dither每次准备也推进一次；测试强制word覆盖输出，但不改变推进次数。

reset：清所有在途描述符、恢复seed及acq_seq=0。被拒请求和stall期间无acq_prepare_fire，则所有相关发生器不推进。已经准备但后来取消的事务不回滚随机状态或序号，并记录取消。SAR事件、ADC2返回、重构退休均不推进这些状态。

stall只阻止新的准备或数字入口接受，不得单独冻结已经运行的模拟采样时钟而让另一半状态继续前进；需要停机时执行明确的排空/停止协议。

## 9. B9：双SADC与RDAC池

- RDAC物理ID为0..17；另有两个独立SADC实例，qid=0/1，不占用RDAC池。
- 每个SADC有自己的64Cu主采样阵列、子阵列、比较状态和失配参数。它确实采样Vin并增加输入负载；数字侧只负责门控与时序，不据此开展驱动器设计。
- 正常工作中，本笔被指定的SADC与八个RDAC ACQ slice在同一公共输入采样边沿采样同一个外部Vin，但各自的Dither预置不同。
- 一个qid在CONV/WAIT_RELEASE，另一个qid在ACQ；SADC转换完成后不得提前返回ACQ扰动另一组正在采样的输入。
- qid在准备时按next_qid严格交替分配。指定qid或所需片组忙，则拒绝/延后准备，不能临时换到另一个qid而保持旧pre-mask、Flash上下文或tag。
- 第一次采样必须先完成规定ACQ。取消不回滚seq/qid状态；reset清空并从qid0重新启动。
- 每个qid的状态至少区分FREE、ACQ、CONV、WAIT_RELEASE；具体命名可沿用原工程。Flash共享由调度器关联到正在转换的qid；模拟宏无需“输出txn_id”。

## 10. 对已提交G1结果的裁定

实际读取并按Git blob校验后重放了`7157ae3:tools/contract_v2.py`：blob为`671f63aa717c68b8ba1e82c43668a5a1c1761f20`。

两个独立问题：
1. coin=1分支是pass，重复计算coin=0，不能计为bridge两种状态验收。
2. guard区仅把N覆盖成64，没有把返回mask置全1。k=512时实际mask仍只有56个宏，即448Cu；但nominal_charge使用被覆盖的N报告512Cu。k=516实际为452Cu，k=519实际为455Cu。

因此保留普通码、coin=0的名义编码测试作为局部资产；撤销guard实际mask和bridge完整通过的结论。修复后从返回的8×20物理mask重新求和，不允许由期望k直接赋值G1观察量。

G1退出条件：
- 521码全部有真实基础mask，guard主全1；
- 两种half、两种coin均验实际路由；至少k=4/8检查9个bit的非平凡变化；
- SADC节点零点与RDAC同码名义电压相等；
- no-op coin、假N覆盖、遗漏低位三类故障必使验证非零退出；
- 不改变已有物理定义来迁就测试。

随后进入G2，只实现数字Flash/SAR/Follower接口和独立行为宏，不重做模拟晶体管电路，不因本补充增加新的算法课题。

## 11. 本补充附件的实际验证范围

`verify_contract_v21.py`不导入仓库生产数字链；其物理网络沿用v2工程节点模型，新增明确路由、guard及负向检查。

实际结果在`verification_v21_results.json`中：133,376个实际mask组合、520个SADC节点零点、32,768个SADC/Dither决定、2,816个名义端到端点，以及明确的no-op与错误mask反例。

这些验证只证明该[E]合同的构造性一致性。没有验证用户RTL、时序、物理实现、ADI私有网表、系数提取完成度或DEM/Dither频域收益。
