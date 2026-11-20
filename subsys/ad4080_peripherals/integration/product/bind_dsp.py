# =============================================================================
# 文件名     : bind_dsp.py
# 模块名     : bind_dsp
# 版本       : v1.0
# 功能       : 在临时目录从固定 Git 对象绑定 DSP 的单口 SRAM。
# 设计说明   : 仅取必要 DSP 对象。逐文件 SHA 和唯一文本锚点同时校验，
#              原始滤波、阈值、事件和记录状态机来自固定版本。
# -----------------------------------------------------------------------------
# 变更记录
# -----------------------------------------------------------------------------
# 初始版本，暂无变更记录。
# =============================================================================

import argparse
import hashlib
import io
import subprocess
import tarfile
from pathlib import Path

# 各入口共用同一已发布 DSP 源提交，防止台架与产品绑定读到不同的代码。
REVISION = (Path(__file__).resolve().parents[2] / "evidence/dsp-revision.txt").read_text(encoding="ascii").strip()
DSP_RTL = "subsys/ad4080_dsp/rtl"
# 源码摘要覆盖临时绑定涉及的依赖，缺少一个叶子也不能代表原 DSP。
SHA256 = {
    "sinc5_comp_coeff_lut.svh": "33022a5a33888f7f363e5e92ef305605085cf54cbace93c6a72c35d7ce9a1801",
    "ad4080_top.sv": "0ddd925cddd34f89b2cd3c0d47c75baff8e83d3207f5be19bc9772616bf4999a",
    "ad4080_regs.sv": "a7bf1dcd00fc732dd7d4b023a6c99a994328bb9024cbe54964f34489e3545178",
    "ad4080_dsp_chain.sv": "bf8fdbdd7461fe8e566e9c1b142075edcbedf37bb1decc6a4aa805fe66633155",
    "cnv_ctrl.sv": "b63015114f34494479974e9f1f1cacd793bdd18f8e3b0d8084ed356a16bc095a",
    "event_detect.sv": "6400b704e19f415af0090b422489ffd27a2e7bb811578e4e9f0980229d82030e",
    "fifo_capture.sv": "79d6b79833f778b8fe515a1aba7497e9697b39f5db86a1c1afe2b25d4f51bdf6",
    "gpio_ctrl.sv": "1ab1be85db05e792730194b49737a33f31252d47746a10d16476bbe996759255",
    "result_fifo.sv": "c9b4c275465bd31c4b817a2073bdcbe25485914b038e50f8ccbef4cabe1938b1",
    "sinc1_decim.sv": "936cb25b6bc6da9994213741e9a6d03e843eda8c6dbb258271b246510d9e9b79",
    "sinc5_comp.sv": "5cf173051e275764beb02f5ca81b3ec30a4028220f6b5912d3468adb1e88fa0d",
    "sinc5_decim.sv": "fe70cc10d4854be8339cdfc95a3377fabf0477393377a4db680de7042d4900e5",
    "spi_slave.sv": "a6d8cd29064e67d492b1f583920478f84c7cd5c8fa88c023adc12043558beca7",
    "sys_error_corr.sv": "035c5c869c14b8e4555855f05251c673327685f8f447c487fb91b111fc3d3f80",
}


# =============================================================================
# 1. 拒绝基线变化和歧义接缝
# =============================================================================
def replace_once(source: str, old: str, new: str) -> str:
    """按固定块原样替换，操作只作用于内存中的 archive 内容。"""
    # 同一锚点出现两次意味着上游接口变了；此时需要重新核对端口所有者。
    if source.count(old) != 1:
        raise ValueError(f"source anchor does not match exactly once: {old!r}")
    return source.replace(old, new, 1)


