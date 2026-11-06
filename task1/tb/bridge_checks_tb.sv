// SPDX-License-Identifier: BSD-3-Clause
//
// 检查桥接叶的全域映射，以及候选顶层的样本归属与前缀补偿。
// 文件记录先读入局部变量，再显式驱动 DUT。所有比较都在输出稳定后进行。

module task1_bridge_checks_tb;
    typedef struct packed {
        logic [39:0] acq_ids;
        logic [39:0] conv_ids;
        logic [17:0] acq_mask;
        logic [17:0] conv_mask;
        logic acq_valid;
        logic conv_valid;
        logic [31:0] acq_id;
        logic [31:0] conv_id;
        logic accepted;
        logic stall;
        logic seed_error;
        logic [31:0] lfsr_state;
    } scheduler_result_t;

    typedef struct packed {
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic valid;
    } dem_result_t;

    typedef struct packed {
        scheduler_result_t schedule;
        logic [31:0] dem_rng_state;
        logic acq_dem_enable;
        logic conv_dem_enable;
        logic [3:0] acq_h_phase;
        logic [3:0] acq_v_phase;
        logic [3:0] conv_h_phase;
        logic [3:0] conv_v_phase;
        dem_result_t masks;
        logic code_error;
    } top_result_t;

    typedef struct packed {
        logic rst_n;
        logic clear;
        logic enable;
        logic advance;
        logic [17:0] eligible;
        logic shuffle;
        logic dem_enable;
        logic seed_load;
        logic [31:0] slice_seed;
        logic [31:0] dem_seed;
        logic code_valid;
        logic [8:0] code;
        logic [31:0] code_sample_id;
        top_result_t expected;
    } top_vector_t;

    typedef struct packed {
        top_vector_t base;
        logic enable_bridge;
        logic expected_acq_bridge;
        logic expected_conv_bridge;
        logic [31:0] expected_correction;
    } bridge_vector_t;

    typedef struct packed {
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic [31:0] correction;
    } leaf_result_t;

    typedef struct packed {
        logic [8:0] code;
        logic [3:0] reserved_column;
        logic [2:0] vertical_slot;
        leaf_result_t expected;
    } leaf_vector_t;

    logic clk = 1'b0;
    bridge_vector_t top_vector;
    top_result_t top_actual;
    logic acq_bridge, conv_bridge;
    logic [31:0] correction_q32;
    leaf_vector_t leaf_vector;
    leaf_result_t leaf_actual;

    fig21_bridge u_leaf (
        .code_i(leaf_vector.code),
        .reserved_column_i(leaf_vector.reserved_column),
        .vertical_slot_i(leaf_vector.vertical_slot),
        .main_mask_o(leaf_actual.main_mask),
        .sub_mask_o(leaf_actual.sub_mask),
        .correction_q32_o(leaf_actual.correction)
    );

    module1_bridge_top u_module1 (
        .clk_i(clk),
        .rst_ni(top_vector.base.rst_n),
        .clear_i(top_vector.base.clear),
        .enable_i(top_vector.base.enable),
        .advance_i(top_vector.base.advance),
        .eligible_i(top_vector.base.eligible),
        .shuffle_i(top_vector.base.shuffle),
        .dem_enable_i(top_vector.base.dem_enable),
        .bridge_enable_i(top_vector.enable_bridge),
        .seed_load_i(top_vector.base.seed_load),
        .slice_seed_i(top_vector.base.slice_seed),
        .dem_seed_i(top_vector.base.dem_seed),
        .code_valid_i(top_vector.base.code_valid),
        .code_i(top_vector.base.code),
        .code_sample_id_i(top_vector.base.code_sample_id),
        .acq_ids_o(top_actual.schedule.acq_ids),
        .conv_ids_o(top_actual.schedule.conv_ids),
        .acq_mask_o(top_actual.schedule.acq_mask),
        .conv_mask_o(top_actual.schedule.conv_mask),
        .acq_valid_o(top_actual.schedule.acq_valid),
        .conv_valid_o(top_actual.schedule.conv_valid),
        .acq_sample_id_o(top_actual.schedule.acq_id),
        .conv_sample_id_o(top_actual.schedule.conv_id),
        .slot_accepted_o(top_actual.schedule.accepted),
        .stall_o(top_actual.schedule.stall),
        .seed_error_o(top_actual.schedule.seed_error),
        .slice_lfsr_state_o(top_actual.schedule.lfsr_state),
        .dem_rng_state_o(top_actual.dem_rng_state),
        .acq_dem_enable_o(top_actual.acq_dem_enable),
        .conv_dem_enable_o(top_actual.conv_dem_enable),
        .acq_bridge_enable_o(acq_bridge),
        .conv_bridge_enable_o(conv_bridge),
        .acq_h_phase_o(top_actual.acq_h_phase),
        .acq_v_phase_o(top_actual.acq_v_phase),
        .conv_h_phase_o(top_actual.conv_h_phase),
        .conv_v_phase_o(top_actual.conv_v_phase),
        .main_mask_o(top_actual.masks.main_mask),
        .sub_mask_o(top_actual.masks.sub_mask),
        .correction_q32_o(correction_q32),
        .mask_valid_o(top_actual.masks.valid),
        .code_error_o(top_actual.code_error)
    );

    int leaf_cases;
    int leaf_nonzero_corrections;
    int top_cycles;
    int top_accepts;
    int top_stalls;
    int top_seed_errors;
    int top_code_errors;
    int top_bridge_prefixes;
    int top_fig11_dem_prefixes;
    int top_fig11_bypass_prefixes;
    int top_nonzero_corrections;

    task automatic check_leaf(input string path);
        leaf_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open bridge leaf vectors: %s", path);
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            leaf_vector = record;
            #1;
            if (leaf_actual !== leaf_vector.expected) begin
                $fatal(1, "bridge leaf code=%0d R=%0d v=%0d expected=%h got=%h",
                       leaf_vector.code, leaf_vector.reserved_column, leaf_vector.vertical_slot,
                       leaf_vector.expected, leaf_actual);
            end
            leaf_cases++;
            leaf_nonzero_corrections += int'(leaf_actual.correction != '0);
            scanned = $fscanf(file_id, "%h", record);
        end
        if (!$feof(file_id)) $fatal(1, "malformed bridge leaf vector file");
        $fclose(file_id);
    endtask

    task automatic check_top(input string path);
        bridge_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open bridge top vectors: %s", path);
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            top_vector = record;
            #1;
            if (!top_vector.base.rst_n || top_vector.base.clear || !top_vector.base.enable) begin
                if (top_actual.masks !== '0 || top_actual.code_error || correction_q32 !== '0) begin
                    $fatal(1, "bridge top cycle=%0d failed inactive guard", top_cycles);
                end
            end
            clk = 1'b1;
            #1;
            if ({top_actual, acq_bridge, conv_bridge, correction_q32} !==
                {top_vector.base.expected, top_vector.expected_acq_bridge,
                 top_vector.expected_conv_bridge, top_vector.expected_correction}) begin
                $fatal(1, "bridge top cycle=%0d code=%0d code_id=%0d expected=%h/%b/%b/%h got=%h/%b/%b/%h",
                       top_cycles, top_vector.base.code, top_vector.base.code_sample_id,
                       top_vector.base.expected, top_vector.expected_acq_bridge,
                       top_vector.expected_conv_bridge, top_vector.expected_correction,
                       top_actual, acq_bridge, conv_bridge, correction_q32);
            end
            top_cycles++;
            top_accepts += int'(top_actual.schedule.accepted);
            top_stalls += int'(top_actual.schedule.stall);
            top_seed_errors += int'(top_actual.schedule.seed_error);
            top_code_errors += int'(top_actual.code_error);
            top_bridge_prefixes += int'(top_actual.masks.valid && conv_bridge);
            top_fig11_dem_prefixes += int'(top_actual.masks.valid && !conv_bridge
                                           && top_actual.conv_dem_enable);
            top_fig11_bypass_prefixes += int'(top_actual.masks.valid && !conv_bridge
                                              && !top_actual.conv_dem_enable);
            top_nonzero_corrections += int'(correction_q32 != '0);
            clk = 1'b0;
            scanned = $fscanf(file_id, "%h", record);
        end
        if (!$feof(file_id)) $fatal(1, "malformed bridge top vector file");
        $fclose(file_id);
    endtask

    initial begin : run_checks
        string leaf_path;
        string top_path;
        if (!$value$plusargs("leaf_vectors=%s", leaf_path) ||
            !$value$plusargs("top_vectors=%s", top_path)) begin
            $fatal(1, "leaf_vectors and top_vectors plusargs are required");
        end
        check_leaf(leaf_path);
        check_top(top_path);
        $display("TASK1_BRIDGE_CHECKS_COMPLETE leaf_cases=%0d leaf_nonzero_corrections=%0d top_cycles=%0d top_accepts=%0d top_stalls=%0d top_seed_errors=%0d top_code_errors=%0d top_bridge_prefixes=%0d top_fig11_dem_prefixes=%0d top_fig11_bypass_prefixes=%0d top_nonzero_corrections=%0d",
                 leaf_cases, leaf_nonzero_corrections, top_cycles, top_accepts, top_stalls,
                 top_seed_errors, top_code_errors, top_bridge_prefixes, top_fig11_dem_prefixes,
                 top_fig11_bypass_prefixes, top_nonzero_corrections);
        $finish;
    end
endmodule
