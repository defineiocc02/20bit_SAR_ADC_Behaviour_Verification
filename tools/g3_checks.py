"""G3 必要保护（4 条，一个小文件，不做通用框架）。"""
class Txn:
    def __init__(self, tag, ids, pre_bits):
        self.tag, self.ids = tag, ids
        self.pre = [r[:] for r in pre_bits]     # 不可变副本
        self.state = 'PREP'
        self.retired = False

    def sample(self):
        assert self.state == 'PREP', "未准备就采样"
        self.state = 'SAMP'

    def to_convert(self):
        assert self.state == 'SAMP', "未采样就转换"
        self.state = 'CONV'

    def adc2(self):
        assert self.state == 'CONV', "未转换就收回 ADC2"
        self.state = 'ADC2'

    def retire(self):
        assert self.state == 'ADC2', "未收到 ADC2 就输出"
        assert not self.retired, "重复结束同一事务"
        self.retired = True
        self.state = 'DONE'
        return self.tag


def reset(txns):
    for t in txns:
        t.state, t.retired = 'CANCEL', True
    return len(txns)


if __name__ == '__main__':
    from algo_chain import pre
    bits, *_ = pre(17, 3)
    ok = 0
    # 1) 未采样就转换
    try:
        Txn(1, list(range(8)), bits).to_convert(); print("FAIL 未拦未采样")
    except AssertionError: ok += 1
    # 2) 未收到 ADC2 就输出
    t = Txn(2, list(range(8)), bits)
    try:
        t.retire(); print("FAIL 未拦早退")
    except AssertionError: ok += 1
    # 3) 重复结束
    t = Txn(3, list(range(8)), bits); t.sample(); t.to_convert(); t.adc2(); t.retire()
    try:
        t.retire(); print("FAIL 未拦重复退休")
    except AssertionError: ok += 1
    # 4) pre-mask 副本不被后续修改影响
    t = Txn(4, list(range(8)), bits)
    frozen = [r[:] for r in t.pre]
    bits[0][0] ^= 1
    assert t.pre == frozen, "pre-mask 副本被外部修改"
    ok += 1
    # 5) reset 清空在途
    tx = [Txn(i, list(range(8)), bits) for i in (5, 6)]
    n = reset(tx)
    assert all(x.state == 'CANCEL' for x in tx) and n == 2
    ok += 1
    print(f"G3 状态检查: {ok}/5 通过" + ("  PASS" if ok == 5 else "  FAIL"))
    assert ok == 5
