"""CAAZ 同步事务的独立队列模型。"""

from collections import deque
from dataclasses import dataclass, replace

_ID_MODULUS = 1 << 32


@dataclass(frozen=True)
class Input:
    """一个上升沿前的数字输入。"""

    rst_n: bool = True
    clear: bool = False
    enable: bool = True
    caaz_enable: bool = True
    acq_valid: bool = False
    acq_id: int = 0
    raw_valid: bool = False
    raw_id: int = 0
    raw_code: int = 0
    raw_bad: bool = False
    raw_chop_neg: bool = False
    raw_az_id: int = 0
    raw_az_ok: bool = True


@dataclass(frozen=True)
class Output:
    """沿后可见的控制、结果和错误状态。"""

    acq_valid: bool = False
    acq_id: int = 0
    chop_neg: bool = False
    az_capture: bool = False
    az_hold: bool = False
    az_id: int = 0
    out_valid: bool = False
    out_code: int = 0
    out_id: int = 0
    out_first_id: int = 0
    out_az_id: int = 0
    out_pair: bool = False
    out_difference: int = 0
    fault: bool = False
    fault_pulse: bool = False
    fault_code: int = 0


@dataclass(frozen=True)
class _Issued:
    sample_id: int
    negative: bool
    az_id: int


class CaazOracle:
    """用采样凭证 FIFO 校验实际结果，按两个码中心的差求配对。"""

    def __init__(self) -> None:
        """初始化尚未发布采样的 epoch。"""
        self._issue_count = 0
        self._mode: bool | None = None
        self._issued: deque[_Issued] = deque()
        self._first: tuple[int, int] | None = None
        self._output = Output()

    def step(self, event: Input) -> Output:
        """按合同处理一拍，返回沿后可见字段。"""
        if not event.rst_n or event.clear or not event.enable:
            self.__init__()
            return self._output
        out = replace(self._output, acq_valid=False, out_valid=False, fault_pulse=False)
        if not out.fault:
            reason = self._reason(event)
            if reason:
                out = replace(out, fault=True, fault_pulse=True, fault_code=reason)
            else:
                # 返回凭证必须已存在，先消费后登记同沿新采样。
                if event.raw_valid:
                    out = self._complete(self._issued.popleft(), event.raw_code, out)
                if event.acq_valid:
                    out = self._publish(event, out)
        self._output = out
        return out

    def _reason(self, event: Input) -> int:
        if self._mode is not None and event.caaz_enable != self._mode:
            return 1
        if event.acq_valid and event.acq_id != self._issue_count % _ID_MODULUS:
            return 2
        if not event.raw_valid:
            return 0
        if not self._issued or event.raw_id != self._issued[0].sample_id:
            return 3
        if event.raw_bad:
            return 4
        if self._mode:
            issued = self._issued[0]
            if event.raw_chop_neg != issued.negative:
                return 5
            if (not event.raw_az_ok) or event.raw_az_id != issued.az_id:
                return 6
        return 0

    def _publish(self, event: Input, out: Output) -> Output:
        self._mode = event.caaz_enable
        pair_id, parity = divmod(event.acq_id, 2)
        issued = _Issued(event.acq_id, bool(self._mode and parity), pair_id if self._mode else 0)
        self._issued.append(issued)
        self._issue_count += 1
        return replace(
            out,
            acq_valid=True,
            acq_id=issued.sample_id,
            chop_neg=issued.negative,
            az_capture=bool(self._mode and not parity),
            az_hold=issued.negative,
            az_id=issued.az_id,
        )

    def _complete(self, issued: _Issued, code: int, out: Output) -> Output:
        if not self._mode:
            return replace(
                out,
                out_valid=True,
                out_code=code,
                out_id=issued.sample_id,
                out_first_id=issued.sample_id,
                out_az_id=0,
                out_pair=False,
                out_difference=2 * code,
            )
        if not issued.negative:
            self._first = issued.sample_id, code
            return out
        if self._first is None:
            raise RuntimeError("odd sample without its first member")
        first_id, first_code = self._first
        self._first = None
        difference = first_code - code
        return replace(
            out,
            out_valid=True,
            out_code=difference // 2,
            out_id=issued.sample_id,
            out_first_id=first_id,
            out_az_id=issued.az_id,
            out_pair=True,
            out_difference=difference,
        )
