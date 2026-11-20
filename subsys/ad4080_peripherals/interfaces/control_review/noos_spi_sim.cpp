// =============================================================================
// 文件名     : noos_spi_sim.cpp
// 模块名     : noos_spi_sim
// 版本       : v1.0
// 功能       : 对冻结 no-OS 驱动提供纯 Verilator 的 SPI 平台调用。
// 设计说明   :
//   * 配置端固定长指令、Mode 3、MSB first、CRC 关闭。保留驱动的指令
//     和实际写入字节，从 RTL 的 SDO 和 OE 接收响应。
//   * 传输后由仿真侧带输出采集工程诊断。实际项目需另接诊断采集或
//     执行读回，SPI 写时序完成本身不代表配置获准。
//   * 初始化与软件复位后，平台按实际 AON ready 有界等待。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

#include "Vnoos_spi_rtl.h"
#include "verilated.h"
#include <array>
#include <cerrno>
#include <cstdio>
#include <memory>

extern "C" {
#include "no_os_gpio.h"
#include "no_os_spi.h"
int run_noos_host_checks(void);
}

namespace {
Vnoos_spi_rtl *model;
unsigned last_reject;
unsigned frames;
unsigned reject_frames;
unsigned ready_waits;
unsigned data_attempts;

// ============================================================================
// 1. RTL 推进及就绪条件
// ============================================================================
void cycles(unsigned count)
{
    for (unsigned i = 0; i < count; ++i) {
        model->clk_i = 0;
        model->eval();
        model->clk_i = 1;
        model->eval();
        if (model->reject_o && last_reject == 0)
            last_reject = model->reject_reason_o;
    }
}

bool wait_for(bool conversion)
{
    // 100 MHz 固定夹具。40k 周期覆盖本次初始化或单次恢复等待，
    // 离开该窗口后返回超时，防止上游误以为当前地址可访问。
    for (unsigned i = 0; i < 40000; ++i) {
        if (conversion ? model->convert_ok_o : model->ready_o) return true;
        cycles(1);
    }
    return false;
}
}  // namespace

extern "C" int sim_last_reject(void) { return static_cast<int>(last_reject); }
extern "C" int sim_conversion_allowed(void) { return model->convert_ok_o; }
extern "C" int sim_wait_conversion(void) { return wait_for(true) ? 0 : -ETIMEDOUT; }

// ============================================================================
// 2. 真实配置总线
// ============================================================================
extern "C" int sim_cfg_frame(uint8_t *payload, uint16_t size)
{
    if (!payload || (size != 3 && size != 4)) return -EINVAL;
    if (!model->ready_o) ++ready_waits;
    if (!wait_for(false)) return -ETIMEDOUT;
    last_reject = 0;
    std::array<uint8_t, 4> transmitted{};
    const bool read = (payload[0] & 0x80) != 0;
    // 厂商 read 不初始化数据相的 MOSI 占位字节。无 CRC 时该位段无
    // 读协议含义，由平台发送零，保持真实指令两字节和写负载原样。
    for (unsigned i = 0; i < (read ? 2u : size); ++i) transmitted[i] = payload[i];
    const unsigned addr = ((transmitted[0] & 0x7f) << 8) | transmitted[1];
    model->cs_ni = 0;
    cycles(8);
    bool response_owned = true;
    for (unsigned b = 0; b < size; ++b) {
        payload[b] = 0;
        for (int bit = 7; bit >= 0; --bit) {
            model->sdi_i = (transmitted[b] >> bit) & 1;
            model->sclk_i = 0;
            cycles(7);
            // SPI 从机在下降沿后的同步窗口更新 SDO，随后取实际电平；
            // 读数据相任何一位丢失 OE 就整笔失败，不虚构零数据。
            if (read && b >= 2) response_owned &= model->sdo_oe_o;
            payload[b] |= uint8_t(model->sdo_o << bit);
            model->sclk_i = 1;
            cycles(7);
        }
    }
    model->cs_ni = 1;
    cycles(8);
    ++frames;
    if (last_reject) ++reject_frames;
    const int rc = last_reject || !response_owned ? -EIO : 0;
    // 同录原发字节和原生接收值。诊断保留来源，不据地址人工改写响应。
    std::printf("SPI %s addr=%04x tx=", read ? "R" : "W", addr);
    for (unsigned n = 0; n < size; ++n) std::printf("%02x", unsigned(transmitted[n]));
    std::printf(" rx=");
    for (unsigned n = 0; n < size; ++n) std::printf("%02x", unsigned(payload[n]));
    std::printf(" reject=%u rc=%d\n", last_reject, rc);
    return rc;
}

// ============================================================================
// 3. no-OS 平台边界
// ============================================================================
extern "C" int32_t no_os_spi_init(no_os_spi_desc **desc, const no_os_spi_init_param *param)
{
    // 官方 init 同时分配配置和数据描述符。本夹具只执行配置接口 IO。
    if (!desc || !param || param->device_id > 1 || param->mode != NO_OS_SPI_MODE_3 ||
        param->bit_order != NO_OS_SPI_BIT_ORDER_MSB_FIRST) return -EINVAL;
    *desc = new no_os_spi_desc{};
    (*desc)->device_id = param->device_id;
    return 0;
}

extern "C" int32_t no_os_spi_remove(no_os_spi_desc *desc)
{
    delete desc;
    return 0;
}

extern "C" int32_t no_os_spi_write_and_read(no_os_spi_desc *desc, uint8_t *data, uint16_t bytes)
{
    if (!desc) return -EINVAL;
    if (desc->device_id != 0) {
        ++data_attempts;
        return -ENOTSUP;
    }
    return sim_cfg_frame(data, bytes);
}

// 本平台以 SPI 描述符自动控 CS。任何额外 GPIO 平台请求明确失败。
extern "C" int32_t no_os_gpio_get(no_os_gpio_desc **, const no_os_gpio_init_param *) { return -ENOTSUP; }
extern "C" int32_t no_os_gpio_direction_output(no_os_gpio_desc *, uint8_t) { return -ENOTSUP; }
extern "C" int32_t no_os_gpio_set_value(no_os_gpio_desc *, uint8_t) { return -ENOTSUP; }
extern "C" int32_t no_os_gpio_remove(no_os_gpio_desc *) { return -ENOTSUP; }

int main(int argc, char **argv)
{
    // 让模型先于其 Verilator 上下文析构。全局模型会在进程退出阶段
    // 触及已经释放的仿真锁，即使事务自身均已完成也会误报运行失败。
    VerilatedContext context;
    context.commandArgs(argc, argv);
    auto hardware = std::make_unique<Vnoos_spi_rtl>(&context);
    model = hardware.get();
    model->power_ok_i = 0;
    model->cs_ni = model->sclk_i = 1;
    model->sdi_i = 0;
    cycles(8);
    model->power_ok_i = 1;
    const int status = run_noos_host_checks();
    model->final();
    model = nullptr;
    // 两次等待对应初次上电和真实软件复位。结果口仅有一次失败能力探测。
    if (status || ready_waits < 2 || frames == 0 || data_attempts != 1) return 1;
    std::printf("NOOS_SPI_RTL_COMPLETE frames=%u reject_frames=%u ready_waits=%u data_guard=%u\n",
                frames, reject_frames, ready_waits, data_attempts);
    return 0;
}
