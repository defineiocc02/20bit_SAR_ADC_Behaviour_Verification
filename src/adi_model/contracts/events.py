"""事件日志：把 `EventRecord` 按样本与同刻顺序收集，供调试/对拍。

本模块不改变仿真流程；`SimResult` 暂不强制携带它，调用方可显式使用。
"""

from __future__ import annotations

from collections import defaultdict

from .specs import EventRecord


class EventLog:
    """按 sample_id 分组、按 (time, order_key) 排序的事件日志。"""

    def __init__(self) -> None:
        self._events: dict[int, list[EventRecord]] = defaultdict(list)

    def add(self, event: EventRecord) -> None:
        self._events[event.sample_id].append(event)

    def for_sample(self, sample_id: int) -> tuple[EventRecord, ...]:
        return tuple(sorted(self._events.get(sample_id, ()),
                            key=lambda e: (e.time_s, e.order_key)))

    def all(self) -> tuple[EventRecord, ...]:
        out = []
        for sid in sorted(self._events):
            out.extend(self.for_sample(sid))
        return tuple(out)

    def __len__(self) -> int:
        return sum(len(v) for v in self._events.values())
