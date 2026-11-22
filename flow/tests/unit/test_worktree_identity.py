# =============================================================================
# 文件名     : test_worktree_identity.py
# 功能       : 从普通 Tcl 和临时 Git 检出验证生产选源、产物配对与清理边界。
# 设计说明   : stub 只记录工具原本会收到的文件，在 elaboration 前终止；
#              不调用 DC、FC、Formality，不用 PDK，也不把检查解释为等价通过。
# =============================================================================
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from adi_model.cli import main_simulate, main_validate_config

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = Path("rtl/filelists/files_adc_v21.f")
BACKEND = Path("flow/synth/v21")


def tcl(script, root, work, **extra):
    """从有空格的非源码目录启动。若脚本暗中依赖调用目录，得到的文件序列会不同。"""
    # 仅调用通用 Tcl 解释器。未安装它的平台不应把这个测试记作商业工具覆盖。
    if not shutil.which("tclsh"):
        pytest.skip("standalone tclsh required for source-selection checks")
    # 覆盖目录和 PDK 输入，以免继承开发机的真实工件位置。所有输出只写 pytest 临时目录。
    env = dict(
        os.environ,
        CHECKOUT=str(root),
        V21_DC_DIR=str(work / "dc"),
        V21_FC_DIR=str(work / "fc"),
        V21_PDK_ROOT=str(work / "pdk"),
        **extra,
    )
    wrapper = work / "probe.tcl"
    wrapper.write_text(script)
    return subprocess.run(
        ["tclsh", str(wrapper)], cwd=work, env=env, capture_output=True, text=True
    )


@pytest.fixture
def checkout(tmp_path):
    # 每个参数化用例拥有独立 DC 产物与索引，测试之间不复用已完成收据。
    root = tmp_path / "check out"
    work = tmp_path / "run here"
    # 源码检出与运行目录各自含空格，绝对路径和 Tcl list 元素不能靠空格拆分。
    work.mkdir()
    (root / MANIFEST.parent).mkdir(parents=True)
    # 复制入口原件而非重写其 Tcl，这样测试能发现某个实际流程绕过公共解析器。
    shutil.copytree(ROOT / BACKEND, root / BACKEND)
    # 正式清单在临时检出多一项真实可读的探针文件。逐项手抄的脚本会立即漏掉它。
    names = [line for line in (ROOT / MANIFEST).read_text().splitlines() if line.startswith("rtl/")]
    # 探针插在清单内部。只做集合比较时无法发现错误重排，因此后面按有序列表验证。
    names.insert(1, "rtl/selection_probe.sv")
    # 留下空行与注释，以便验证 Tcl/仿真清单在这些合法输入上的处理一致。
    (root / MANIFEST).write_text("// fixture\n\n" + "\n".join(names) + "\n")
    for name in names:
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"// fixture: {target.stem}\n")
    return root, work, names


# 仅构造成品字节和原子收据。没有网表语义或商用单元库，不能用它替代门级等价证据。
# 在新检出中用相同内容可验过摘要，以排除绝对路径导致的伪差异。
CREATE_PRODUCTS = """
source [file join $::env(CHECKOUT) flow synth v21 design_inputs.tcl]
set dir [v21::dc_dir]
set top adc_digital_ip_v21
v21::begin_dc $dir $top
foreach file [list ${top}_syn.v ${top}.svf] {
    set fd [open [file join $dir $file] w]; puts $fd "synthetic fixture $file"; close $fd
}
v21::finish_dc $dir $top
"""


@pytest.mark.parametrize("entry", ["dc_synth_v21", "fc_rtl_to_gds", "fm_equiv"])
def test_actual_backend_entries_use_the_ordered_checkout_manifest(checkout, entry):
    root, work, names = checkout
    # 给 FM 一份由同一 Tcl 生成并封存的模拟输入，确保实际经过配对检查。
    if entry == "fm_equiv":
        receipt = tcl(CREATE_PRODUCTS, root, work)
        assert receipt.returncode == 0, receipt.stderr
    # 抛出这个哨兵表示刚好收完源码。若先开始综合或读取到另一个网表，则不会得到它。
    probe = """
foreach cmd {set_app_var set_svf create_lib read_db suppress_message} {proc $cmd args {return 1}}
foreach cmd {analyze read_sverilog} {proc $cmd args {puts "INPUT [lindex $args end]"; return 1}}
foreach cmd {elaborate set_top} {proc $cmd args {error STOP_AFTER_SOURCES}}
if {![catch {source [file join $::env(CHECKOUT) flow synth v21 $::env(ENTRY).tcl]} why] ||
    $why ne "STOP_AFTER_SOURCES"} {error "unexpected entry result: $why"}
"""
    result = tcl(probe, root, work, ENTRY=entry)
    assert result.returncode == 0, result.stderr
    # 三条流程只允许在预期位置被截停；脚本提前报错或吞掉读取失败都会丢失文件。
    seen = [Path(row[6:]) for row in result.stdout.splitlines() if row.startswith("INPUT ")]
    assert seen == [root / name for name in names]


