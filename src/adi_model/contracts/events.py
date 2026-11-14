"""事件日志：把 `EventRecord` 按样本与同刻顺序收集，供调试/对拍。

本模块不改变仿真流程；`SimResult` 暂不强制携带它，调用方可显式使用。
"""

from __future__ import annotations

from collections import defaultdict

from adi_model.contracts.specs import EventRecord


class EventLog:
    """按 sample_id 分组、按 (time, order_key) 排序的事件日志。"""

    def __init__(self) -> None:
        self._events: dict[int, list[EventRecord]] = defaultdict(list)

    def add(self, event: EventRecord) -> None:
        """把一条事件追加到其 sample_id 名下。

        Args:
            event: 已构造的事件记录；其 `sample_id` 决定归属。

        Side effects: 原地追加。**同一 sample 内不排序**——排序发生在读取时
            （`for_sample`），因此追加顺序不影响最终顺序。
        """
        self._events[event.sample_id].append(event)

    def for_sample(self, sample_id: int) -> tuple[EventRecord, ...]:
        """返回某个 sample 的事件，按 (time_s, order_key) 排序。

        Args:
            sample_id: 采样序号；未知 id 返回空元组（不抛异常）。

        Returns:
            不可变元组，调用方不能破坏内部状态。

        排序用 `order_key` 作次级键：同一时间戳上多个事件时，先后由它决定，
        这样"同沿优先级"是显式可核对的，不依赖插入顺序。
        """
        return tuple(sorted(self._events.get(sample_id, ()),
                            key=lambda e: (e.time_s, e.order_key)))

    def all(self) -> tuple[EventRecord, ...]:
        """返回全部事件：先按 sample_id 升序，再按 `for_sample` 的顺序。

        Returns:
            不可变元组；空日志返回空元组。
        """
        out = []
        for sid in sorted(self._events):
            out.extend(self.for_sample(sid))
        return tuple(out)

    def __len__(self) -> int:
        return sum(len(v) for v in self._events.values())
