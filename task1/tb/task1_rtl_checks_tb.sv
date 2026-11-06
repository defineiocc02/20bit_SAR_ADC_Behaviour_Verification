// SPDX-License-Identifier: BSD-3-Clause
//
// 模块一组件逐事件自检。向量由独立 Python 规格生成，比较完整输出和资格。
// 调度记录在上升沿后检查，DEM 记录在组合逻辑稳定后检查。
// 先把文件记录读入任务局部变量，再以显式赋值驱动 DUT 端口。

module task1_rtl_checks_tb;
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
        logic rst_n;
        logic clear;
        logic enable;
        logic advance;
        logic [17:0] eligible;
        logic shuffle;
        logic seed_load;
        logic [31:0] seed;
        scheduler_result_t expected;
    } scheduler_vector_t;

    typedef struct packed {
        logic [135:0] main_mask;
        logic [39:0] sub_mask;
        logic valid;
    } dem_result_t;

    typedef struct packed {
        logic [8:0] code;
        logic enable;
        logic [3:0] horizontal;
        logic [3:0] vertical;
        dem_result_t expected;
    } dem_vector_t;

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

    logic clk = 1'b0;
    top_vector_t top_vector;
    top_result_t top_actual;
    scheduler_vector_t scheduler_vector;
    scheduler_result_t scheduler_actual;
    dem_vector_t dem_vector;
    dem_result_t dem_actual;

    task1_slice_scheduler u_scheduler (
        .clk_i(clk),
        .rst_ni(scheduler_vector.rst_n),
        .clear_i(scheduler_vector.clear),
        .enable_i(scheduler_vector.enable),
        .advance_i(scheduler_vector.advance),
        .eligible_i(scheduler_vector.eligible),
        .shuffle_i(scheduler_vector.shuffle),
        .seed_load_i(scheduler_vector.seed_load),
        .seed_i(scheduler_vector.seed),
        .acq_ids_o(scheduler_actual.acq_ids),
        .conv_ids_o(scheduler_actual.conv_ids),
        .acq_mask_o(scheduler_actual.acq_mask),
        .conv_mask_o(scheduler_actual.conv_mask),
        .acq_valid_o(scheduler_actual.acq_valid),
        .conv_valid_o(scheduler_actual.conv_valid),
        .acq_sample_id_o(scheduler_actual.acq_id),
        .conv_sample_id_o(scheduler_actual.conv_id),
        .slot_accepted_o(scheduler_actual.accepted),
        .stall_o(scheduler_actual.stall),
        .seed_error_o(scheduler_actual.seed_error),
        .lfsr_state_o(scheduler_actual.lfsr_state)
    );

    fig11_dem u_dem (
        .code_i(dem_vector.code),
        .dem_enable_i(dem_vector.enable),
        .horizontal_phase_i(dem_vector.horizontal),
        .vertical_phase_i(dem_vector.vertical),
        .main_mask_o(dem_actual.main_mask),
        .sub_mask_o(dem_actual.sub_mask),
        .valid_o(dem_actual.valid)
    );

    module1_top u_module1 (
        .clk_i(clk),
        .rst_ni(top_vector.rst_n),
        .clear_i(top_vector.clear),
        .enable_i(top_vector.enable),
        .advance_i(top_vector.advance),
        .eligible_i(top_vector.eligible),
        .shuffle_i(top_vector.shuffle),
        .dem_enable_i(top_vector.dem_enable),
        .seed_load_i(top_vector.seed_load),
        .slice_seed_i(top_vector.slice_seed),
        .dem_seed_i(top_vector.dem_seed),
        .code_valid_i(top_vector.code_valid),
        .code_i(top_vector.code),
        .code_sample_id_i(top_vector.code_sample_id),
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
        .acq_h_phase_o(top_actual.acq_h_phase),
        .acq_v_phase_o(top_actual.acq_v_phase),
        .conv_h_phase_o(top_actual.conv_h_phase),
        .conv_v_phase_o(top_actual.conv_v_phase),
        .main_mask_o(top_actual.masks.main_mask),
        .sub_mask_o(top_actual.masks.sub_mask),
        .mask_valid_o(top_actual.masks.valid),
        .code_error_o(top_actual.code_error)
    );

    int top_cycles;
    int top_accepts;
    int top_conversions;
    int top_stalls;
    int top_seed_errors;
    int top_active_prefixes;
    int top_bypass_prefixes;
    int top_code_errors;

    int scheduler_cycles;
    int scheduler_accepts;
    int scheduler_conversions;
    int scheduler_stalls;
    int scheduler_seed_errors;
    int dem_active;
    int dem_bypass;
    int dem_invalid;

    task automatic check_scheduler(input string path);
        scheduler_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open scheduler vectors: %s", path);
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            scheduler_vector = record;
            #1;
            clk = 1'b1;
            #1;
            if (scheduler_actual !== scheduler_vector.expected) begin
                $fatal(1, "scheduler cycle=%0d rst_n=%b clear=%b enable=%b advance=%b eligible=%h shuffle=%b seed_load=%b seed=%h expected=%h got=%h",
                       scheduler_cycles, scheduler_vector.rst_n, scheduler_vector.clear,
                       scheduler_vector.enable, scheduler_vector.advance,
                       scheduler_vector.eligible, scheduler_vector.shuffle,
                       scheduler_vector.seed_load, scheduler_vector.seed,
                       scheduler_vector.expected, scheduler_actual);
            end
            scheduler_cycles++;
            scheduler_accepts += int'(scheduler_actual.accepted);
            scheduler_conversions += int'(scheduler_actual.accepted && scheduler_actual.conv_valid);
            scheduler_stalls += int'(scheduler_actual.stall);
            scheduler_seed_errors += int'(scheduler_actual.seed_error);
            clk = 1'b0;
            scanned = $fscanf(file_id, "%h", record);
        end
        if (!$feof(file_id)) $fatal(1, "malformed scheduler vector file");
        $fclose(file_id);
    endtask

    task automatic check_dem(input string path);
        dem_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open DEM vectors: %s", path);
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            dem_vector = record;
            #1;
            if (dem_actual !== dem_vector.expected) begin
                $fatal(1, "DEM code=%0d enable=%b h=%0d v=%0d expected=%h got=%h",
                       dem_vector.code, dem_vector.enable, dem_vector.horizontal,
                       dem_vector.vertical, dem_vector.expected, dem_actual);
            end
            if (!dem_actual.valid) dem_invalid++;
            else if (dem_vector.enable) dem_active++;
            else dem_bypass++;
            scanned = $fscanf(file_id, "%h", record);
        end
        if (!$feof(file_id)) $fatal(1, "malformed DEM vector file");
        $fclose(file_id);
    endtask

    task automatic check_top(input string path);
        top_vector_t record;
        int file_id;
        int scanned;
        file_id = $fopen(path, "r");
        if (file_id == 0) $fatal(1, "cannot open top vectors: %s", path);
        scanned = $fscanf(file_id, "%h", record);
        while (scanned == 1) begin
            top_vector = record;
            #1;
            if (!top_vector.rst_n || top_vector.clear || !top_vector.enable) begin
                if (top_actual.masks !== '0 || top_actual.code_error) begin
                    $fatal(1, "top cycle=%0d failed inactive combinational guard", top_cycles);
                end
            end
            clk = 1'b1;
            #1;
            if (top_actual !== top_vector.expected) begin
                $fatal(1, "top cycle=%0d code=%0d code_id=%0d expected=%h got=%h",
                       top_cycles, top_vector.code, top_vector.code_sample_id,
                       top_vector.expected, top_actual);
            end
            top_cycles++;
            top_accepts += int'(top_actual.schedule.accepted);
            top_conversions += int'(top_actual.schedule.accepted && top_actual.schedule.conv_valid);
            top_stalls += int'(top_actual.schedule.stall);
            top_seed_errors += int'(top_actual.schedule.seed_error);
            top_active_prefixes += int'(top_actual.masks.valid && top_actual.conv_dem_enable);
            top_bypass_prefixes += int'(top_actual.masks.valid && !top_actual.conv_dem_enable);
            top_code_errors += int'(top_actual.code_error);
            clk = 1'b0;
            scanned = $fscanf(file_id, "%h", record);
        end
        if (!$feof(file_id)) $fatal(1, "malformed top vector file");
        $fclose(file_id);
    endtask

    initial begin : run_checks
        string scheduler_path;
        string dem_path;
        string top_path;
        if (!$value$plusargs("scheduler_vectors=%s", scheduler_path) ||
            !$value$plusargs("dem_vectors=%s", dem_path) ||
            !$value$plusargs("top_vectors=%s", top_path)) begin
            $fatal(1, "scheduler_vectors, dem_vectors, and top_vectors plusargs are required");
        end
        check_scheduler(scheduler_path);
        check_dem(dem_path);
        check_top(top_path);
        $display("TASK1_RTL_CHECKS_COMPLETE scheduler_cycles=%0d scheduler_accepts=%0d scheduler_conversions=%0d scheduler_stalls=%0d scheduler_seed_errors=%0d dem_active=%0d dem_bypass=%0d dem_invalid=%0d top_cycles=%0d top_accepts=%0d top_conversions=%0d top_stalls=%0d top_seed_errors=%0d top_active_prefixes=%0d top_bypass_prefixes=%0d top_code_errors=%0d",
                 scheduler_cycles, scheduler_accepts, scheduler_conversions, scheduler_stalls,
                 scheduler_seed_errors, dem_active, dem_bypass, dem_invalid,
                 top_cycles, top_accepts, top_conversions, top_stalls, top_seed_errors,
                 top_active_prefixes, top_bypass_prefixes, top_code_errors);
        $finish;
    end
endmodule
