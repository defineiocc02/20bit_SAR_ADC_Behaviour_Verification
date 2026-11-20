// =============================================================================
// 文件名     : sample_cadence.cpp
// 模块名     : 数字 IP 连续采样端口测试
// 功能       : 从公开端口逐沿驱动 adc_digital_ip_v21，检查连续采样与出码。
// 设计说明   : 每笔经过真实片选、SAR 事件、掩码、权重装表、ADC2 归属与重构。
//              时间场景使用给定的比较决定和 ADC2 原码；扰动闭环场景从公开
//              物理开关驱动理想量化器和余差。数字时钟无门延迟。
// 测量窗口   : 配置生效后开始，最后一个样本返回后排空。时刻从首个公共采样沿起算。
// 依据       : 生产顶层导出的 prepare、apply、capture、raw 和 20 位退休接口。
//              论文约 7 ns 对应前级转换。这里可直接测量的是最后一次数字写门的边沿。
//              两者相比较时仍需考虑译码的物理延迟、电平转换和开关建立时间。
// =============================================================================
#include "Vadc_digital_ip_v21.h"
#include "verilated.h"
#include <algorithm>
#include <array>
#include <bit>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

using Word = std::int64_t;
constexpr int samples = 192;
constexpr Word q30 = Word{1} << 30;

// 码、标签或顺序错误属于测试失败；只有场景表指明的错过孔径可以作为负对照通过。
void require(bool ok, const std::string &message) {
    if (!ok)
        throw std::runtime_error(message);
}

struct Event {
    int index, bit, decision, k;
    bool write;
};
// 模式 0 是普通转换，1 是拒绝门限后继续六位 SAR，2 是接受门限后再判三位。
struct Sample {
    int tag, mode, flash, word, upper, h, v, half, coin, raw, final_k, code, sat;
    std::vector<Event> events;
    int input_code = -1;                          // 受控模型的真实输入，单位为 20 位输出码
    int input_quarters = 0;  // 正向激励中额外的 1/4 LSB20，独立于 DUT 原码
    int sampled_pre8 = 0;                         // 八个物理槽实际采样预置之和，单位为 1/8 粗码
    unsigned sadc_mask = 0;                       // 在公共采样沿读取的实际 bank 输出
    int lower8 = 0;                               // 从所属 SADC 的七根物理控制线折算
    int mismatch_pre = 0, mismatch_direction = 0; // 指定物理子单元的 pre 和 post−pre
    // 交给 Python 复核的实测量。只从生产引脚登记，最终按退休标签写出。
    std::uint64_t observed_ids = 0;
    unsigned observed_pre = 0, observed_qid = 0;
    std::array<unsigned, 8> observed_post{};
    std::array<int, 8> observed_events{-1, -1, -1, -1, -1, -1, -1, -1};
    unsigned observed_events_count = 0;
    bool forced = true;                           // 原有事件回归显式给字，生产字源单独对拍
    bool from_switches = false;                   // 是否从实际端口推导比较器与 ADC2 返回
    bool range = false;
};

// 内部字、全字扫描、混合与断开低段各有可观察的独立判据。
// 两个权重用例施加同一物理偏差，唯一改变的是装入数字核的系数。
enum class WordTest { Events, Internal, Sweep, Mixed, Disconnected, NominalTable, CorrectedTable };

// 测试端用无符号 32 位运算重放受理序号。RTL 的独立寄存器不能靠这个参考驱动。
unsigned next_random(unsigned state) {
    static_assert(sizeof(state) == 4);
    state ^= state << 13;
    state ^= state >> 17;
    return state ^ (state << 5);
}

int sadc_eighths(unsigned bits) {
    // 权重的八分之一刻度直接来自 B1 的电容表，独立于生产译码的接线表达式。
    constexpr std::array<int, 7> weight = {1, 1, 2, 4, 8, 16, 32}; // bit0...6
    int total = 0;
    for (int j = 0; j < 7; ++j)
        total += ((bits >> j) & 1) * weight[j];
    return total;
}

// 读物理端口中的紧邻字段，跨越 32 位容器时接上高字。宽度只用 4 或 20。
template <class Wide> unsigned lane(const Wide &v, int index, unsigned width) {
    unsigned bit = index * width, n = bit / 32, shift = bit % 32;
    std::uint64_t value = v[n];
    if (shift + width > 32)
        value |= std::uint64_t(v[n + 1]) << 32;
    return unsigned((value >> shift) & ((1u << width) - 1));
}

// 扫描实际写使能覆盖的物理行；只看槽编号会漏掉同时错选物理片的情况。
// 数组读取发生于采样沿之前，随后这一笔的 ADC2 返回不再读取新的采集组。
int physical_pre8(const Vadc_digital_ip_v21 &d) {
    int sum = 0;
    for (int sid = 0; sid < 18; ++sid) {
        if ((d.pre_wr_o >> sid) & 1) {
            unsigned nib = lane(d.rdac_pre_state_o, sid, 4);
            sum += (nib >> 1) + (nib & 1); // 高三位表示 4/2/1，平衡开关再加 1
        }
    }
    return sum;
}