def bind_fifo(source: str) -> str:
    """保留队列指针和空满运算，物理写受理驱动原指针，读沿来自外部已受理请求。"""
    source = replace_once(source, "    output logic overflow, underflow, access_reject\n", """    output logic overflow, underflow, access_reject,
    // [单口绑定] 实际 ready 只判断写受理。rd_en 已是外部实际接受的读。
    input logic store_write_ready_i, store_read_valid_i,
    input logic [DATA_W-1:0] store_read_data_i,
    output logic store_write_req_o, store_write_taken_o,
    output logic [DATA_W-1:0] store_write_data_o,
    output logic [$clog2(DEPTH)-1:0] store_write_addr_o, queue_read_addr_o
""")
    source = replace_once(source, "    wire write_fire=rst_n && !clear && !collision && wr_en && (!full || overwrite_en);",
                         """    // 写请求与最终读授权分离，避免单口反馈形成组合环。
    assign store_write_req_o=rst_n && !clear && wr_en && (!full || overwrite_en);
    wire write_fire=store_write_req_o && store_write_ready_i && !collision;
    assign store_write_taken_o=write_fire;
    assign store_write_data_o=wr_data;
    assign store_write_addr_o=wr_ptr[MEM_AW-1:0];
    assign queue_read_addr_o=rd_ptr[MEM_AW-1:0];""")
    source = replace_once(source, '    (* ram_style="block" *) logic [DATA_W-1:0] mem [0:DEPTH-1];\n', "")
    source = replace_once(source, """    always_ff @(posedge clk) begin
        if (write_fire) mem[wr_ptr[MEM_AW-1:0]]<=wr_data;
        rd_valid<=read_fire || snapshot_fire;
        if (snapshot_fire) rd_data<=mem[snapshot_addr];
        else if (read_fire) rd_data<=mem[rd_ptr[MEM_AW-1:0]];
    end
""", """    // [返回] 禁止把同域任意响应当成队列读。已显式提交的请求有一拍回送。
    logic read_expected_q;
    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) read_expected_q <= 1'b0;
        else read_expected_q <= read_fire || snapshot_fire;
    end
    assign rd_valid = read_expected_q && store_read_valid_i;
    assign rd_data = store_read_data_i;
""")
    return replace_once(source, "    input logic [$clog2(DEPTH)-1:0] snapshot_addr,\n", "")


def bind_capture(source: str) -> str:
    """外引原冻结记录指针、长度和物理写。写完成以真实单口握手为准。"""
    source = replace_once(source, "    output logic [3:0] cfg_reject_reason\n", """    output logic [3:0] cfg_reject_reason,
    input logic store_write_ready_i, store_read_valid_i,
    input logic [DATA_W-1:0] store_read_data_i,
    output logic store_write_req_o,
    output logic [DATA_W-1:0] store_write_data_o,
    output logic [$clog2(DEPTH)-1:0] store_write_addr_o, record_read_addr_o,
    output logic record_read_offer_o,
    output logic [14:0] record_read_count_o
""")
    source = replace_once(source, "    wire write_fire=capture_enabled && valid_in && !clear_capture;", """    wire write_offer=capture_enabled && valid_in && !clear_capture;
    logic write_fire; // 快照位置和完成状态只前进一次真实写
    logic [MEM_AW-1:0] queue_read_addr;
    assign record_read_offer_o = captured && !read_done && !clear_capture && (overwrite || !empty);
    assign record_read_count_o = read_target - read_count;
    assign record_read_addr_o = overwrite ? snapshot_addr : queue_read_addr;""")
    return replace_once(source, """        .wr_en(write_fire),.overwrite_en(overwrite),.wr_data(data_in),
        .rd_en(queue_read),.snapshot_rd_en(snapshot_read),.snapshot_addr(snapshot_addr),""", """        .wr_en(write_offer),.overwrite_en(overwrite),.wr_data(data_in),
        .rd_en(queue_read),.snapshot_rd_en(snapshot_read),
        .store_write_ready_i,.store_read_valid_i,.store_read_data_i,
        .store_write_req_o,.store_write_addr_o,.store_write_data_o,
        .store_write_taken_o(write_fire),.queue_read_addr_o(queue_read_addr),""")


