# RTL 仿真入口

从仓库根目录运行。先按 DUT 选择路线，结果按该路线的测试输入、模拟器与判据解读。

| 对象 | 入口 | 判据 |
| --- | --- | --- |
| v21 数字顶层 `adc_digital_ip_v21` | [verify_v21.sh](../../tools/verify_v21.sh) · [环境说明](../../docs/v21/I5_ENVIRONMENT.md) | 分项 VCS 回归、事务与 ADC2 节点宏对拍，以及脚本单列的整数检查。 |
| v21 与旧 IP 复用的 D3/D4 叶子 | `make -f flow/Makefile verify-d3d4` · [verify_d3d4.sh](../../tools/verify_d3d4.sh) | DEM 掩码、分路扰动的独立向量对拍。 |
| 保留的 `d3d4_ip`、`digital_top_w` | `make -f flow/Makefile verify-d3d4-ip` · [run_topw.sh](../../tools/run_topw.sh) | 分别对应旧 IP 组合验收和旧权重链回归。两者的测试台、数据与 v21 分开。 |
| 独立 `sar20_digital_core`、定点子模块与 DSP | [run_open_rtl.py](../../tools/run_open_rtl.py) · [CI 中的组件命令](../../.github/workflows/ci.yml) | 由测试台选择和显式输入包限定范围，包含定点、协议、源绑定及 CAAZ 标签检查。 |
| Fig.19 理想静态节点 | [Fig.19 fixture](fixtures/fig19_ideal_ports/README.md) · [慢测用例](../tests/integration/test_source_bound_replay.py) | 指定几何与名义系数下的 DUT 捕获及回放。 |

v21 及旧 D3/D4 的 VCS 入口见各脚本，直接选源时看 [编译清单](../build/filelists/README.md)。v21 脚本会重建其配置的 `D3D4_WORKDIR`，因此该路径应指向该次回归专用的临时目录。

## 独立组件如何调用

```bash
python3 tools/run_open_rtl.py --help
python3 tools/run_rtl_sim.py --help
```

`run_open_rtl.py` 可选择 Verilator、VCS 或 Icarus Verilog，具体测试台的支持情况以脚本为准。调用时显式指定 `--tops` 与尚未创建的仓库外 `--out`。`run_rtl_sim.py` 固定选择本机 VCS，由调用者准备 VCS 与许可证。[run_vcs.sh](run_vcs.sh) 是同一个本机 VCS 入口的包装器，参数直接传递给 Python。

源绑定测试台还要求 `--inputs-dir`、系数、向量或格式文件等具体输入。组合与判据以 [CI 命令](../../.github/workflows/ci.yml) 和对应测试台为准。给输出目录分配独立路径，通过退出状态、日志及该次运行的报告核对实际完成情况。 [分层验证说明](../verification/README.md)

## 目录定位

- [tb/](tb/) 放独立 core、算术和协议测试台。[仓库根的 tb/](../../tb/) 有 v21、D3/D4 与早期链测试台。
- [fixtures/](fixtures/) 放显式合成输入和绑定。每组样例只用于声明的几何、位宽与协议。
- [ref/](ref/) 放独立定点参考，[smoke/](smoke/) 放仿真器通路的小例子。

早期 P1/P2/P3 命令、覆盖账本和 SVA 排查记录可用 `git show 391c55a:flow/sim/README.md` 回看。当前结果入口见[复现状态](../../docs/STATUS.md)。