// 最后应用的物理开关决定残差。主半单元为 32 个八分之一单位，
// 子支路再按 4/2/1/1 计权，桥交换后的平衡位同样参与这次求和。
int physical_post8(const Vadc_digital_ip_v21 &d) {
    int sum = 0;
    for (int slot = 0; slot < 8; ++slot) {
        int sid = (d.node_ids_o >> (5 * slot)) & 31;
        require(sid < 18, "physical slice ID out of bounds");
        unsigned mask = lane(d.rdac_post_state_o, sid, 20);
        sum += 32 * std::popcount(mask & 65535u);
        sum +=
            4 * ((mask >> 16) & 1) + 2 * ((mask >> 17) & 1) + ((mask >> 18) & 1) + ((mask >> 19) & 1);
    }
    return sum;
}

// 这里是外部理想余差宏。原码从 DUT 输出的物理开关得到；目标出码来自输入本身。
// 这样错接一条采样支路就会引起错码或越窗，不能靠正反解同一个期望码通过。
void capture_ideal_residue(const Vadc_digital_ip_v21 &d, Sample &a, WordTest test) {
    const int post8 = physical_post8(d);
    require(post8 == 8 * a.final_k, "final physical switch weight mismatch");
    int unbounded_raw = 2048 + 2 * a.input_code + a.input_quarters / 2 - 512 * post8 + 512 * a.sampled_pre8;
    if (test == WordTest::NominalTable || test == WordTest::CorrectedTable) {
        // 给片 0 子单元 18 设置 2^-10 的物理权重差。只有这片在本笔内并且
        // 选中时才影响残差；在 G=32 下，一份贡献相当于 64 个 ADC2 原码。
        int post_bit = (d.conv_sel_o & 1) ? ((lane(d.rdac_post_state_o, 0, 20) >> 18) & 1) : 0;
        a.mismatch_direction = post_bit - a.mismatch_pre;
        unbounded_raw -= 64 * a.mismatch_direction;
        if (test == WordTest::NominalTable)
            a.code = a.input_code - 32 * a.mismatch_direction;
    }
    a.range = unbounded_raw < 0 || unbounded_raw > 8191;
    a.raw = a.range ? 0 : unbounded_raw;
    if (a.range) {
        // 去掉宏的低段连线时，越窗细码按节点宏合同回零；出码与范围标志分别核对。
        a.code = std::clamp((post8 - a.sampled_pre8) * 256 - 1024, 0, 1048575);
    }
}

// 1. 前级只从输入采样和实际 SADC 预置形成粗码；构造后面的已决事件时，
// 不得使用 DUT 的 RDAC 末码反推比较器结果。最高区间的门限拒绝额外占一沿。
void set_decisions(Sample &a, int coarse) {
    a.flash = std::min(coarse >> 6, 7);
    a.mode = coarse < 448 ? 0 : (coarse < 512 ? 1 : 2);
    const int low = coarse & (a.mode == 2 ? 7 : 63);
    a.events.clear();
    // 普通区的描述符保留 Flash 三位前缀，再逐位接收决定。
    // 事件 1 只提交 Flash。在保护区，事件 2 的探测可以消耗一拍而不写开关。
    int prefix = a.flash << 6;
    a.events.push_back({1, 0, 0, prefix + a.upper, true});
    if (a.mode != 0) {
        const bool accepted = a.mode == 2;
        if (accepted)
            prefix = 512;
        a.events.push_back({2, 9, int(accepted), prefix + a.upper, accepted});
    }
    for (int j = a.mode == 2 ? 2 : 5; j >= 0; --j) {
        int decision = (low >> j) & 1;
        prefix |= decision << j;
        int index = (a.mode == 2 ? 5 : 7) - j;
        a.events.push_back({index, j, decision, prefix + a.upper, true});
    }
    a.final_k = prefix + a.upper;
}

// 三条路径循环出现，包含门限拒绝后重用事件序号 2 的情况。
// 每次改变 raw 与扰动字，使串标签或拿错开关记录能表现在接口码上。
Sample stimulus(int tag) {
    Sample a{};
    a.tag = tag;
    a.mode = (tag - 1) % 3;
    a.flash = a.mode == 0 ? (1 + (tag / 3) % 6) : 7;
    a.word = (13 * tag + 7) % 64;
    // 对靠近上边界的保护码选用有回旋余量的扰动和细码。
    // 此项专查连续吞吐：让每笔结果保持在正常码域，饱和平台容易掩盖错配。
    if (a.mode == 2)
        a.word |= 16;
    a.upper = 4 * (a.word >> 5) + ((a.word >> 3) & 1);
    a.h = (3 * tag) % 8;
    a.v = (tag + tag / 8) % 8;
    a.half = (tag >> 1) & 1;
    a.coin = (tag >> 2) & 1;
    a.raw = a.mode == 0 ? (149 * tag + 1024) % 8192 : 1024 + (149 * tag) % 3072;
    const int low = a.mode == 2 ? (tag / 3) % 2 : (23 * tag + 7) % 64;
    set_decisions(a, (a.mode == 2 ? 512 : a.flash * 64) + low);

    // 同一套名义权重下，post 权重为 k/16，pre 权重为 T/16，G=32。
    // 独立计算参考码，不使用 DUT 内部权重和或 DUT 退休结果作为期望。
    // t8 用八分之一的单位保存采样预置，包含被数字反扣的扰动分量。
    // 此处先用激励本身构造正确码，测试从不从观测到的错误码反推“真值”。
    const int t8 = 8 * (a.upper + 2 * ((a.word >> 4) & 1)) + (a.word & 7);
    const Word y = -3145728 + 1536 * a.raw + 768;
    const Word n = 64 * y + 6 * (a.final_k * (q30 / 16)) - 3 * (32 * q30) - 6 * (t8 * (q30 / 128));
    // I=2^25 时重构器的有符号右移等价于除以 2048 向负无穷取整。
    // C++ 的有符号除法向零取整，负值必须显式修正，再执行恰半离零的末级舍入。
    const Word x = n >= 0 ? n / 2048 : -((-n + 2047) / 2048);
    const Word delta = ((std::abs(x) + 48) / 96) * (x < 0 ? -1 : 1);
    const Word unbounded = 524288 + delta;
    a.code = int(std::clamp<Word>(unbounded, 0, 1048575));
    a.sat = int(a.code != unbounded);
    require(!a.sat, "cadence stimulus should be in the unclipped output range");
    return a;
}