def bind_top(source: str) -> str:
    """只传递物理存储及记录接点，原配置、数值和触发实例保持原连接。"""
    # 芯片事件、数值与配置均由来源提交定义。此步骤只外引真实存储和记录状态。
    # 外部看 FULL 的上升和记录清除沿，不能从以后才送到串行器的尾部反推。
    source = replace_once(source, "    output logic processing_reject\n", """    output logic processing_reject,
    input logic clear_record_i, store_write_ready_i, store_read_valid_i,
    input logic [IN_W-1:0] store_read_data_i,
    output logic store_write_req_o,
    output logic [$clog2(FIFO_DEPTH)-1:0] store_write_addr_o, record_read_addr_o,
    output logic [IN_W-1:0] store_write_data_o,
    output logic record_read_offer_o, capture_incomplete_o, capture_fifo_full_o, capture_metadata_clear_o, fifo_empty_o, fifo_full_o,
    output logic [14:0] record_read_count_o
""")
    source = replace_once(source, "    logic unused_fifo_full,unused_fifo_empty,unused_fifo_overflow,unused_fifo_underflow;", """    logic unused_fifo_overflow,unused_fifo_underflow;
    assign capture_metadata_clear_o = fifo_clear || clear_record_i;
    assign capture_fifo_full_o = full_flag;
    assign capture_incomplete_o = capture_incomplete;""")
    source = replace_once(source, ".clear_capture(fifo_clear)", ".clear_capture(fifo_clear || clear_record_i)")
    source = replace_once(source, ".full(unused_fifo_full),.empty(unused_fifo_empty),", ".full(fifo_full_o),.empty(fifo_empty_o),")
    return replace_once(source, "        .cfg_reject(capture_reject),.cfg_reject_reason(capture_reason)\n", """        .cfg_reject(capture_reject),.cfg_reject_reason(capture_reason),
        .store_write_ready_i,.store_read_valid_i,.store_read_data_i,
        .store_write_req_o,.store_write_addr_o,.store_write_data_o,
        .record_read_offer_o,.record_read_addr_o,.record_read_count_o
""")


# =============================================================================
# 2. 只读提取与生成收据
# =============================================================================
def main() -> None:
    """输入受保护仓路径和新目标目录，输出临时 RTL 及逐文件原/绑定哈希。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    repo, out = args.repo.resolve(), args.out.resolve()
    if repo == out or repo in out.parents or out.exists():
        raise SystemExit("binding output must be a fresh path outside the source repository")
    # 从固定提交取文件可绕开其他维护者当下尚未提交的实验改动。
    names = [f"{DSP_RTL}/{name}" for name in SHA256]
    snapshot = subprocess.check_output(["git", "-C", str(repo), "archive", REVISION, *names])
    bind = {"result_fifo.sv": bind_fifo, "fifo_capture.sv": bind_capture, "ad4080_top.sv": bind_top}
    # 宏接口和数组的更换仅在这三处进行，系数头与数值叶逐字保留。
    generated = {}
    # 逐个读取已列名的成员，使提交中的其他归档路径不参与交付。
    with tarfile.open(fileobj=io.BytesIO(snapshot)) as archive:
        for name, expected in SHA256.items():
            member = archive.extractfile(f"{DSP_RTL}/{name}")
            if member is None:
                raise ValueError(f"missing file {name}")
            raw = member.read()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError(f"upstream hash mismatch for {name}")
            generated[name] = bind[name](raw.decode()).encode() if name in bind else raw
    # 仅当清单中的输入都与冻结摘要吻合时才创建目录，失败时不会留下半套绑定。
    out.mkdir()
    print(f"AD4080 DSP source revision {REVISION}")
    # 同时记录绑定前后的摘要，便于区分寄存器原件与用于四银行的地址接缝。
    for name, contents in generated.items():
        (out / name).write_bytes(contents)
        print(f"{name} original={SHA256[name]} bound={hashlib.sha256(contents).hexdigest()}")


if __name__ == "__main__":
    main()
