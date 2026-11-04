# RTL 与 Agent 协同：工具链选型与实测记录

> 日期：2026-10-19 · 环境：macOS arm64（zhaoyi）· Python 3.13.12
>
> 本文只记录**实际跑过**的东西，以及跑不通时的原始证据。没有验证过的不写"支持"。

---

## 1. 结论先行

| 工具 | 用途 | 本机状态 | 结论 |
|---|---|---|---|
| **pyslang 12.0.0** | SystemVerilog 解析 + elaboration | ✅ 可用 | **已接入门禁**，抓出 5 个真实缺陷 |
| **Verible v0.0-4296** | lint + format | ❌ 不可执行 | 官方 macOS 资产实际是 Linux ELF（见 §2） |
| **RTLCoder（HKUST）** | RTL 生成模型 + 评测流程 | ⚠️ 部分可用 | 仓库可克隆；模型权重不可达（见 §3） |
| Verilator / Icarus | 仿真 | ❌ 未安装 | 本机无仿真能力，TB 只能静态检查 |
| Docker | 跑 Linux 版 Verible | ❌ 未安装 | 无法用容器绕过 ELF 问题 |

**当前可执行的门禁**：

```bash
cd ad4080_rtl
bash scripts/lint.sh          # pyslang + 文件头 + 风格
STRICT=1 bash scripts/lint.sh # 连警告一起判失败
```

最近一次结果：

```
== pyslang: 15 files, 0 errors, 5 warnings ==
== style: 0 violations ==
15/15 files compliant
RESULT: PASS
```

5 条警告全部是**有意的混合位宽乘法/截断**，已在代码行上方用
`// WAIVER(...)` 写明理由，不是未处理项。

---

## 2. Verible：为什么没跑起来

从 GitHub Releases 下载 `verible-v0.0-4296-g0f262651-macOS.tar.gz`（15 MB，
校验为 gzip），解压后执行：

```
$ file verible-v0.0-4296-g0f262651/bin/verible-verilog-lint
verible-verilog-lint: ELF 64-bit LSB executable, ARM aarch64,
  version 1 (GNU/Linux), statically linked, ... for GNU/Linux 3.7.0
$ ./verible-verilog-lint --version
(eval):1: exec format error
```

即：**macOS 资产里装的是 Linux ARM64 二进制**，不是 Mach-O。这不是本机环境
问题，是上游打包问题。本机又没有 Docker/Homebrew，因此无法在本机执行。

在 Linux CI 上应补跑（命令已写进 `scripts/lint.sh` 的可选分支）：

```bash
verible-verilog-lint --rules_config=.verible.rules rtl/*.sv
verible-verilog-format --verify --inplace=false rtl/*.sv
```

`.verible.rules` 已按 Google/CHIPS Alliance 的规则名写好；由于本机无法执行，
**这份规则文件尚未被 Verible 本身验证过**，首次在 CI 上跑时需要
`--help_rules` 核对当前版本的规则名。这一点如实登记，不假装已验证。

---

## 3. RTLCoder：拿到了什么、没拿到什么

仓库：<https://github.com/hkust-zhiyao/RTL-Coder>（IEEE TCAD 2025 / LAD 2024）

### 3.1 实际动作

```bash
git clone --depth 1 git@github.com:hkust-zhiyao/RTL-Coder.git /tmp/rtl_coder   # SSH 成功
# HTTPS 克隆被代理拒绝：CONNECT tunnel failed, response 502
curl https://huggingface.co          # 000（DNS 污染/不可达）
curl https://hf-mirror.com           # 200（镜像可达）
```

- 仓库内容：`benchmark_inference/`（VerilogEval / RTLLM 推理脚本）、
  `dataset/Resyn27k.json`（55 MB，2.7 万条指令-代码对）、`train/`（MLE /
  scoring / 梯度分割）、`data_generation/`。
- 模型：`ishorn5/RTLCoder-v1.1`、`-Deepseek-v1.1`、`-gptq-4bit`、
  `-gguf-4bit`。**权重在 HuggingFace，本机不可达**；镜像虽通，但 4-bit GGUF
  约 4 GB，且 7B 模型在本机 CPU 上生成一个完整模块的时间不可接受，因此
  **没有实际跑模型推理**。这一点不掩饰。

### 3.2 真正被采用的三件事

