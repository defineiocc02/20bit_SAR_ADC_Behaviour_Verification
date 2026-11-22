// =============================================================================
// 文件名     : sinc5_comp_coeff_lut.svh
// 模块名     : sinc5_comp 的 FIR 纯函数查表
// 版本       : v1.0
// 功能       : 提供已量化对称 FIR 的一半系数，另一半由消费端配对。
// 设计说明   : 由 design/generate_sinc5_comp.py 生成，Q22，每档 DC 总和恰 2^22。
//              rate 决定 CIC 的 N，tap 0 为两端，tap 17 是中心。
//              工程重设计，源指标参见 AD4080 Rev. B 表 28、图 105～107。
// 变更记录   : 初始版本，暂无变更记录。
// =============================================================================
localparam int PMODE_COMP_TAPS = 35;
localparam int PMODE_COMP_COEF_W = 24;
localparam int PMODE_COMP_COEF_FRAC = 22;

function automatic logic signed [PMODE_COMP_COEF_W-1:0] sinc5_comp_coeff(
    input logic [2:0] rate,
    input int tap
);
    case (rate)
        // CIC N=2，总抽取 D=4
        3'd0: begin
            case (tap)
                 0: sinc5_comp_coeff =       24'sd17;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    -24'sd3948;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd35613;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =     -24'sd119;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd42042;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd30545;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd59080;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd75043;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd44346;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd149226;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =   -24'sd11079;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd226693;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd151900;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd281282;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd458318;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd206778;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1378688;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd1968490;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=4，总抽取 D=8
        3'd1: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd27978;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    24'sd24802;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd27959;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =     24'sd3748;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd58535;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd30731;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd74379;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd93956;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd70022;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd168766;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =     24'sd2436;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd256629;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd153177;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd311760;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd487947;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd205988;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1404034;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2011566;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=8，总抽取 D=16
        3'd2: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd29696;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    24'sd25882;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd27721;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =     24'sd4452;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd60056;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd31738;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd75699;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd96548;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd71326;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd171662;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =     24'sd1547;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd261073;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd157402;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd315014;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd497093;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd200879;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1410155;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2024758;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=16，总抽取 D=32
        3'd3: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd27609;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    24'sd15617;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd36376;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =    -24'sd2142;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd49668;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd44332;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd79242;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd92087;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd56285;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd180246;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =     -24'sd190;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd252388;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd174439;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd314845;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd499179;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd184108;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1420431;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2033920;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=32，总抽取 D=64
        3'd4: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd27496;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    24'sd29334;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd27296;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =     24'sd2085;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd61364;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd28017;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd79083;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd93437;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd77864;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd171383;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =     24'sd8758;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd263854;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd150256;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd321659;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd494163;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd207512;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1408805;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2017356;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=64，总抽取 D=128
        3'd5: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd16606;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    -24'sd5987;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd48343;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =    -24'sd2133;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd43671;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd48865;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd66962;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd95924;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd40706;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd176687;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =   -24'sd23236;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd248252;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd188677;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd294236;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd518960;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd167265;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1422972;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2059688;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=128，总抽取 D=256
        3'd6: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd16626;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    -24'sd5937;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd48310;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =    -24'sd2174;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd43592;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd48884;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd66901;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd95980;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd40683;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd176760;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =   -24'sd23197;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd248270;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd188645;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd294211;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd518956;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd167173;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1422985;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2059816;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        // CIC N=256，总抽取 D=512
        3'd7: begin
            case (tap)
                 0: sinc5_comp_coeff =   -24'sd16627;  // 配对 x[n-0] 和 x[n-34]
                 1: sinc5_comp_coeff =    -24'sd5935;  // 配对 x[n-1] 和 x[n-33]
                 2: sinc5_comp_coeff =    24'sd48310;  // 配对 x[n-2] 和 x[n-32]
                 3: sinc5_comp_coeff =    -24'sd2176;  // 配对 x[n-3] 和 x[n-31]
                 4: sinc5_comp_coeff =   -24'sd43588;  // 配对 x[n-4] 和 x[n-30]
                 5: sinc5_comp_coeff =   -24'sd48886;  // 配对 x[n-5] 和 x[n-29]
                 6: sinc5_comp_coeff =    24'sd66899;  // 配对 x[n-6] 和 x[n-28]
                 7: sinc5_comp_coeff =    24'sd95984;  // 配对 x[n-7] 和 x[n-27]
                 8: sinc5_comp_coeff =   -24'sd40681;  // 配对 x[n-8] 和 x[n-26]
                 9: sinc5_comp_coeff =  -24'sd176766;  // 配对 x[n-9] 和 x[n-25]
                10: sinc5_comp_coeff =   -24'sd23197;  // 配对 x[n-10] 和 x[n-24]
                11: sinc5_comp_coeff =   24'sd248273;  // 配对 x[n-11] 和 x[n-23]
                12: sinc5_comp_coeff =   24'sd188648;  // 配对 x[n-12] 和 x[n-22]
                13: sinc5_comp_coeff =  -24'sd294212;  // 配对 x[n-13] 和 x[n-21]
                14: sinc5_comp_coeff =  -24'sd518964;  // 配对 x[n-14] 和 x[n-20]
                15: sinc5_comp_coeff =   24'sd167164;  // 配对 x[n-15] 和 x[n-19]
                16: sinc5_comp_coeff =  24'sd1422991;  // 配对 x[n-16] 和 x[n-18]
                17: sinc5_comp_coeff =  24'sd2059830;  // 中心 x[n-17]
                default: sinc5_comp_coeff = '0;
            endcase
        end
        default: sinc5_comp_coeff = '0;
    endcase
endfunction
