# 版本与变更记录

## 用哪份代码复现

| 读到的名称 | 它标识什么 |
| --- | --- |
| `merge/adi-repro` | 数字 IP 的开发分支。分支会向前移动，引用一次试验请附当时的完整 Git 提交号。 |
| [9.0.0](../../pyproject.toml) | 当前源码生成的 Python 分发包版本，由 [pyproject.toml](../../pyproject.toml) 定义。对应的软件作者、许可和引用方式见 [CITATION.cff](CITATION.cff)。 |
| `adc_digital_ip_v21` | SystemVerilog 顶层模块名。默认 Python 按这套顶层的开关、逐位判决、权重和定点规则出码。[生产清单](../../flow/build/filelists/files_adc_v21.f) |

截至 2026-11-19，GitHub 最近一次正式发布仍是 `v8.2.2`，标签指向 `cad5114`。它保存了当时的行为模型。复核下表的数字 IP 结果时，请取表中对应的提交。[正式发布](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/releases/tag/v8.2.2) · [全部发布](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/releases)

## 数字 IP 的主要变更

| 日期 | 提交 | 对使用者的影响 |
| --- | --- | --- |
| 2026-11-19 | [`d97afa1`](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/commit/d97afa1) | 安装包和命令行都执行生产顶层的事务模型。五组共 1,792 笔、每笔 36 个字段通过 Python 与 RTL 对拍。[输入与结果](../audit/AUDIT_ALGO_VS_SOURCE.md) |
| 2026-11-19 | [`8db9a30`](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/commit/8db9a30) | 修复采集与转换交接时的片选，以及定点重构错误位的样本归属。[触发条件与测例](../audit/ISSUE_10.md) |
| 2026-11-17 | [`b878f58`](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/commit/b878f58) | 生产构建统一到一个集成顶层，移除旧整链入口。[RTL 目录与实例](../../rtl/README.md) |

图册按一笔转换、交接沿、失配校正和全码线性度组织正文。图中作用句限 30 字，原图、工程假设与逐笔观测另列。顶层数据固定在 `3057d2f`。[阅读图册与核对原件](../algorithms/README.md)

## 在本地核对

```bash
git branch --show-current
git rev-parse HEAD
git tag --points-at HEAD
git ls-remote --refs --tags origin 'refs/tags/v9*' 'refs/tags/v8.2.2'
git show 'v8.2.2^{commit}' -s --format='%H %cs %s'
git grep -n -E '^version ?[=:]' -- pyproject.toml docs/meta/CITATION.cff
```

旧发布的修订说明、审计明细及当时的文件摘要可以从 Git 逐字取回。离线检出可用 `git show d97afa1:docs/meta/CHANGELOG.md`。[整理前的完整变更记录](https://github.com/defineiocc02/20bit_SAR_ADC_Behaviour_Verification/blob/d97afa160a7c9c0ff2f1427656e5899b360771e2/docs/meta/CHANGELOG.md)