// 准备端口只写新采集描述符；旧转换的 qid / 应用事件来自其自身的冻结描述符。
void descriptor(Vadc_digital_ip_v21 &d, const Sample &a) {
    // 保持同一个待受理描述符直到 ready。采样字、相位和标签同时被接受。
    d.prepare_tag = a.tag;
    d.dither_force_i = a.forced;
    d.prepare_word = a.forced ? a.word : 63 - a.word;
    d.prepare_U = a.forced ? a.upper : 7; // 生产必须忽略测试数据，包括冲突的 U
    d.prepare_flash = a.from_switches ? 0 : a.flash;
    d.prepare_h = a.h;
    d.prepare_v = a.v;
    d.prepare_half = a.half;
    d.prepare_coin = a.coin;
    d.prepare_qid = (a.tag - 1) & 1;
}

// 用整数飞秒驱动，避免 625 ps 或 1.5625 ns 时钟在长序列中出现累计取整误差。
struct Clock {
    VerilatedContext &context;
    Vadc_digital_ip_v21 &d;
    Word fs;
    void check_slice_owners() const {
        if (!d.rst_n)
            return;
        const unsigned acq = d.acq_sel_o, conv = d.conv_sel_o;
        require(!(acq & conv) && d.idle_sel_o == (~(acq | conv) & 0x3ffffu),
                "physical slice owners overlap or spare mask differs");
        require(!(d.pre_wr_o & ~acq), "preset write outside acquisition group");
    }
    void rise() {
        // 同一组不变量在上升沿两侧各观察一次。只在采样退休时检查会漏掉
        // release、promote、prepare 同沿前的半周期片选冲突。
        d.eval();
        check_slice_owners();
        context.timeInc(fs / 2);
        d.clk = 1;
        d.eval();
        check_slice_owners();
    }
    void fall() {
        context.timeInc(fs - fs / 2);
        d.clk = 0;
        d.eval();
    }
    void cycle() {
        rise();
        fall();
    }
};

// 每场从复位和完整 360 字装表开始；所有配置周期都在吞吐测量窗外。
// 使用生产调度器，不灌预排片号，也不 force 内部状态。
void configure(Clock &c, bool load_measured_weight, bool program_pool_seed) {
    auto &d = c.d;
    d.clk = 0;
    d.rst_n = 0;
    d.sched_ext_sel = 0;
    // 默认入口使用片池复位种子；其他受控场景仍覆盖真实的重播种端口。
    d.sched_seed_we = program_pool_seed;
    d.sched_seed = 0x12345678;
    d.prepare_valid = 0;
    d.dither_force_i = 0;
    d.prepare_word = 0;
    d.prepare_U = 0;
    d.stop_i = 0;
    d.sample_event = 0;
    d.input_captured = 0;
    d.residue_captured = 0;
    d.hold_ack = 0;
    d.ev_bit_valid_i = 0;
    d.ev_start_i = 0;
    d.ev_flash_valid_i = 0;
    d.ev_flash_code_i = 0;
    d.adc2_valid_i = 0;
    d.adc2_raw_i = 0;
    d.adc2_range_err_i = 0;
    d.cfg_epoch = 0;
    d.b_q24_cfg = 0;
    d.w_cfg_we = 0;
    d.w_cfg_commit = 0;
    d.w_cfg_reload = 0;
    c.cycle();
    c.cycle();
    d.rst_n = 1;
    c.cycle();
    d.sched_seed_we = 0;
    const std::array<Word, 4> sub = {q30 / 32, q30 / 64, q30 / 128, q30 / 128};
    // 已配置的 18 片共用名义权重，但具体选哪八片由真实 RTL 自己决定。
    // 先写各片所有主、子权重，最后一次性提交，第一笔请求不与 commit 抢沿。
    d.w_cfg_we = 1;
    for (int sid = 0; sid < 18; ++sid) {
        for (int unit = 0; unit < 20; ++unit) {
            bool is_sub = unit >= 16;
            d.w_cfg_sid = sid;
            d.w_cfg_sel = is_sub;
            d.w_cfg_idx = is_sub ? unit - 16 : unit;
            d.w_cfg_data = is_sub ? sub[unit - 16] : q30 / 4;
            if (load_measured_weight && sid == 0 && unit == 18)
                d.w_cfg_data += Word{1} << 20;
            c.cycle();
        }
    }
    d.w_cfg_we = 0;
    d.w_cfg_commit = 1;
    c.cycle();
    d.w_cfg_commit = 0;
    d.eval();
    require(d.w_cfg_valid && !d.w_cfg_range_err, "nominal table did not commit");
}

