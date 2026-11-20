// =============================================================================
// 文件名     : noos_host_recipe.c
// 模块名     : noos_host_recipe
// 版本       : v1.0
// 功能       : 运行未改的 ADI no-OS API，并给出水位普通字节模式兼容配方。
// 设计说明   :
//   * 只编写调用端。厂商驱动和头文件来自单独固定的归档，驱动的读改写、
//     SPI 指令及水位低/高字节顺序保持原样。
//   * 水位配方要求调用方独占配置并保持 FIFO 未 arm。预置高字节 0x01
//     避免中间值触及冻结 bank 的 0 或 0x4000 边界。
//   * 完整读回匹配后才用官方 API arm。独立严格实体访问同时复验。
// -----------------------------------------------------------------------------
// 变更记录
// -----------------------------------------------------------------------------
// 初始版本，暂无变更记录。
// =============================================================================

#include <errno.h>
#include <stdio.h>
#include "ad4080.h"

// ============================================================================
// 1. 主机兼容配方
// ============================================================================
int project_ad4080_set_fifo_checked(struct ad4080_dev *dev, uint16_t watermark,
                                  enum ad4080_fifo_mode mode)
{
    enum ad4080_strict_reg_access strict;
    enum ad4080_fifo_mode state;
    uint16_t observed;
    int rc;

    if (!dev || watermark == 0 || watermark > AD4080_FIFO_SIZE ||
        (mode != AD4080_IMMEDIATE_TRIGGER && mode != AD4080_EVENT_TRIGGER_LAST_WM &&
         mode != AD4080_EVENT_TRIGGER) ||
        (mode == AD4080_EVENT_TRIGGER && (watermark & 3u))) return -EINVAL;
    rc = ad4080_get_strict_reg_access(dev, &strict);
    if (rc) return rc;
    if (strict != AD4080_REG_NORMAL_MODE) return -ENOTSUP;
    rc = ad4080_get_fifo_mode(dev, &state);
    if (rc) return rc;
    if (state != AD4080_FIFO_DISABLE) return -EBUSY;

    // 先使旧高字节落到 0x01，随后低字节的任何中间值都在 0x0100–0x01FF。
    // 最终高字节由原厂 API 写入。对目标最小 1 和最大 0x4000 同样成立。
    rc = ad4080_write(dev, AD4080_REG_FIFO_WATERMARK + 1, 0x01);
    if (rc) return rc;
    rc = ad4080_set_fifo_watermark(dev, watermark);
    if (rc) return rc;
    rc = ad4080_get_fifo_watermark(dev, &observed);
    if (rc) return rc;
    if (observed != watermark) return -EBADMSG;

    // 0x1C 改写之后若读回失败，应把模式当作未知并按实际状态恢复。
    rc = ad4080_set_fifo_mode(dev, mode);
    if (rc) return rc;
    rc = ad4080_get_fifo_mode(dev, &state);
    if (rc) return rc;
    return state == mode ? 0 : -EBADMSG;
}

// ============================================================================
// 2. 未改驱动流程及负例
// ============================================================================
#ifdef AD4080_NOOS_SIM
// 生产侧可直接编译本文件的水位配方。下方只进入本包的仿真入口。
extern int sim_cfg_frame(uint8_t *payload, uint16_t size);
extern int sim_last_reject(void);
extern int sim_conversion_allowed(void);
extern int sim_wait_conversion(void);

#define VERIFY(expr) do { \
    if (!(expr)) { \
        fprintf(stderr, "NOOS_ASSERT line=%d %s\n", __LINE__, #expr); \
        return 1; \
    } \
    ++checks; \
} while (0)