1. **提示词结构**（来自 `test_on_rtllm.py` / `rtllm-1.1.json`）：
   ```
   Please act as a professional verilog designer.
   <功能描述>
   Module name: <name>
   Input ports:  <逐端口：名字、位宽、含义>
   Output ports: <逐端口：名字、位宽、含义>
   Implementation: <时序/复位/边界条件>
   ```
   本项目所有 Agent 任务书都按这个结构写（见 `docs/coding_standard.md` §11）。

2. **输出后处理**（`scripts/rtl_postprocess.py`）：
   - 取 Markdown 围栏内容；
   - 丢弃第一个 `module` 之前的解释文字；
   - 在 `endmodulemodule` 处截断（Deepseek 版的重复结束符）；
   - 截到最后一个 `endmodule`，缺则补；
   - 可选补 `` `default_nettype none/wire ``；
   - `--check` 直接调 pyslang 做语法门禁。

3. **"生成 → 语法 → 仿真"闭环**：RTLCoder 用 iverilog 做功能门禁；本机没有
   iverilog，用 pyslang 的 elaboration 替代语法门禁，功能验证留给 TB（当前
   无仿真器，如实标注未运行）。

### 3.3 没有采用的部分

- 27K 数据集：与 AD4080 这种"按手册逐位实现"的任务分布不匹配，且样本由
  GPT-3.5 生成、作者自己声明不保证正确。直接检索会引入错误先验。
- 训练脚本：无 GPU、无权重，跑不动。
- VerilogEval / RTLLM 基准：题目是通用小模块（加法器、FIFO、FSM），不能
  验证 AD4080 的寄存器语义与定点位宽。

---

## 4. pyslang 抓到的真实缺陷（这是引入工具的直接收益）

| # | 文件 | 现象 | 根因 | 修复 |
|---|---|---|---|---|
| 1 | `fifo_capture.sv` | 语法错误 | 端口名 `event` 是 SV 保留字 | 改名 `event_in` |
| 2 | `sys_error_corr.sv` | `K_GAIN_S` 变成 −512 | `512` 放进 `signed [9:0]` 溢出 | 改 11 bit |
| 3 | `cal_two_point.sv` | `INV_DX_K` 变成 −24495 | `41041` 放进 `signed [15:0]` 溢出 | 改 18 bit，常数重算为 40961 |
| 4 | `cal_two_point.sv` | 增益基准从 −512 起算 | `10'sd512` 在 10-bit 有符号里是 −512 | 改 `11'sd512` |
| 5 | 全数据通路 | 负半轴算反 | 手册 Table 7 明确输出是**补码**，RTL 按无符号写 | 校正/滤波/事件全部改 signed |

第 5 条是架构级错误：旧版把 `0x80000` 当成接近满幅的正数，任何负输入都会
被校正与滤波彻底算错。它不是 lint 风格问题，是功能错误。

---

## 5. 最终工作流（人 + 多 Agent）

```
需求/手册条目
   │
   ├─ Agent A: 读手册 → docs/review_*.md（只写报告，不改码）
   ├─ Agent B: 读专利/论文 → docs/review_*.md
   │
   ▼
主设计者裁决：每条差异 → 改 / 不改（写明理由）
   │
   ▼
Agent 生成或修改 RTL
   │
   ├─ scripts/rtl_postprocess.py --check   # 输出清洗 + 语法
   ├─ scripts/sv_lint.py                   # elaboration + 风格
   ├─ scripts/check_headers.py             # 文件头完整性
   ▼
人工评审（位宽/符号/复位/CDC/时序假设）
   │
   ▼
提交（一个 commit 只对应一条差异）
```

原则：
- **Agent 不做最终裁决**。审查报告是输入，改不改由主设计者定。
- **没有仿真就说没有仿真**。pyslang 通过 ≠ 功能正确。
- **每条假设有标签**：`ASSUMPTION` / `WAIVER` / `TODO` / `FIXME`。
- **接口冻结**：顶层端口清单改动必须先改文档，再改代码。

---

## 6. 参考

- Verible: <https://github.com/chipsalliance/verible> ·
  lint 规则 <https://chipsalliance.github.io/verible/verilog_lint.html>
- Google SystemVerilog Style Guide:
  <https://chipsalliance.github.io/verible/verilog_lint.html>
- systemverilog.io Style Guide: <https://systemverilog.io/styleguide>
- pyslang / slang: <https://github.com/MikePopoloski/slang>
- RTLCoder: <https://github.com/hkust-zhiyao/RTL-Coder>
- RTLLM: <https://github.com/hkust-zhiyao/RTLLM>
- VerilogEval: <https://github.com/NVlabs/verilog-eval>