struct Scenario {
    const char *name;
    Word clock_fs;
    int frame_clocks;
    int withhold_raw_at_frame;
    const char *expected_miss;
    int expected_tag;
    WordTest words = WordTest::Events;
};
// 标签把跨多个公共周期的五个动作串起来。尚未发生的动作保留负一，
// 即使恰好漏掉第一个样本，默认值也不能碰巧等于首个有效时刻。
struct Times {
    int sample = -1, start = -1, post = -1, capture = -1, raw = -1, out = -1;
};
// 全部计数来自实际受理和退休的边沿。负对照以首次失约结束，
// 因此其中的部分输出数只说明停止位置，不能换算为一个有效吞吐率。
struct Counts {
    int prepared = 0, sampled = 0, captured = 0, returned = 0, retired = 0, writes = 0;
    int max_post_clocks = 0, min_output_gap = 0, max_output_gap = 0, missed_tag = 0;
    int range_flags = 0;
    std::string miss;
};

// 只允许受理沿更新一个 bank。检查没有被选中的七位，能捕获误写另一个量化器的情况。
unsigned check_sadc_write(const Vadc_digital_ip_v21 &d, const Sample *accepted, unsigned old) {
    unsigned write = 0, wanted = old;
    if (accepted) {
        unsigned q = (accepted->tag - 1) & 1, shift = 7 * q;
        unsigned w = accepted->word, mask = ((w & 16) << 1) | ((w & 7) << 1);
        write = 1u << q;
        wanted = (old & ~(127u << shift)) | (mask << shift);
    }
    require(d.sadc_pre_wr_o == write && d.sadc_pre_state_o == wanted, "SADC mask, write bank or hold");
    return wanted;
}

// 放在第一笔正常请求之前。若拒绝偷偷推进了字源，首笔公开已知字立即失配。
// 写门和持有的 bank 同时检查，防止只屏蔽顶层 ready 而内部已经写入。
void reject_probes(Clock &c) {
    auto &d = c.d;
    d.dither_force_i = 1;
    d.prepare_qid = 0;
    d.prepare_word = 53;
    d.prepare_U = 5;
    d.prepare_valid = 0;
    d.eval();
    require(!d.err_o, "idle U incorrectly flagged");
    d.prepare_valid = 1;
    d.eval();
    require(!d.prepare_ready && d.err_o, "inconsistent forced word was accepted");
    c.cycle();
    check_sadc_write(d, nullptr, 0);
    d.prepare_U = 4;
    d.prepare_qid = 1;
    d.eval();
    require(!d.prepare_ready && d.err_o, "wrong first quantizer accepted");
    c.cycle();
    check_sadc_write(d, nullptr, 0);
    // 即使字与 qid 正确，停收也不能消耗随机数或改变 SADC 引脚。
    d.prepare_qid = 0;
    d.stop_i = 1;
    d.eval();
    require(!d.prepare_ready && !d.err_o, "stop accepted a sample or invented an error");
    c.cycle();
    check_sadc_write(d, nullptr, 0);
    d.stop_i = 0;
    d.prepare_valid = 0;
}