int run_noos_host_checks(void)
{
    unsigned checks = 0;
    struct ad4080_dev *dev = NULL;
    uint8_t value, product_lo, product_hi, chip;
    uint16_t observed;
    enum ad4080_addr_asc order;
    enum ad4080_strict_reg_access strict;
    enum ad4080_op_mode op;
    enum ad4080_fifo_mode fifo;
    struct no_os_spi_init_param cfg_spi = {
        .device_id = 0, .max_speed_hz = 8000000,
        .mode = NO_OS_SPI_MODE_3, .bit_order = NO_OS_SPI_BIT_ORDER_MSB_FIRST
    };
    struct no_os_spi_init_param data_spi = cfg_spi;
    data_spi.device_id = 1;
    // 调用方选定公开默认数据配置，只向固定字段发相同值的读改写。
    struct ad4080_init_param config = {
        .id = ID_AD4080, .cfg = {.spi = &cfg_spi}, .data = {.spi = &data_spi},
        .addr_asc = AD4080_ADDR_INCR, .single_instr = AD4080_STREAM_MODE,
        .short_instr = AD4080_15_BIT_ADDR, .op_mode = AD4080_OP_NORMAL,
        .strict_reg = AD4080_REG_NORMAL_MODE, .cnv_spi_lvds_lanes = AD4080_ONE_LANE,
        .conv_data_spi_lvds = AD4080_CONV_DATA_LVDS, .lvds_cnv_clk_cnt = 0,
        .lvds_self_clk_mode = AD4080_ECHO_CLK_MODE, .cnv_clk_mode = AD4080_CNV_CMOS_MODE,
        .lvds_vod = AD4080_240mVPP, .fifo_mode = AD4080_FIFO_DISABLE
    };
    VERIFY(ad4080_init(&dev, config) == 0 && dev != NULL);
    VERIFY(ad4080_read(dev, AD4080_REG_CHIP_TYPE, &chip) == 0 && chip == AD4080_CHIP_ID);
    VERIFY(ad4080_read(dev, AD4080_REG_PRODUCT_ID_L, &product_lo) == 0);
    VERIFY(ad4080_read(dev, AD4080_REG_PRODUCT_ID_H, &product_hi) == 0);
    VERIFY((product_lo | (product_hi << 8)) == AD4080_PRODUCT_ID);
    VERIFY(ad4080_get_addr_asc(dev, &order) == 0 && order == AD4080_ADDR_INCR);
    VERIFY(ad4080_get_strict_reg_access(dev, &strict) == 0 && strict == AD4080_REG_NORMAL_MODE);
    printf("OBS noos_init chip=%02x product=%02x%02x addr_asc=1 strict=0\n",
           chip, product_hi, product_lo);

    // 数据接口配置流程可使用固定值，改变实际数据 PHY 的请求要显式拒绝。
    VERIFY(ad4080_set_conv_data_spi_lvds(dev, AD4080_CONV_DATA_SPI) == -EIO && sim_last_reject() == 2);
    VERIFY(ad4080_read(dev, AD4080_REG_DATA_INTF_CONFIG_A, &value) == 0 && value == 0x40);
    VERIFY(ad4080_read_data(dev, &value, 1) == -ENOTSUP);
    printf("OBS noos_data_profile readonly_change=blocked data_transfer=not_bound\n");

    VERIFY(ad4080_set_op_mode(dev, AD4080_OP_STANDBY) == 0);
    VERIFY(ad4080_get_op_mode(dev, &op) == 0 && op == AD4080_OP_STANDBY && !sim_conversion_allowed());
    VERIFY(ad4080_set_op_mode(dev, AD4080_OP_NORMAL) == 0);
    VERIFY(ad4080_get_op_mode(dev, &op) == 0 && op == AD4080_OP_NORMAL);
    VERIFY(!sim_conversion_allowed() && sim_wait_conversion() == 0);
    printf("OBS noos_mode standby=2 normal=0 recovery_wait=1\n");

    // 两次单字段 read-modify-write 验证原驱动保留未选滤波位。
    VERIFY(ad4080_update_bits(dev, AD4080_REG_FILTER_CONFIG, AD4080_FILTER_SEL_MSK,
                              AD4080_FILTER_SINC1) == 0);
    VERIFY(ad4080_update_bits(dev, AD4080_REG_FILTER_CONFIG, AD4080_SINC_DECIMATION_MSK,
                              AD4080_SINC_DEC_16 << 3) == 0);
    VERIFY(ad4080_read(dev, AD4080_REG_FILTER_CONFIG, &value) == 0 && value == 0x19);
    VERIFY(ad4080_update_bits(dev, AD4080_REG_FILTER_CONFIG, AD4080_FILTER_SEL_MSK,
                              AD4080_FILTER_SINC5_PLUS) == -EIO && sim_last_reject() == 1);
    VERIFY(ad4080_read(dev, AD4080_REG_FILTER_CONFIG, &value) == 0 && value == 0x19);
    printf("OBS noos_filter sinc1_D16=0x19 unbound_comp_preserved=1\n");

    // 该 AD408x 通用枚举与本器件手册冲突。用原函数发出 0xA 并检查独立桥的拒绝。
    VERIFY(ad4080_set_gpio_output_func(dev, AD4080_GPIO_1, AD4080_GPIO_CNV_INHIBIT_INPUT)
           == -EIO && sim_last_reject() == 2);
    VERIFY(ad4080_read(dev, AD4080_REG_GPIO_CONFIG_B, &value) == 0 && value == 0);
    printf("OBS noos_gpio enum_0xa_rejected=2\n");

    // [反例] 从真实复位水位调用原 no-OS helper，完整读取仍为 0x0100。
    VERIFY(ad4080_get_fifo_watermark(dev, &observed) == 0 && observed == 0x4000);
    VERIFY(ad4080_set_fifo_watermark(dev, 0x01aa) == 0);
    VERIFY(ad4080_get_fifo_watermark(dev, &observed) == 0 && observed == 0x0100);
    printf("COUNTEREXAMPLE noos_unmodified_default_4000 requested=01aa observed=0100\n");

    VERIFY(project_ad4080_set_fifo_checked(dev, 0x4000, AD4080_IMMEDIATE_TRIGGER) == 0);
    VERIFY(ad4080_set_fifo_mode(dev, AD4080_FIFO_DISABLE) == 0);
    VERIFY(ad4080_get_fifo_watermark(dev, &observed) == 0 && observed == 0x4000);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x01aa, AD4080_IMMEDIATE_TRIGGER) == 0);
    VERIFY(ad4080_get_fifo_mode(dev, &fifo) == 0 && fifo == AD4080_IMMEDIATE_TRIGGER);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x0100, AD4080_IMMEDIATE_TRIGGER) == -EBUSY);
    VERIFY(ad4080_set_fifo_mode(dev, AD4080_FIFO_DISABLE) == 0);
    VERIFY(ad4080_set_addr_asc(dev, AD4080_ADDR_DECR) == 0);
    VERIFY(ad4080_get_addr_asc(dev, &order) == 0 && order == AD4080_ADDR_DECR);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x0100, AD4080_IMMEDIATE_TRIGGER) == 0);
    VERIFY(ad4080_set_fifo_mode(dev, AD4080_FIFO_DISABLE) == 0);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x0001, AD4080_IMMEDIATE_TRIGGER) == 0);
    VERIFY(ad4080_set_fifo_mode(dev, AD4080_FIFO_DISABLE) == 0);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x01aa, AD4080_EVENT_TRIGGER) == -EINVAL);
    VERIFY(ad4080_get_fifo_mode(dev, &fifo) == 0 && fifo == AD4080_FIFO_DISABLE);
    printf("OBS noos_fifo_recipe default_4000_to_01aa=ok ASC=1 DESC=1 sample_edges=0001,0100,4000 arm=1\n");

    // 仍可在同一台 RTL 选择严格实体。用原 API 使能并证明其普通字节
    // helper 在 strict 下被拦，再向同一配置 SPI 提交完整 2 字节帧。
    VERIFY(ad4080_set_strict_reg_access(dev, AD4080_REG_STRICT_MODE) == 0);
    VERIFY(ad4080_get_strict_reg_access(dev, &strict) == 0 && strict == AD4080_REG_STRICT_MODE);
    VERIFY(project_ad4080_set_fifo_checked(dev, 0x01aa, AD4080_IMMEDIATE_TRIGGER) == -ENOTSUP);
    VERIFY(ad4080_set_fifo_watermark(dev, 0x01aa) == -EIO && sim_last_reject() == 5);
    uint8_t set_pair[4] = {0x00, AD4080_REG_FIFO_WATERMARK + 1, 0x01, 0xaa};
    uint8_t get_pair[4] = {0x80, AD4080_REG_FIFO_WATERMARK + 1, 0, 0};
    VERIFY(sim_cfg_frame(set_pair, sizeof(set_pair)) == 0);
    VERIFY(sim_cfg_frame(get_pair, sizeof(get_pair)) == 0 && get_pair[2] == 0x01 && get_pair[3] == 0xaa);
    printf("OBS noos_strict byte_helper_reject=5 atomic_desc_read=01aa\n");
    VERIFY(ad4080_remove(dev) == 0);
    printf("NOOS_HOST_COMPLETE checks=%u\n", checks);
    return 0;
}
#endif
