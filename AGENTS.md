# AGENTS.md

## 本 Agent 的职责范围

**仅限两个数字 IP 模块：**

| 编号 | 模块 | 文件 |
|:---|:---|:---|
| D3 | DEM（三维打乱、物理 post-mask） | `rtl/dem_mask_v3.sv` |
| D4 | Split Dither（分路、采样预置 pre-mask） | `rtl/dither_pre.sv` |

## 明确不属于本 Agent

- 18 片池调度、ACQ/CONV/IDLE 状态机
- D2 Follower 事件控制器
- D5 权重校准与 20 位重构
- RA / ADC2 / 参考时序 / 采样沿
- 模拟电荷模型、晶体管电路
- SFDR / 频谱研究
- 28nm 综合、STA、APR
- 物理合同评审、接口合同修改

**越界即停止。**

## 每次开始开发前必须

1. 重读本文件
2. 重读 `docs/CONTRACT_CURRENT.md`（当前合同入口）
3. 确认改动只落在 D3/D4 两个文件及其测试

## 每次提交前必须

1. 重读本文件
2. 运行 `tools/cmp_dem_dither.py`，确认 0 失配
3. 提交信息写明：问题 / 根因 / 改动 / 复现命令
4. 确认没有修改 `3+3+3` 映射、bridge 规则、Dither 符号、电容模型

## 复现命令

```bash
# RTL 产生向量（需要 VCS）
vcs -full64 -sverilog -timescale=1ns/1ps -o simv_m \
    rtl/dem_mask_v3.sv rtl/dither_pre.sv tb/tb_dem_dither.sv
./simv_m > /tmp/mout.txt

# 与 Python 逐位比对
PYTHONPATH=src:tools python3 tools/cmp_dem_dither.py /tmp/mout.txt
```

预期输出：

```
DEM   133376 组 失配 0
Dither    512 组 失配 0
RESULT: PASS
```

## 阶段状态

D3、D4 的 RTL 功能与对拍已完成。顶层集成由顶层负责人接手，本 Agent 不再扩展职责。