// 文件为每笔输出一行。所有带时间的列记录真实端口观察所在的时钟沿，
// 首个公共采样沿为零。依据 clock_fs 换算秒；配置与首笔预装不计入间隔。
Counts run(const Scenario &s, std::ofstream &trace) {
    const int transactions = s.words == WordTest::Internal ? 1024 : samples;
    VerilatedContext context;
    context.randReset(0);
    Vadc_digital_ip_v21 d(&context);
    Clock clk{context, d, s.clock_fs};
    configure(clk, s.words == WordTest::CorrectedTable, s.words != WordTest::Internal);
    if (s.words == WordTest::Mixed) {
        // 只重播种片组 RNG；扰动序号与已受理的样本数仍须逐笔匹配。
        d.sched_seed = 0x13579BDF;
        d.sched_seed_we = 1;
        clk.cycle();
        d.sched_seed_we = 0;
    }
    std::vector<Sample> plan(1);
    // 生成期望序列与等待宏响应的长短无关。混合测试原子的基本单位是
    // 接受一个请求，而非收到一次比较判决或退休一个输出。
    unsigned state = 0xC001D00D;
    // 横向轮转、纵相位和桥选择由宏上游提供。生产默认对照使用合同 B8 的
    // 上游激励，两个状态与 DUT 内的扰动状态彼此独立。
    unsigned vertical_state = 0x2468ACE1, bridge_state = 0x9E3779B9;
    for (int tag = 1; tag <= transactions; ++tag) {
        auto a = stimulus(tag);
        if (s.words != WordTest::Events) {
            // 固定序列只作独立断言；实际送入 DUT 的比较位与细码将在采集/捕获沿
            // 从 DUT 驱动的开关计算。混合场景在强制字与自动字之间逐笔换手。
            state = next_random(state);
            a.from_switches = true;
            if (s.words == WordTest::Internal) {
                vertical_state = next_random(vertical_state);
                bridge_state = next_random(bridge_state);
                a.h = (tag - 1) & 7;
                a.v = vertical_state & 7;
                a.half = ((tag - 1) >> 3) & 1;
                a.coin = bridge_state & 1;
            }
            a.input_code = 410304; // −3 V + (200 + 11/32) 个粗码步
            a.forced = s.words == WordTest::Sweep || s.words == WordTest::Disconnected ||
                       s.words == WordTest::NominalTable || s.words == WordTest::CorrectedTable ||
                       (s.words == WordTest::Mixed && tag % 5 == 0);
            a.word = a.forced ? (tag - 1) % 64 : int(state & 63);
            a.upper = 4 * (a.word >> 5) + ((a.word >> 3) & 1);
            if (s.words == WordTest::Mixed) {
                // 独立产生覆盖全量程的输入。四分之一输出步可以检测把 Vin
                // 先舍入成目标数字码再伪造 ADC2 原码的错误。
                a.input_code = ((tag - 1) * 55211) % (1 << 20);
                a.input_quarters = (tag - 1) & 3;
                switch ((tag - 1) & 15) {
                case 0: a.input_code = 0; break;
                case 1: a.input_code = (1 << 20) - 1; a.input_quarters = 3; break;
                case 2: a.input_code = 448 * 2048 - 1; break;
                case 3: a.input_code = 511 * 2048 + 704; break;
                case 4: a.input_code = 191 * 2048 + 704; break;
                }
            }
            int ideal = a.input_code + (a.input_quarters >= 2 ? 1 : 0);
            a.sat = ideal >= (1 << 20);
            a.code = std::min(ideal, (1 << 20) - 1);
            a.events.clear(); // 采样前不预装试探位，也不提前知道 Flash 结果
        }
        plan.push_back(a);
    }
    std::vector<Times> time(transactions + 1);
    Counts c;
    int rejected_midstream = 0;
    if (s.words != WordTest::Events)
        reject_probes(clk);
    // 第一份描述符必须在第一个公共沿之前被接受。这个预装不占稳态间隔，
    // 但纳入总受理数；因此 N 个请求最终必须恰好退休 N 次。
    descriptor(d, plan[1]);
    d.prepare_valid = 1;
    d.eval();
    require(d.prepare_ready, "first descriptor not ready");
    clk.cycle();
    unsigned sadc_banks = check_sadc_write(d, &plan[1], 0);
    c.prepared = 1;
    int active = 0, event_index = 0, last_output = -1;
    const auto first_edge_fs = context.time() + s.clock_fs / 2;
    // 每个边沿的日历时间必须与模拟器时钟一致。过期的采样请求不能顺延后再计数。
    // 配置和排空单列，返回时间由持续观测 out_valid 的实际边沿确定。
    for (int tick = 0; tick <= (transactions + 1) * s.frame_clocks + 15; ++tick) {
        const bool aperture = tick % s.frame_clocks == 0;
        const int frame = tick / s.frame_clocks;
        const int sample_tag = aperture && frame < transactions ? frame + 1 : 0;
        const int capture_tag = aperture && frame >= 1 && frame <= transactions ? frame : 0;
        // 输入 i、捕获 i−1、返回 i−2 可落在同一公共沿。最后两个沿只负责排空。
        // 模拟侧的延迟是整整一个采样周期，这会持续占用唯一的 ADC2 owner 槽。
        const int raw_tag = aperture && frame >= 2 && frame <= transactions + 1 ? frame - 1 : 0;
        // 宏合同假设：上一笔细码在下一公共捕获沿已有 valid，允许同沿换主。
        // 负对照只撤去一次返回，必须在实际 capture_arm 上看到本笔错过窗口。
        d.adc2_valid_i = raw_tag && frame != s.withhold_raw_at_frame;
        d.adc2_range_err_i = raw_tag && plan[raw_tag].range;
        if (raw_tag)
            d.adc2_raw_i = plan[raw_tag].raw;
        d.sample_event = aperture && (sample_tag || capture_tag);
        d.input_captured = 0;
        d.residue_captured = 0;
        d.hold_ack = 0;
        // 让下一请求尽早有效。若片组尚处于保持或安静窗，ready 会自然施加反压，
        // 测试端不会据此把整个采样日历往后平移。
        d.prepare_valid = c.prepared < transactions;
        d.stop_i = !d.prepare_valid;
        if (d.prepare_valid)
            descriptor(d, plan[c.prepared + 1]);
        d.ev_flash_valid_i = 0;
        if (sample_tag && c.prepared == sample_tag) {
            d.eval();
            auto &a = plan[sample_tag];
            unsigned mask = (d.sadc_pre_state_o >> (7 * ((sample_tag - 1) & 1))) & 127;
            a.sadc_mask = mask;
            a.lower8 = sadc_eighths(mask);
            // 前级判决只使用 SADC 的开关位。RDAC 的预置独立保存，等其实际
            // 转换开关稳定后再形成余差；两组信息不能互作比较器的输入真值。
            a.sampled_pre8 = physical_pre8(d);
            a.mismatch_pre = (d.pre_wr_o & 1) ? ((lane(d.rdac_pre_state_o, 0, 4) >> 1) & 1) : 0;
            require(a.sampled_pre8 == a.word && a.lower8 == 16 * ((a.word >> 4) & 1) + (a.word & 7),
                    "actual SADC and RDAC pins do not carry the same sample's dither");
            const int applied = s.words == WordTest::Disconnected ? 0 : a.lower8;
            if (a.from_switches)
                set_decisions(a, (4 * a.input_code + a.input_quarters + 1024 * applied) / 8192);
        }
        // 决定序列预先给定，每一位在驱动沿前已稳定；门限拒绝也消耗一次决定。
        // 此处不模拟比较器的判决时间，后续必须以实际宏的最迟有效时刻替换此假设。
        const Event *e = nullptr;
        if (active && event_index < int(plan[active].events.size()))
            e = &plan[active].events[event_index];
        d.ev_bit_valid_i = e != nullptr;
        if (e) {
            d.ev_bit_i = e->decision;
            d.ev_bit_index_i = e->bit;
            d.ev_event_index_i = e->index;
            d.ev_qid_i = (active - 1) & 1;
        }
        // 三种 Flash 返回时机：公共采样沿、其后一沿、第一次提交同沿。
        // 功能场景的 prepare_flash 故意置零，误用早期占位值会立即形成错码。
        if (s.words != WordTest::Events) {
            int ft = 0;
            if (sample_tag && sample_tag % 3 == 0)
                ft = sample_tag;
            if (tick % s.frame_clocks == 1 && frame < transactions && (frame + 1) % 3 == 1)
                ft = frame + 1;
            if (e && event_index == 0 && active % 3 == 2)
                ft = active;
            // 已在采样沿交付 tag 6 的正确 Flash。另一 qid 的返回和首提交后的
            // 同 qid 返回都不应再改变它的前缀，尤其不可把 guard 误换成普通区。
            if (s.words == WordTest::Mixed && frame + 1 == 6 && tick % s.frame_clocks == 1) {
                d.ev_qid_i = 0;
                d.ev_flash_valid_i = 1;
                d.ev_flash_code_i = 0;
            }
            if (s.words == WordTest::Mixed && active == 6 && e && event_index == 1) {
                d.ev_qid_i = 1;
                d.ev_flash_valid_i = 1;
                d.ev_flash_code_i = 0;
            }
            if (ft) {
                require(!e || ft == active, "two different qids on shared event input");
                d.ev_qid_i = (ft - 1) & 1;
                d.ev_flash_valid_i = 1;
                d.ev_flash_code_i = plan[ft].flash;
            }
        }
        d.eval();
        // 不降低采样率来等待 DUT。到了公共孔径时授权未就绪，就记录第一个错过的样本。
        if (capture_tag && !d.adc2_capture_arm_o) {
            c.miss = "capture_arm_low";
            c.missed_tag = capture_tag;
            break;
        }
        // 即使残差可以捕获，本次输入也必须已有对应的采集描述符。
        // 不能把此次公共沿上的新 prepare 当成已经为它采过样。
        if (sample_tag && c.prepared != sample_tag) {
            c.miss = "input_descriptor_missing";
            c.missed_tag = sample_tag;
            break;
        }
        d.input_captured = sample_tag != 0;
        d.residue_captured = capture_tag != 0;
        d.hold_ack = capture_tag != 0;
        d.eval();
        // 核对被捕获的正是预期的上一笔。仅检查 arm 为高，不能发现跨笔借用了
        // 另一笔的 final_k 或错误地向同一笔重复授权。
        if (capture_tag) {
            require(d.node_valid_o && int(d.node_tag_o) == capture_tag &&
                        int(d.node_qid_o) == ((capture_tag - 1) & 1) &&
                        int(d.final_k_o) == plan[capture_tag].final_k,
                    "capture context or final command");
            time[capture_tag].capture = tick;
            auto &measured = plan[capture_tag];
            measured.observed_ids = d.node_ids_o;
            measured.observed_qid = d.node_qid_o;
            measured.observed_pre = d.node_pre_o;
            for (unsigned slot = 0; slot < 8; ++slot)
                measured.observed_post[slot] = lane(d.node_post_o, slot, 20);
            if (s.words != WordTest::Events) {
                capture_ideal_residue(d, plan[capture_tag], s.words);
                require(!plan[capture_tag].range || s.words == WordTest::Disconnected,
                        "correctly connected SADC produced an out-of-range residue");
            }
            ++c.captured;
        }
        if (sample_tag) {
            time[sample_tag].sample = tick;
            ++c.sampled;
        }
        // 同沿新捕获可以替换 owner，返回检查必须在上升沿前读取旧 tag。
        if (d.adc2_valid_i) {
            require(bool(d.adc2_rsp_range_o) == plan[raw_tag].range,
                    "raw range flag crossed transactions");
            c.range_flags += bool(d.adc2_rsp_range_o);
            require(d.adc2_ready_o && d.adc2_rsp_valid_o && int(d.adc2_rsp_tag_o) == raw_tag &&
                        int(d.adc2_rsp_qid_o) == ((raw_tag - 1) & 1) &&
                        int(d.adc2_rsp_raw_o) == plan[raw_tag].raw,
                    "wrong raw owner at capture edge");
            time[raw_tag].raw = tick;
            ++c.returned;
        } else {
            require(!d.adc2_rsp_valid_o, "response without a returned raw");
        }
        // 所有同步受理均以沿前的 valid/ready 为准。沿后寄存器的新值供下一笔使用。
        // 在真正已经可受理的边沿才注入错误。纠正后重试原 tag，能检出
        // 误写 bank、错误推进 qid 或在失败请求上多取了一个随机字。
        bool reject = false;
        if (s.words == WordTest::Mixed && d.prepare_valid && d.prepare_ready) {
            if (d.prepare_tag == 20 && rejected_midstream == 0) {
                d.prepare_U ^= 1; // 该笔是强制字
                reject = true;
            } else if (d.prepare_tag == 31 && rejected_midstream == 1) {
                d.prepare_qid ^= 1; // 请求再次占用刚分配的量化器
                reject = true;
            }
            if (reject) {
                d.eval();
                require(d.err_o && !d.prepare_ready, "invalid live descriptor accepted");
                ++rejected_midstream;
            }
        }
        const bool prepared = d.prepare_ready && d.prepare_valid;
        const bool write = e && e->write;
        require(bool(d.err_o) == reject && bool(d.apply_fire_o) == write,
                "unexpected protocol error or event write");
        if (e) {
            // 比较事件与实际写门独立，拒绝 512 时保留一个 -1 空位。
            auto &measured = plan[active];
            require(event_index < 8, "too many SAR decisions for one sample");
            ++measured.observed_events_count;
            if (d.apply_fire_o)
                measured.observed_events[event_index] = d.k_o;
        }
        if (write)
            require(int(d.tag_o) == active && int(d.k_o) == e->k, "wrong event command/tag");
        clk.rise();
        sadc_banks = check_sadc_write(d, prepared ? &plan[c.prepared + 1] : nullptr, sadc_banks);
        require(context.time() == first_edge_fs + tick * s.clock_fs, "simulator time drift");
        // 沿前检查握手，沿后检查实际寄存写门；门限拒绝不能产生额外写操作。
        require(std::popcount(unsigned(d.post_wr_o)) == (write ? 8 : 0), "wrong physical write count");
        if (write)
            ++c.writes;
        // 样本到末次写入的时间包含等待片组交接。仅把第一事件到末事件的时间
        // 当成端到端前级预算，会漏掉捕获后计安静窗和启动所占的边沿。
        if (e && ++event_index == int(plan[active].events.size())) {
            time[active].post = tick;
            require(d.final_applied_o && int(d.final_k_o) == plan[active].final_k,
                    "final post missing");
            c.max_post_clocks = std::max(c.max_post_clocks, tick - time[active].sample);
        }
        if (prepared)
            ++c.prepared;
        // 最后一笔没有新 prepare；允许 stop 的排空路径建立转换。
        // 只认端口真正出现的新 tag，不能假定每次 prepare 都同沿启动一笔。
        if (int(d.tag_o) != active) {
            require(int(d.tag_o) == active + 1 && (!active || time[active].post >= 0),
                    "start tag order");
            active = d.tag_o;
            time[active].start = tick;
            event_index = 0;
        }
        require(!d.adc2_late_err_o, "late raw error");
        if (d.out_valid) {
            const int tag = ++c.retired;
            require(tag <= transactions && int(d.out_tag) == tag,
                    "missing, duplicated or reordered output");
            const Sample &a = plan[tag];
            auto &t = time[tag];
            t.out = tick;
            require(int(d.out_code) == a.code && int(d.out_sat) == a.sat && !d.out_err,
                    "incorrect 20-bit code or flags on tag " + std::to_string(tag));
            require(t.sample >= 0 && t.start > t.sample && t.post > t.start && t.capture > t.post &&
                        t.raw == t.capture + s.frame_clocks && t.out == t.raw + 6,
                    "sample timing or raw/tag latency");
            // 只看总笔数会漏掉先停顿再追赶的情况。每一对相邻有效输出都须相隔
            // 精确的一帧，首笔的流水填充则只计入该样本的延迟。
            if (last_output >= 0) {
                int gap = tick - last_output;
                c.min_output_gap = c.min_output_gap ? std::min(c.min_output_gap, gap) : gap;
                c.max_output_gap = std::max(c.max_output_gap, gap);
                require(gap == s.frame_clocks, "output cadence differs from sample cadence");
            }
            last_output = tick;
            // 保存每个标签的原码、最终命令和时间列，供独立整数参考确认码与标志。
            trace << s.name << ',' << s.clock_fs << ',' << tag << ',' << a.mode << ',' << a.flash << ','
                  << a.word << ',' << a.h << ',' << a.v << ',' << a.half << ',' << a.coin << ','
                  << a.raw << ',' << a.final_k << ',' << d.out_code << ',' << int(d.out_sat) << ','
                  << int(d.out_err) << ',' << t.sample << ',' << t.start << ',' << t.post << ','
                  << t.capture << ',' << t.raw << ',' << t.out << ',' << a.sampled_pre8 << ','
                  << a.lower8 << ',' << a.range << ',' << a.input_code << ',' << a.mismatch_direction
                  << ',' << a.mismatch_pre << ',' << a.sadc_mask << ',' << a.observed_qid << ','
                  << a.observed_ids << ',' << a.observed_pre << ',' << a.observed_events_count << ',' << a.input_quarters;
            for (auto mask : a.observed_post) trace << ',' << mask;
            for (auto command : a.observed_events) trace << ',' << command;
            trace << '\n';
        }
        clk.fall();
    }
    if (s.words == WordTest::Mixed)
        require(rejected_midstream == 2, "the two live rejection controls did not execute");
    if (s.words == WordTest::Internal) {
        // 运行中复位后两个 bank 的值和写门立即清零。重新完成装表后，第一笔仍
        // 领取复位序列的第一个字；此前已经退休的一千余笔不能改变重播起点。
        d.rst_n = 0;
        d.eval();
        check_sadc_write(d, nullptr, 0);
        configure(clk, false, false);
        descriptor(d, plan[1]);
        d.prepare_valid = 1;
        d.eval();
        require(d.prepare_ready, "restart first transaction not ready");
        clk.cycle();
        check_sadc_write(d, &plan[1], 0);
        d.prepare_valid = 0;
        d.eval();
        require(physical_pre8(d) == 45, "restart did not replay first word");
    }
    d.final();
    // 完整场景须关合整条计数链，不能用相邻两笔时间恰好正确来代替全部交付。
    if (c.miss.empty()) {
        require(c.prepared == transactions && c.sampled == transactions && c.captured == transactions &&
                    c.returned == transactions && c.retired == transactions,
                "incomplete input-to-output stream");
    }
    return c;
}

