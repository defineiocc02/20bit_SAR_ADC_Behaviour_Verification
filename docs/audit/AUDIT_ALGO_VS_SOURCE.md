# 生产数字 IP 与论文、专利的逐项对照

**验收对象是 `adc_digital_ip_v21`。** Python 的 `adi-simulate`、`python -m adi_model simulate`、`adi_model.simulate` 和 `default_model().simulate` 现在使用同一个事务模型。它从输入电压及外部控制正向计算比较结果、物理开关、余差和出码。逐时钟的片权、反压、排空和捕获时限由同一生产顶层的 Verilator 测试验收。[Python 入口](../../src/adi_model/defaults.py) · [生产清单](../../rtl/filelists/files_adc_v21.f) · [接口合同](../CONTRACT_CURRENT.md)

## 功能能对应到哪里

| 功能 | 原文披露 | 生产 RTL 与默认 Python 的落实方式 |
| --- | --- | --- |
| 片组调度 | ISSCC 第 182 页和图 9.8.1 明确给出 18 个 RDAC 物理片，8 采、8 转、2 备用。讲稿第 10–12 页区分两个前级量化器与共享 RA/ADC2。 | 受理顺序决定 qid，旧采集组整组交给转换，从余下十片选两片备用。具体的 xorshift32、映射到 10 和 9 的方法及种子是工程选择。冷启动首个采集组为 0–7。[片选 RTL](../../rtl/sar_core/sched18.sv) · [逐拍片选参考](../../src/adi_model/sched18_rtl.py) |
| 已决位跟随 | 论文说明 RDAC 随独立量化器已经形成的判决更新。专利图 16、19 示意两个量化器与并行残差 DAC。 | 普通区提交 Flash 和 bit5…0，共写 7 次。最高区试探 512：接受后只判 bit2…0，共写 5 次；拒绝时本拍不写，再从 bit5 继续。512 门限、事件编号与上限 520 属于工程定义。Python 的理想 SADC 依次比较门限，记录每次实际写入，RTL 消费宏返回的合法判决。[架构专利](https://patents.google.com/patent/US10516408B2/en) · [事件 RTL](../../rtl/sar_core/sar_evt_v21.sv) · [事务算法](../../src/adi_model/digital_conversion.py) |
| DEM | 论文图 9.8.3、讲稿第 29–31 页给出 8/18 片选、3 位横向轮转、3 位纵向分配和二进制／一元边界交换。DEM 专利图 19–21 给出保留列、选择器和两级打乱的实施例。 | 普通命令按高、中、低各三位解码，每槽 16 个主半单元、4 个子支路。横纵相位、半单元和交换选择是顶层**输入**。Python 默认以上游 B8 激励驱动这些引脚，也可提供采集到的实际控制。讲稿的 50% 标注不能确定上游随机电路和原片真值表。[DEM 专利](https://patents.google.com/patent/US10511316B2/en) · [掩码 RTL](../../rtl/sar_core/dem_mask_v3.sv) · [共同使用的开关参考](../../src/adi_model/dem_dither.py) |
| 分路扰动 | 讲稿第 17 页画出低段送 SADC、完整扰动送 RDAC，并在 SAR 至 DEM 之间调整命令。US10505561B2 图 19 正文用七片分摊小数，从粗码减上段，再在后级去除剩余扰动。 | 本工程采用八槽与正向采样坐标。令 f=4D3+2D2+D1，低段 L=2D5+f/8，高段 U=4D6+D4，完整预置 T=U+L。SADC 接 L，八槽 RDAC 平均接 T，最终命令 k=c+U。重构按同一笔八个实际采样开关扣一次 Wp。六位字由独立 xorshift32(13,17,5) 在受理沿生成，种子 0xC001D00D。专利示例对应 f/7 及 c−U，不能把减号单独移植到现行电荷坐标。[分路扰动专利](https://patents.google.com/patent/US10505561B2/en) · [译码 RTL](../../rtl/sar_core/dither_pre.sv) |
| 权重与重构 | 论文第 182 页明确披露片外提取系数、片上做 DAC 权重修正。 | 装载 18×20 个 Q30 权重，按物理片号、采样 pre 与最终 post 形成 G、Wp、Wm。生产 alpha 固定为 1，分子为 64Z+6Wm−3G−6Wp，随后用本笔 G 归一并按有符号规则舍入。ADC2 的 13 位、0.75 V 窗口、Q24/Q30、倒数初值表与两次 Newton 均为工程细节。按粗码索引的误差 LUT 只见于研究对照。[权重 RTL](../../rtl/sar_core/wsum_v21.sv) · [重构 RTL](../../rtl/sar_core/recon_v21.sv) · [倒数 RTL](../../rtl/sar_core/recip_q60.sv) |
| 吞吐与交权 | 图 9.8.1–2 给出输入与上一笔余差的公共采样时刻。论文公布 40 MS/s 和样片 INL/DNL。 | Python 每行描述一笔已受理输入。真实 RTL 测试在时钟沿两侧检查片选、写门和数据归属。640 MHz 理想数字时钟与约定宏响应下，192 笔连续按 25 ns 间隔交付。实际宏建立、非重叠时间及同版整核物理时序仍待验收。[片权问题实测](ISSUE_10.md) · [时钟条件](../STATUS.md#持续采样率) |

[论文 DOI](https://doi.org/10.1109/ISSCC49657.2024.10454329) · [原图 9.8.1–9.8.4](../../references/ISSCC2024_PAPER.pdf#page=2) · [作者讲稿](../../references/ISSCC2024_SLIDES.pdf)

**“增强 2 bit”的比较口径。** 原文明确说量化器至 RDAC 的扰动范围增强 2 bit。对当前码表枚举，低段有 16 种取值、完整预置有 64 种，是四倍的取值数。用同一粗码步衡量峰峰跨度，两者分别为 2.875 和 7.875，比例为 63/23，约 2.74。现有披露没有给出可逐档核对的样片电压表；四倍电压摆幅不能写作本工程的实测结果。

交织跟踪、辅助输入及参考缓冲专利还描述了模拟侧实施例。现行生产顶层的范围由源清单和引脚确定。备用片按历史码预跟踪、辅助输入驱动、粗细参考缓冲与模拟开关建立应各自验收。论文参考文献 [12] 印为 10,797,889，同题交织授权原件为 US10707889B1。[交织专利](https://patents.google.com/patent/US10707889B1/en) · [辅助输入专利](https://patents.google.com/patent/US10541702B1/en) · [参考缓冲专利](https://patents.google.com/patent/US10826519B1/en)

## 同一组输入的实测

| 场景 | 生产顶层与公开 Python 的比较 |
| --- | --- |
| 片内序列 | 固定输入 −0.6522216796875 V，未经测试选字的 1,024 笔覆盖全部 64 个字。每笔均为 410304，ADC2 越窗 0 笔。首四字为 45、7、17、40。 |
| 强制字遍历 | 同一输入遍历三轮共 192 笔。字 53 时 SADC 预置为 0x2A，c=202、k=206、raw=6016，扣回后为 410304。 |
| 混合输入 | 192 笔交替使用片内字和定向字，重播种片池；电压含四分之一输出码步、两个端点附近及八个 Flash 区间。普通、拒绝 512、接受 512 分别 144、29、19 笔，正端数字限幅 12 笔。限幅记录随样本保存。 |
| 已知子单元偏差 | 每张表 192 笔。物理片 0 的子单元 18 增加 2⁻¹⁰，名义表产生 10 笔 −32 码与 14 笔 +32 码；用正确权重校正后全部归零。两次使用同一器件与输入。 |

共 **1,792 笔**，每笔对比 **36 个字段**，含标签、两端预置、全部八槽 post、实际写入序列、ADC2 原码、输出码和标志。固定输入组另比较真实 CLI 生成的完整 NPZ。独立的断线负对照中，192 笔有 96 笔 ADC2 越窗。两项符号故障的测量和基线复现命令见[独立审查](ISSUE_10.md)。

~~~bash
work="$(mktemp -d)"
bash tools/pyenv.sh --run -m adi_model simulate --dc -0.6522216796875 --samples 1024 --output "$work/python"
bash tools/pyenv.sh --run tools/run_open_rtl.py --simulator verilator --tops v21_sample_cadence --out "$work/rtl"
bash tools/pyenv.sh --run tools/check_default_rtl.py "$work/rtl/sar_core_sample_cadence/samples.csv" --default-npz "$work/python/codes.npz"
~~~

## 早期行为图的读法

旧公开入口使用专利 Fig.11 的 17 主/5 子位置研究几何。对同一固定输入启用其 Fig.19 电荷口，64 个字中有 42 笔 ADC2 越窗。该研究现在只在源码仓库的实验目录中显式装配；已发布的图、原始数值与已知反例仍按原工况解释。未披露的原片实现统一以本工程合同安排开发和验收。[原提交复核与实测](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/issues/10#issuecomment-7004164016) · [Fig.11 研究源码](../../experiments/fig11.py) · [历史行为图](../figures/behavioral/README.md)
