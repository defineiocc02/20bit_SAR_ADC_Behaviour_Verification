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

REVISION = "7408edf8db9a41eebdd756378b69d2775f107dec"
DSP_RTL = "subsys/ad4080_dsp/rtl"
# 源码摘要覆盖临时绑定涉及的依赖，缺少一个叶子也不能代表原 DSP。
SHA256 = {
    "ad4080_top.sv": "de4e2e19d8dde47c96086dc473423a680eea5593819cca60cd753a0149e665c7",
    "ad4080_regs.sv": "ca06793d5e2fd43b73b17d1a3c80bd7610b2c5b889af7d9ddb6d8ebfa9646ddc",
    "ad4080_dsp_chain.sv": "5dcc535b926ef2f06d8abd66a51e1423c8fd78dc2c070be5d73d724572ac3356",
    "event_detect.sv": "6400b704e19f415af0090b422489ffd27a2e7bb811578e4e9f0980229d82030e",
    "fifo_capture.sv": "79d6b79833f778b8fe515a1aba7497e9697b39f5db86a1c1afe2b25d4f51bdf6",
    "gpio_ctrl.sv": "28d91dd00e3ac175a20ba2a9e71a6031eca220d19f7b2e2e7d40b138517a0c47",
    "result_fifo.sv": "c9b4c275465bd31c4b817a2073bdcbe25485914b038e50f8ccbef4cabe1938b1",
    "sinc1_decim.sv": "936cb25b6bc6da9994213741e9a6d03e843eda8c6dbb258271b246510d9e9b79",
    "sinc5_comp.sv": "d246d9cd14d4be3fe17887b78a0e8c1524af3a37b3f4948c4525a54fd953c155",
    "sinc5_decim.sv": "d8c8c5138f807014d08319e7d19ccdfd69ee622d4d5a2fc7417fe9d317dd434b",
    "spi_slave.sv": "37b3d9afeb1eaf21372ea2420bd7c92f465a90c8f81069144b38ff3ccb03a139",
    "sys_error_corr.sv": "45e5022bab911e02a9d3b737b978ba29b1b0daacb7b97377792ae3748fdea3d3",
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
    source = replace_once(source, "    output logic processing_reject\n", """    output logic processing_reject,
    input logic clear_record_i, store_write_ready_i, store_read_valid_i,
    input logic [IN_W-1:0] store_read_data_i,
    output logic store_write_req_o,
    output logic [$clog2(FIFO_DEPTH)-1:0] store_write_addr_o, record_read_addr_o,
    output logic [IN_W-1:0] store_write_data_o,
    output logic record_read_offer_o, capture_incomplete_o, fifo_empty_o, fifo_full_o,
    output logic [14:0] record_read_count_o
""")
    source = replace_once(source, "    logic unused_fifo_full,unused_fifo_empty,unused_fifo_overflow,unused_fifo_underflow;", """    logic unused_fifo_overflow,unused_fifo_underflow;
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
    # 只有十二个输入都与冻结摘要吻合才创建目录，失败时不会留下半套绑定。
    out.mkdir()
    print(f"AD4080 DSP source revision {REVISION}")
    for name, contents in generated.items():
        (out / name).write_bytes(contents)
        print(f"{name} original={SHA256[name]} bound={hashlib.sha256(contents).hexdigest()}")


if __name__ == "__main__":
    main()
