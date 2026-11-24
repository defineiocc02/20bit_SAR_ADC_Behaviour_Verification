`timescale 1ns/1ps
// =============================================================================
// 文件名     : tb_ad4080_silicon_periph_closure.sv
// 模块名     : tb_ad4080_silicon_periph_closure
// 版本       : v1.0
// 功能       : 端到端硬件自检全部 4 项外围流片边界闭环模块：
//              1) ad4080_crc24_service（5 组 Python crc24.py 黄金向量 + 撤销/未绑定反例）
//              2) ad4080_otp_macro_adapter + 真实 otp_shadow_loader（加载发布 + 1-bit 篡改触发 ROM_CRC_ERR）
//              3) ad4080_result_store_foundry（4×4096×20 跨银行读写 + 写保持 + 睡眠钳位）
//              4) ad4080_lvds_phy_wrapper（400 MHz DDR 双沿发射 + 0°/90°/180° DCO 相位 + self/echo OE）
// 设计说明   :
//   * [独立黄金对拍] 全部 5 组 CRC-24 期望尾字及 OTP 3 字镜像 CRC-24（0xD537E3）均由
//     仓内 io/adi_crc/crc24.py 离线独立生成，验证 RTL 与 Python 位级完全等价。
//   * [真实模块联调] OTP 测试直接例化生产模块 rtl/ad4080_periph/otp/otp_shadow_loader.sv
//     与 ad4080_otp_macro_adapter 互连，验证完整冷启动握手、CRC 失败阻断与冲刷重入。
// 变更记录   :
//   2026-11-24  v1.0 初版。外围流片硬件边界全套自检台架。
// =============================================================================
module tb_ad4080_silicon_periph_closure;

    // =========================================================================
    // 1. 模块 1：ad4080_crc24_service 硬件对拍（5 组 crc24.py 黄金向量）
    // =========================================================================
    logic        clk_io, rst_io_n;
    logic        crc_bound, crc_inc_hdr, crc_refout;
    logic [23:0] crc_poly, crc_init, crc_xorout, crc_out;
    logic [1:0]  crc_worder, crc_pair, crc_kind;
    logic        crc_epoch, crc_start, crc_cancel, crc_ready;
    logic        crc_bvalid, crc_bready;
    logic        crc_fmt_bound, crc_svc_ready, crc_valid, crc_err;

    ad4080_crc24_service u_crc24 (
        .clk_io_i            (clk_io),
        .rst_ni              (rst_io_n),
        .cfg_profile_bound_i (crc_bound),
        .cfg_poly_i          (crc_poly),
        .cfg_init_i          (crc_init),
        .cfg_xorout_i        (crc_xorout),
        .cfg_include_header_i(crc_inc_hdr),
        .cfg_refout_i        (crc_refout),
        .cfg_wire_order_i    (crc_worder),
        .io_record_epoch_i   (crc_epoch),
        .crc_start_i         (crc_start),
        .crc_cancel_i        (crc_cancel),
        .crc_ready_i         (crc_ready),
        .beat_valid_i        (crc_bvalid),
        .beat_ready_i        (crc_bready),
        .beat_pair_i         (crc_pair),
        .beat_kind_i         (crc_kind),
        .format_bound_o      (crc_fmt_bound),
        .crc_service_ready_o (crc_svc_ready),
        .crc_valid_o         (crc_valid),
        .crc_error_o         (crc_err),
        .crc_o               (crc_out)
    );

    task automatic io_tick();
        #1.25; clk_io = 1'b1; #1.25; clk_io = 1'b0;
    endtask

    // 跑一条含 0xAA 头 + 两笔样本 [0x135A9, 0xE62B4] 的 LVDS DDR 帧并核对末载荷沿零等待 CRC
    task automatic check_crc_case(
        input string       name,
        input logic [23:0] poly,
        input logic [23:0] init,
        input logic [23:0] xorout,
        input logic        inc_hdr,
        input logic        refout,
        input logic [1:0]  worder,
        input logic [23:0] expected_wire
    );
        logic [7:0]  hdr;
        logic [39:0] pay;
        logic [23:0] captured_crc;
        hdr = 8'hAA;
        pay = {20'h135A9, 20'hE62B4};

        crc_bound   = 1'b1; crc_poly   = poly;   crc_init = init;
        crc_xorout  = xorout; crc_inc_hdr = inc_hdr; crc_refout = refout;
        crc_worder  = worder; crc_cancel = 1'b0; crc_ready = 1'b0;
        crc_bvalid  = 1'b0;   crc_bready = 1'b1; crc_pair  = 2'd0; crc_kind = 2'd0;

        if (!crc_fmt_bound || !crc_svc_ready)
            $fatal(1, "CRC case %s not bound or not ready", name);

        // 1) 启动会话
        crc_start = 1'b1;
        io_tick();
        crc_start = 1'b0;

        // 2) 发送 4 拍 0xAA 同步头（每拍 2-bit，MSB 优先）
        for (int s = 0; s < 4; s++) begin
            crc_bvalid = 1'b1;
            crc_kind   = 2'd0;
            crc_pair   = hdr[7 - 2*s -: 2];
            io_tick();
        end

        // 3) 发送 20 拍载荷（40 bit），在末拍（s==19）同沿拉高 crc_ready 零等待捕获尾字！
        for (int s = 0; s < 20; s++) begin
            crc_bvalid = 1'b1;
            crc_kind   = 2'd1;
            crc_pair   = pay[39 - 2*s -: 2];
            crc_ready  = (s == 19);
            #0.2;
            if (s == 19) begin
                if (!crc_valid || crc_err)
                    $fatal(1, "CRC case %s invalid/err on final beat", name);
                captured_crc = crc_out;
            end
            io_tick();
        end
        crc_bvalid = 1'b0;
        crc_ready  = 1'b0;

        if (captured_crc !== expected_wire)
            $fatal(1, "CRC case %s mismatch: got 0x%06X exp 0x%06X", name, captured_crc, expected_wire);
        if (!crc_svc_ready)
            $fatal(1, "CRC case %s did not return to ready after retire", name);
    endtask

    // =========================================================================
    // 2. 模块 2：ad4080_otp_macro_adapter + 生产 otp_shadow_loader 联调
    // =========================================================================
    logic        clk_aon, rst_aon_n, otp_pg;
    logic        otp_cancel, otp_quiescent;
    logic        otp_req_v, otp_req_r, otp_rsp_v, otp_rsp_r, otp_rsp_err;
    logic [1:0]  otp_req_a;
    logic [31:0] otp_rsp_d;
    logic        ver_req_v, ver_req_r, ver_rsp_v, ver_rsp_r, ver_pass;
    logic [127:0] ver_img, shadow_img;
    logic        otp_busy, shadow_valid, rom_crc_err;
    logic [3:0]  otp_err_code;
    logic        otp_prog_we, otp_err_inj;
    logic [1:0]  otp_prog_addr;
    logic [31:0] otp_prog_data;

    otp_shadow_loader #(
        .IMAGE_WORDS   (4),
        .WORD_WIDTH    (32),
        .TIMEOUT_CYCLES(16)
    ) u_otp_loader (
        .clk_i               (clk_aon),
        .rst_ni              (rst_aon_n),
        .power_good_i        (otp_pg),
        .adapters_cancel_o   (otp_cancel),
        .adapters_quiescent_i(otp_quiescent),
        .otp_req_valid_o     (otp_req_v),
        .otp_req_ready_i     (otp_req_r),
        .otp_req_addr_o      (otp_req_a),
        .otp_rsp_valid_i     (otp_rsp_v),
        .otp_rsp_ready_o     (otp_rsp_r),
        .otp_rsp_data_i      (otp_rsp_d),
        .otp_rsp_error_i     (otp_rsp_err),
        .verify_req_valid_o  (ver_req_v),
        .verify_req_ready_i  (ver_req_r),
        .verify_image_o      (ver_img),
        .verify_rsp_valid_i  (ver_rsp_v),
        .verify_rsp_ready_o  (ver_rsp_r),
        .verify_rsp_pass_i   (ver_pass),
        .busy_o              (otp_busy),
        .error_code_o        (otp_err_code),
        .shadow_valid_o      (shadow_valid),
        .shadow_image_o      (shadow_img)
    );

    ad4080_otp_macro_adapter #(
        .IMAGE_WORDS(4),
        .WORD_WIDTH (32),
        .CRC_POLY   (24'h864CFB),
        .CRC_INIT   (24'hB704CE)
    ) u_otp_adapter (
        .clk_aon_i               (clk_aon),
        .rst_ni                  (rst_aon_n),
        .otp_power_good_i        (otp_pg),
        .otp_adapters_cancel_i   (otp_cancel),
        .otp_adapters_quiescent_o(otp_quiescent),
        .otp_req_valid_i         (otp_req_v),
        .otp_req_ready_o         (otp_req_r),
        .otp_req_addr_i          (otp_req_a),
        .otp_rsp_valid_o         (otp_rsp_v),
        .otp_rsp_ready_i         (otp_rsp_r),
        .otp_rsp_data_o          (otp_rsp_d),
        .otp_rsp_error_o         (otp_rsp_err),
        .otp_verify_req_valid_i  (ver_req_v),
        .otp_verify_req_ready_o  (ver_req_r),
        .otp_verify_image_i      (ver_img),
        .otp_verify_rsp_valid_o  (ver_rsp_v),
        .otp_verify_rsp_ready_i  (ver_rsp_r),
        .otp_verify_rsp_pass_o   (ver_pass),
        .prog_we_i               (otp_prog_we),
        .prog_addr_i             (otp_prog_addr),
        .prog_data_i             (otp_prog_data),
        .macro_rd_err_inject_i   (otp_err_inj),
        .rom_crc_err_o           (rom_crc_err)
    );

    task automatic aon_tick();
        #5.0; clk_aon = 1'b1; #5.0; clk_aon = 1'b0;
    endtask

    // =========================================================================
    // 3. 模块 3：ad4080_result_store_foundry（4×4096×20 工艺厂宏封装）
    // =========================================================================
    logic        clk_mem, rst_mem_n;
    logic        mem_req, mem_wr, mem_rvalid, mem_slp, mem_sd;
    logic [13:0] mem_addr;
    logic [19:0] mem_din, mem_dout;

    ad4080_result_store_foundry u_sram_foundry (
        .clk_i       (clk_mem),
        .rst_ni      (rst_mem_n),
        .req_i       (mem_req),
        .write_i     (mem_wr),
        .addr_i      (mem_addr),
        .data_i      (mem_din),
        .data_o      (mem_dout),
        .read_valid_o(mem_rvalid),
        .slp_i       (mem_slp),
        .sd_i        (mem_sd),
        .rtsel_i     (2'b01),
        .wtsel_i     (2'b01)
    );

    task automatic mem_tick();
        #1.25; clk_mem = 1'b1; #1.25; clk_mem = 1'b0;
    endtask

    // =========================================================================
    // 4. 模块 4：ad4080_lvds_phy_wrapper（400 MHz DDR ODDR + DCO 相位封装）
    // =========================================================================
    logic       clk_phy, clk_phy_90, rst_phy_n;
    logic [1:0] dco_phase_sel;
    logic [2:0] vod_ctrl, vod_pad;
    logic       da_fall, da_rise, db_fall, db_rise;
    logic       da_oe, db_oe, dco_oe;
    logic       da_ddr, db_ddr, dco_ddr;
    logic       da_p, da_n, db_p, db_n, dco_p, dco_n;

    ad4080_lvds_phy_wrapper u_lvds_phy (
        .clk_io_i       (clk_phy),
        .clk_io_90_i    (clk_phy_90),
        .rst_ni         (rst_phy_n),
        .dco_phase_sel_i(dco_phase_sel),
        .lvds_vod_ctrl_i(vod_ctrl),
        .da_fall_i      (da_fall),
        .da_rise_i      (da_rise),
        .db_fall_i      (db_fall),
        .db_rise_i      (db_rise),
        .da_oe_i        (da_oe),
        .db_oe_i        (db_oe),
        .dco_oe_i       (dco_oe),
        .da_ddr_o       (da_ddr),
        .db_ddr_o       (db_ddr),
        .dco_ddr_o      (dco_ddr),
        .da_pad_p_o     (da_p),
        .da_pad_n_o     (da_n),
        .db_pad_p_o     (db_p),
        .db_pad_n_o     (db_n),
        .dco_pad_p_o    (dco_p),
        .dco_pad_n_o    (dco_n),
        .vod_boost_pad_o(vod_pad)
    );

    // =========================================================================
    // 5. 全部四项外围闭环模块自检流程
    // =========================================================================
    // [测试总纲]
    //   TEST 1 : 可编程 CRC-24 —— 未绑定/偶多项式阻断、epoch 翻转撤销、5 组 crc24.py 黄金对拍
    //   TEST 2 : 工艺厂 OTP 宏 —— 正常提交、1-bit 篡改触发 ROM_CRC_ERR、物理宏读错注入
    //   TEST 3 : 4×4096×20 SRAM —— 跨 4 银行边界读写、1 拍同步读延迟、轻睡钳位与保持
    //   TEST 4 : 400 MHz LVDS PHY —— ODDR 双沿合成、0°/90°/180° DCO 相位、self/单路 OE 关断
    initial begin
        // ---------------------------------------------------------------------
        // TEST 1: ad4080_crc24_service（5 组 crc24.py 黄金向量 + 负对照）
        // ---------------------------------------------------------------------
        clk_io = 1'b0; rst_io_n = 1'b0; crc_epoch = 1'b0;
        crc_bound = 1'b0; crc_poly = 24'd0; crc_init = 24'd0; crc_xorout = 24'd0;
        crc_inc_hdr = 1'b0; crc_refout = 1'b0; crc_worder = 2'd0;
        crc_start = 1'b0; crc_cancel = 1'b0; crc_ready = 1'b0;
        crc_bvalid = 1'b0; crc_bready = 1'b0; crc_pair = 2'd0; crc_kind = 2'd0;
        io_tick();
        rst_io_n = 1'b1;
        io_tick();

        // [负对照 1A] 未绑定配置或偶多项式（bit0=0）必须拉低 format_bound_o 且强启报错清零尾字
        crc_bound = 1'b0; crc_poly = 24'h864CFA;
        #0.2;
        if (crc_fmt_bound) $fatal(1, "Unbound/even poly must not report format_bound!");
        crc_start = 1'b1; io_tick(); crc_start = 1'b0;
        #0.2;
        if (!crc_err || crc_out != 24'd0) $fatal(1, "Invalid CRC start must assert crc_error_o and zero crc_o!");
        crc_cancel = 1'b1; io_tick(); crc_cancel = 1'b0;

        // [负对照 1B] 会话中途翻转 io_record_epoch_i 必须立即使 valid 失效并下一拍回归空闲
        crc_bound = 1'b1; crc_poly = 24'h864CFB; crc_init = 24'hB704CE;
        crc_start = 1'b1; io_tick(); crc_start = 1'b0;
        crc_epoch = 1'b1; #0.2;
        if (crc_valid || crc_out != 24'd0) $fatal(1, "Epoch flip did not mask stale CRC output!");
        io_tick();
        if (!crc_svc_ready) $fatal(1, "Epoch flip did not release CRC service to idle!");

        // [正向黄金对拍] 5 组经仓内 io/adi_crc/crc24.py 离线独立生成的精确期望尾字，
        // 完整覆盖 include_header=0/1、refout=0/1 与全部 4 种 wire_order 排线方式。
        check_crc_case("openpgp_hdr_msb",  24'h864CFB, 24'hB704CE, 24'h000000, 1'b1, 1'b0, 2'd0, 24'hCBA3F5);
        check_crc_case("flexray_pay_msb",  24'h5D6DCB, 24'hFEDCBA, 24'h000000, 1'b0, 1'b0, 2'd0, 24'h8CFACD);
        check_crc_case("synth_hdr_ref_br", 24'h6812AD, 24'hFFFFFF, 24'h5A5A5A, 1'b1, 1'b1, 2'd2, 24'hF286E0);
        check_crc_case("synth_pay_lsb",    24'h34C917, 24'h123456, 24'hABCDEF, 1'b0, 1'b0, 2'd1, 24'h077554);
        check_crc_case("synth_pay_lsbb",   24'h34C917, 24'h123456, 24'hABCDEF, 1'b0, 1'b1, 2'd3, 24'hDAD076);
        $display("CHECK_CRC24_SERVICE PASS cases=5 negative_controls=2");

        // ---------------------------------------------------------------------
        // TEST 2: ad4080_otp_macro_adapter + 真实 otp_shadow_loader 联调
        // ---------------------------------------------------------------------
        // [正向加载] 预烧录 3 字数据 + crc24.py 独立生成的合法 CRC-24 尾字（0xD537E3），
        // 确认 otp_shadow_loader 完成读-验-原子提交且 rom_crc_err_o=0。
        clk_aon = 1'b0; rst_aon_n = 1'b0; otp_pg = 1'b0;
        otp_prog_we = 1'b0; otp_prog_addr = 2'd0; otp_prog_data = 32'd0; otp_err_inj = 1'b0;
        aon_tick();
        otp_prog_we = 1'b1;
        otp_prog_addr = 2'd0; otp_prog_data = 32'hA55A1234; aon_tick();
        otp_prog_addr = 2'd1; otp_prog_data = 32'h55AA5678; aon_tick();
        otp_prog_addr = 2'd2; otp_prog_data = 32'h13579BDF; aon_tick();
        otp_prog_addr = 2'd3; otp_prog_data = 32'h00D537E3; aon_tick();
        otp_prog_we = 1'b0;

        rst_aon_n = 1'b1; otp_pg = 1'b1;
        for (int cyc = 0; cyc < 40; cyc++) begin
            aon_tick();
            if (!otp_busy) break;
        end
        if (!shadow_valid || rom_crc_err || otp_err_code != 4'd0)
            $fatal(1, "Valid OTP load failed: valid=%b crc_err=%b code=%0d", shadow_valid, rom_crc_err, otp_err_code);
        if (shadow_img[31:0] !== 32'hA55A1234 || shadow_img[95:64] !== 32'h13579BDF)
            $fatal(1, "Published OTP shadow image corrupted!");

        // [负对照 2A] 篡改 1 bit 镜像（翻转字 1 最低位）-> 拒绝发布、错误码 ERR_IMAGE_REJECTED(3)
        // 且硬件锁存真实 rom_crc_err_o=1 供手册 0x11 寄存器读取。
        rst_aon_n = 1'b0; otp_pg = 1'b0; aon_tick();
        otp_prog_we = 1'b1; otp_prog_addr = 2'd1; otp_prog_data = 32'h55AA5679; aon_tick();
        otp_prog_we = 1'b0;
        rst_aon_n = 1'b1; otp_pg = 1'b1;
        for (int cyc = 0; cyc < 40; cyc++) begin
            aon_tick();
            if (!otp_busy) break;
        end
        if (shadow_valid || !rom_crc_err || otp_err_code != 4'd3)
            $fatal(1, "Tampered OTP not rejected: valid=%b rom_crc_err=%b code=%0d",
                   shadow_valid, rom_crc_err, otp_err_code);

        // [负对照 2B] 注入工艺宏物理读错（macro_rd_err_inject_i=1）-> 立即中止并报 ERR_OTP_READ(2)
        rst_aon_n = 1'b0; otp_pg = 1'b0; otp_err_inj = 1'b1; aon_tick();
        rst_aon_n = 1'b1; otp_pg = 1'b1;
        for (int cyc = 0; cyc < 40; cyc++) begin
            aon_tick();
            if (!otp_busy) break;
        end
        if (shadow_valid || otp_err_code != 4'd2)
            $fatal(1, "Macro read error not trapped: valid=%b code=%0d", shadow_valid, otp_err_code);
        otp_err_inj = 1'b0;
        $display("CHECK_OTP_MACRO_ADAPTER PASS valid_commit=1 tamper_rom_crc_err=1 rd_err=1");

        // ---------------------------------------------------------------------
        // TEST 3: ad4080_result_store_foundry（四银行跨界 + 睡眠隔离与保持）
        // ---------------------------------------------------------------------
        // [银行边界覆盖] 依次在 Bank0(0x0FFF)、Bank1(0x1000)、Bank2(0x2FFF)、Bank3(0x3000)
        // 写入特征字并回读，随后进入轻睡模式验证误写屏蔽、输出钳 0 与唤醒后原值无损。
        clk_mem = 1'b0; rst_mem_n = 1'b0; mem_req = 1'b0; mem_wr = 1'b0;
        mem_addr = 14'd0; mem_din = 20'd0; mem_slp = 1'b0; mem_sd = 1'b0;
        mem_tick();
        rst_mem_n = 1'b1;
        mem_tick();

        mem_req = 1'b1; mem_wr = 1'b1;
        mem_addr = 14'h0FFF; mem_din = 20'h1A001; mem_tick();
        mem_addr = 14'h1000; mem_din = 20'h2B002; mem_tick();
        mem_addr = 14'h2FFF; mem_din = 20'h3C003; mem_tick();
        mem_addr = 14'h3000; mem_din = 20'h4D004; mem_tick();

        mem_wr = 1'b0;
        mem_addr = 14'h0FFF; mem_tick();
        if (!mem_rvalid || mem_dout !== 20'h1A001) $fatal(1, "Bank 0 read mismatch: %h", mem_dout);
        mem_addr = 14'h1000; mem_tick();
        if (!mem_rvalid || mem_dout !== 20'h2B002) $fatal(1, "Bank 1 read mismatch: %h", mem_dout);
        mem_addr = 14'h2FFF; mem_tick();
        if (!mem_rvalid || mem_dout !== 20'h3C003) $fatal(1, "Bank 2 read mismatch: %h", mem_dout);
        mem_addr = 14'h3000; mem_tick();
        if (!mem_rvalid || mem_dout !== 20'h4D004) $fatal(1, "Bank 3 read mismatch: %h", mem_dout);

        // 轻睡保持验证：睡眠期钳位输出 0 且阻断误写，唤醒后原数据完整保留
        mem_slp = 1'b1; mem_wr = 1'b1; mem_addr = 14'h3000; mem_din = 20'hFFFFF; mem_tick();
        if (mem_rvalid || mem_dout !== 20'd0) $fatal(1, "SRAM sleep isolation failed!");
        mem_slp = 1'b0; mem_wr = 1'b0; mem_addr = 14'h3000; mem_tick();
        if (!mem_rvalid || mem_dout !== 20'h4D004) $fatal(1, "SRAM retention lost across sleep!");
        $display("CHECK_SRAM_FOUNDRY_WRAP PASS banks=4 sleep_retention=1");

        // ---------------------------------------------------------------------
        // TEST 4: ad4080_lvds_phy_wrapper（ODDR 双沿 + 0°/90°/180° DCO + OE）
        // ---------------------------------------------------------------------
        // [时序与极性校验] 装载 (da: fall=1, rise=0; db: fall=0, rise=1)，
        // 依次验证 90° 中心对齐、0° 同相边沿、180° 反相边沿以及 self/单 lane OE 静默关断。
        clk_phy = 1'b0; clk_phy_90 = 1'b0; rst_phy_n = 1'b0;
        dco_phase_sel = 2'd0; vod_ctrl = 3'b101;
        da_fall = 1'b1; da_rise = 1'b0; db_fall = 1'b0; db_rise = 1'b1;
        da_oe = 1'b1; db_oe = 1'b1; dco_oe = 1'b1;
        // 复位释放后先给 1 拍建立使 dco_oe_neg_q 在低半周无毛刺开启
        #1.0; clk_phy = 1'b1; #1.0; clk_phy = 1'b0;
        rst_phy_n = 1'b1;
        #0.625; clk_phy = 1'b1; #0.625; clk_phy = 1'b0;

        // 4A) 90° 正交中心对齐：高半周发 rise，UI 正中 clk_phy_90 翻转产生采样沿，低半周发 fall
        dco_phase_sel = 2'd1;
        #0.625; clk_phy = 1'b1; clk_phy_90 = 1'b0; #0.2;
        if (da_ddr !== 1'b0 || db_ddr !== 1'b1 || dco_ddr !== 1'b0 || da_p !== 1'b0 || da_n !== 1'b1)
            $fatal(1, "LVDS PHY rise-half/90deg phase mismatch!");
        #0.425; clk_phy_90 = 1'b1; #0.2;
        if (dco_ddr !== 1'b1 || dco_p !== 1'b1 || dco_n !== 1'b0)
            $fatal(1, "LVDS PHY 90deg DCO rising edge missing!");
        #0.425; clk_phy = 1'b0; #0.2;
        if (da_ddr !== 1'b1 || db_ddr !== 1'b0 || vod_pad !== 3'b101)
            $fatal(1, "LVDS PHY fall-half mismatch!");

        // 4B) 0° 同相边沿对齐与 180° 反相边沿对齐极性核查（当前 clk_phy == 0）
        dco_phase_sel = 2'd0; #0.1;
        if (dco_ddr !== 1'b0 || dco_p !== 1'b0 || dco_n !== 1'b1)
            $fatal(1, "LVDS PHY 0deg phase mismatch!");
        dco_phase_sel = 2'd2; #0.1;
        if (dco_ddr !== 1'b1 || dco_p !== 1'b1 || dco_n !== 1'b0)
            $fatal(1, "LVDS PHY 180deg phase mismatch!");

        // 4C) self 模式关断 DCO OE 且单 lane 关断 DB OE（经过下降沿无毛刺关断后双端静默为 0）
        db_oe = 1'b0; dco_oe = 1'b0;
        #0.5; clk_phy = 1'b1; #0.625; clk_phy = 1'b0; #0.2;
        if (dco_ddr !== 1'b0 || dco_p !== 1'b0 || dco_n !== 1'b0 || db_p !== 1'b0 || db_n !== 1'b0)
            $fatal(1, "LVDS PHY self/single-lane OE shutdown failed!");
        $display("CHECK_LVDS_PHY_WRAPPER PASS oddr=1 dco_phases=0_90_180 oe_shutdown=1");

        $display("AD4080_SILICON_PERIPH_CLOSURE_PASS checks=4");
        $finish;
    end

endmodule