@pytest.mark.parametrize(
    "changed",
    ["rtl/selection_probe.sv", "adc_digital_ip_v21_syn.v", "adc_digital_ip_v21.svf", "manifest"],
)
def test_receipt_rejects_a_mixed_source_or_product(checkout, changed):
    root, work, _ = checkout
    assert tcl(CREATE_PRODUCTS, root, work).returncode == 0
    check = "source [file join $::env(CHECKOUT) flow synth v21 design_inputs.tcl]\nv21::check_dc [v21::dc_dir] adc_digital_ip_v21"
    assert tcl(check, root, work).returncode == 0
    # 保留产品名和文件大小附近的形态，只更改内容。仅按扩展名或时间判断会误放行。
    target = (
        root / MANIFEST
        if changed == "manifest"
        else (root if "/" in changed else work / "dc") / changed
    )
    # 改一个字节，即使网表和文件名完全相同，也须拒绝错配。
    with target.open("a") as out:
        out.write("// changed\n")
    # 检查要给出不一致的原因，不能把某个无关的 Tcl 异常算作成功拦截。
    failure = tcl(check, root, work)
    assert failure.returncode and "receipt differs" in failure.stderr


def test_cleaner_preserves_tracked_files_and_nested_repositories(tmp_path):
    root = tmp_path / "temporary checkout"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # 根外层的存证用来证明保护来自索引；不能只把 flow/tests 写进白名单就算通过。
    tracked = ["flow/tests/fixtures/a.rpt", "artifacts/frozen.log", "work/source.sv"]
    for name in tracked + ["scratch/output.log", "simv-dir/nested/.git/HEAD", "csrc/cache"]:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture")
    subprocess.run(["git", "add", "--", *tracked], cwd=root, check=True)
    cmd = [sys.executable, str(ROOT / "flow/scripts/clean.py"), "--root", str(root)]
    # 预览须查询 Git 而不是读一份人工维护的固定路径名单，产物与存证允许扩展名相同。
    preview = subprocess.check_output(cmd, text=True, cwd=tmp_path)
    assert "scratch/output.log" in preview and "artifacts/frozen.log" not in preview
    assert (root / "scratch/output.log").exists()
    # 仅在这份一次性自建仓库测试删除；命中目录中只要有跟踪源或嵌套仓库就要保留。
    subprocess.run([*cmd, "--apply"], check=True, capture_output=True, cwd=tmp_path)
    assert all((root / name).is_file() for name in tracked + ["simv-dir/nested/.git/HEAD"])
    assert not (root / "csrc").exists() and not (root / "scratch/output.log").exists()