int main() {
    // 同一份编译结果、同一套输入。只改变时钟/采样沿的关系或显式撤去一次 raw。
    // 频率是理想刺激。所有成立与失败的例子都不对 PDK 时序作假设。
    // 12 与 13 拍的两项在同一主时钟下运行，区分门限回退多消耗的一拍。
    // 两组固定 25 ns 采样的快时钟试验保持样本和宏时序规则一致。
    // 最后一项仅撤去第 4 个捕获沿上的旧细码，检查单一 owner 的真实反压。
    const std::array<Scenario, 12> scenarios = {{
        {"clock25ns_sample25ns", 25000000, 1, -1, "capture_arm_low", 1},
        {"clock25ns_13ticks", 25000000, 13, -1, "", 0},
        {"clock25ns_12ticks", 25000000, 12, -1, "capture_arm_low", 2},
        {"sample25ns_16ticks", 1562500, 16, -1, "", 0},
        {"sample25ns_40ticks", 625000, 40, -1, "", 0},
        {"sample25ns_late_raw", 1562500, 16, 4, "capture_arm_low", 4},
        {"dither_internal", 1562500, 16, -1, "", 0, WordTest::Internal},
        {"dither_sweep", 1562500, 16, -1, "", 0, WordTest::Sweep},
        {"dither_mixed", 1562500, 16, -1, "", 0, WordTest::Mixed},
        {"dither_sadc_disconnected", 1562500, 16, -1, "", 0, WordTest::Disconnected},
        {"dither_nominal_table", 1562500, 16, -1, "", 0, WordTest::NominalTable},
        {"dither_corrected_table", 1562500, 16, -1, "", 0, WordTest::CorrectedTable},
    }};
    std::ofstream trace("samples.csv"), summary("summary.csv");
    if (!trace || !summary)
        return 2;
    trace << "case,clock_fs,tag,path,flash,word,h,v,half,coin,raw,k,code,sat,err,sample,start,post,"
             "capture,raw_edge,out,rdac_pre_eighths,sadc_lower_eighths,adc2_range,input_code,post_"
             "minus_pre,mismatch_pre,sadc_mask,qid,ids_packed,pre_packed,event_count,input_quarters";
    for (int n = 0; n < 8; ++n) trace << ",post_slot" << n;
    for (int n = 0; n < 8; ++n) trace << ",event_k" << n;
    trace << '\n';
    summary
        << "case,clock_fs,sample_fs,prepared,sampled,captured,returned,retired,writes,max_sample_"
           "to_post_ticks,min_out_gap_ticks,max_out_gap_ticks,first_missed_tag,outcome,range_flags\n";
    try {
        for (const auto &s : scenarios) {
            Counts c = run(s, trace);
            summary << s.name << ',' << s.clock_fs << ',' << s.clock_fs * s.frame_clocks << ','
                    << c.prepared << ',' << c.sampled << ',' << c.captured << ',' << c.returned << ','
                    << c.retired << ',' << c.writes << ',' << c.max_post_clocks << ','
                    << c.min_output_gap << ',' << c.max_output_gap << ',' << c.missed_tag << ','
                    << (c.miss.empty() ? "pass" : c.miss) << ',' << c.range_flags << '\n';
            std::cout << s.name << " retired=" << c.retired << " capture_miss_tag=" << c.missed_tag
                      << " result=" << (c.miss.empty() ? "pass" : c.miss) << '\n';
            require(c.miss == s.expected_miss && c.missed_tag == s.expected_tag,
                    "unexpected outcome for " + std::string(s.name));
            require(c.range_flags == (s.words == WordTest::Disconnected ? 96 : 0),
                    "wrong range-error count for " + std::string(s.name));
        }
    } catch (const std::exception &error) {
        std::cerr << "FAIL " << error.what() << '\n';
        return 1;
    }
    std::cout << "SAMPLE_CADENCE_COMPLETE scenarios=" << scenarios.size() << '\n';
    return 0;
}