def test_preflight_and_simulation_accept_the_same_production_inputs(tmp_path, capsys):
    params, overrides = tmp_path / "params.json", tmp_path / "controls.json"
    # 两份输入分别代表接口几何和控制开关。预检既要读到覆盖项，也要检查同一厂模的固定字段。
    params.write_text('{"adc2_n_bits":13,"n_main":16,"n_sub":4}')
    overrides.write_text('{"dem_enable":false}')
    args = ["--parameters", str(params), "--config-overrides", str(overrides)]
    assert main_validate_config(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["scope"] == "adc_digital_ip_v21" and not result["model_run"]
    assert main_simulate([*args, "--samples", "1"]) == 0
    capsys.readouterr()
    # 旧 ADC2 宽度既不可作为预检有效输入，也不能从实际命令运行。
    params.write_text('{"adc2_n_bits":12}')
    assert main_validate_config(args) == 2
    assert main_simulate([*args, "--samples", "1"]) == 2


def test_public_profile_and_python_default_select_the_same_geometry():
    """配置注册和包级导出必须指向同一个生产背板，独立统计原型不参与此比较。"""
    from adi_model import default_model, simulate
    from adi_model import defaults
    from adi_model.contracts.profiles import DEFAULT_PROFILE_ID, PROFILES, load_profile

    assert default_model is defaults.default_model and simulate is defaults.simulate
    # 统计原型和独立 DSP 有自己的名字。只数顶层目标注册项，不按包中类的数量判版本。
    assert set(PROFILES) == {DEFAULT_PROFILE_ID}
    target = load_profile(DEFAULT_PROFILE_ID)
    assert target.topology.topology_id == "adc_digital_ip_v21"
    geom = defaults.DefaultParameters()
    assert (
        (target.topology.n_main, target.topology.n_sub, target.adc2_bits)
        == (
            geom.n_main,
            geom.n_sub,
            geom.adc2_n_bits,
        )
        == (16, 4, 13)
    )


@pytest.mark.parametrize(
    "tool_rc,verdict,expected",
    [(0, "SUCCEEDED", 0), (0, "FAILED", 1), (124, "SUCCEEDED", 2), (1, "SUCCEEDED", 1)],
)
def test_formal_wrapper_binds_staged_checkout_before_accepting_status(
    tmp_path, tool_rc, verdict, expected
):
    """驱动替身验证暂存目录和退出判据。打印的假判定只供测试此 shell 包装。"""
    assert tcl(CREATE_PRODUCTS, ROOT, tmp_path).returncode == 0
    work, bins = tmp_path / "fm stage", tmp_path / "bin"
    bins.mkdir()
    # 保证本测试即使运行在装有商业工具的机器也只执行此处生成的替身。
    # 它真实核对暂存清单和收据，可以检出只上传 Tcl、错读其他检出的问题。
    simulator = bins / "fm_shell"
    simulator.write_text("""#!/usr/bin/env bash
set -eu
tclsh flow/synth/v21/design_inputs.tcl --check-dc "$V21_DC_DIR"
printf 'STEP_INPUTS_OK\\nVerification %s\\nSTEP_VERIFY_DONE\\n' "$TEST_FM_VERDICT"
exit "$TEST_FM_RC"
""")
    # 替身仍保留工具进程与外层 shell 的退出码边界，假 SUCCEEDED 会与非零 rc 对照。
    simulator.chmod(0o755)
    # 超时分支只检查退出语义，不故意让测试等到时钟到期。
    timeout = bins / "timeout"
    timeout.write_text('#!/bin/sh\nshift\nexec "$@"\n')
    timeout.chmod(0o755)
    env = dict(
        os.environ,
        PATH=str(bins) + os.pathsep + os.environ["PATH"],
        V21_DC_DIR=str(tmp_path / "dc"),
        V21_WORKDIR=str(work),
        V21_RUNNER="",
        D3D4_RUNNER="",
        TEST_FM_RC=str(tool_rc),
        TEST_FM_VERDICT=verdict,
    )
    # 实际工厂脚本接收短预算；替身负责触发状态组合，而不是等待十五秒超时。
    cmd = ["bash", str(ROOT / "tools/check_formal.sh"), "15"]
    result = subprocess.run(cmd, env=env, cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == expected, (result.stdout, result.stderr)
    assert ("RESULT: FORMAL_EQUIV_PASS" in result.stdout) == (expected == 0)
    assert (work / MANIFEST).read_bytes() == (ROOT / MANIFEST).read_bytes()
    # 失败日志和成功日志都属于原运行；重试不能通过重建相同目录擦掉其中任何一种。
    log = (work / "fm.log").read_bytes()
    # 第二次指定同一运行路径应保留首轮日志和输入，不递归清理旧目录。
    assert subprocess.run(cmd, env=env, cwd=tmp_path, capture_output=True).returncode != 0
    assert (work / "fm.log").read_bytes() == log


def test_remote_preview_requires_target_and_uses_the_current_cleaner(tmp_path):
    """本地 SSH 替身只打开临时 Git 检出，验证参数保真和默认预览。"""
    root = tmp_path / "owner's checkout"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    # 保留已跟踪报告和一个未跟踪日志，两个对象的预览行为要不同。
    (root / "frozen.rpt").write_text("stored")
    (root / "new.log").write_text("untracked")
    subprocess.run(["git", "add", "frozen.rpt"], cwd=root, check=True)
    # 最后一个 SSH 参数是由登录 shell 解释的完整命令，stdin 为当前清理脚本。
    # 路径含空格和引号，简单拼接本地字符串会在这个用例选错根。
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/usr/bin/env bash\nexec sh -c "${!#}"\n')
    ssh.chmod(0o755)
    env = dict(
        os.environ, PATH=str(tmp_path) + os.pathsep + os.environ["PATH"], REMOTE_HOST="fixture"
    )
    # 缺参在 SSH 前结束。无 --apply 时即使有未跟踪输出，也只报告候选。
    script = ["bash", str(ROOT / "flow/scripts/clean_remote.sh")]
    assert subprocess.run(script, cwd=tmp_path, env=env, capture_output=True).returncode == 2
    run = subprocess.run(
        [*script, "--root", str(root)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "new.log" in run.stdout and "frozen.rpt" not in run.stdout
    assert (root / "new.log").is_file() and (root / "frozen.rpt").is_file()


def test_directory_gate_rejects_equal_counts_with_different_ownership(tmp_path):
    """用小型 Git 工程走实际 CLI，匹配文件数量不能掩盖错目录或重复选源。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    # Git 不记录空目录，每个公开目录放一个文件，得到和读者实际可见相同的目录树。
    for directory in (".github", "archive", "experiments", "flow", "references", "rtl", "src", "tb", "tools"):
        (tmp_path / directory).mkdir()
        (tmp_path / directory / "entry.txt").write_text("fixture")
    for name in ("README.md", "Makefile", "pyproject.toml", ".gitignore", ".gitattributes"):
        (tmp_path / name).touch()
    (tmp_path / "docs").mkdir()
    for name in ("INDEX.md", "STATUS.md", "CONTRACT_CURRENT.md"):
        (tmp_path / "docs" / name).touch()
    # 目录职责与生产/仿真清单分别校验。临时模块的名字不绑定实际项目的硬编码计数。
    source = "rtl/sar_core.sv"
    (tmp_path / source).write_text("module core; endmodule\n")
    (tmp_path / "rtl/rtl_sources.f").write_text(source + "\n")
    (tmp_path / MANIFEST.parent).mkdir(parents=True)
    (tmp_path / MANIFEST).write_text(source + "\n")
    sim = tmp_path / MANIFEST.with_name("files_adc_v21_sim.f")
    sim.write_text(f"-f {MANIFEST}\n")
    script = tmp_path / "tools/check_doc_counts.py"
    shutil.copyfile(ROOT / "tools/check_doc_counts.py", script)
    command = [sys.executable, str(script), "--check"]
    assert subprocess.run(command, capture_output=True).returncode == 0
    # 保持 10 个目录和 5 个文件，但读者已经找不到约定的 src 入口。
    (tmp_path / "src").rename(tmp_path / "wrong")
    failure = subprocess.run(command, text=True, capture_output=True)
    assert failure.returncode and "root directories" in failure.stdout
    (tmp_path / "wrong").rename(tmp_path / "src")
    # 测试台应继承生产清单。即使文件确实存在，拷贝其中一行也会令编译器重复读模块。
    with sim.open("a") as out:
        out.write(source + "\n")
    failure = subprocess.run(command, text=True, capture_output=True)
    assert failure.returncode and "repeated production sources" in failure.stdout
    sim.write_text(f"-f {MANIFEST}\n")
    # 即使清单与磁盘完全一致，第二份文件也可能用不同文件名声明同一个模块。
    (tmp_path / "rtl/other.sv").write_text("module core; endmodule\n")
    (tmp_path / "rtl/rtl_sources.f").write_text(source + "\nrtl/other.sv\n")
    failure = subprocess.run(command, text=True, capture_output=True)
    assert failure.returncode and "duplicate RTL module core" in failure.stdout



def test_missing_dc_artifact_is_rejected_before_work_directory_creation(tmp_path):
    """缺少 SVF 时的错误本身包含 .svf，必须按文件状态拒绝并保留工作路径。"""
    dc, work = tmp_path / "dc", tmp_path / "new run"
    dc.mkdir()
    (dc / "adc_digital_ip_v21_syn.v").write_text("netlist fixture")
    env = dict(os.environ, V21_DC_DIR=str(dc), V21_WORKDIR=str(work), V21_RUNNER="", D3D4_RUNNER="")
    cmd = ["bash", str(ROOT / "tools/check_formal.sh")]
    # 预检失败发生在加载程序与许可之前，因此也可在没有任何 EDA 工具的环境重现。
    failure = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert failure.returncode != 0 and "missing DC artifact" in failure.stderr
    assert ".svf" in failure.stderr and not work.exists()
