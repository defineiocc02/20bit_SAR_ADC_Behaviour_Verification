"""控制层：调度、DEM、dither 字与码本（从 20b10mtask 迁入）。"""

from .dither_word import DitherWordGenerator, DitherWordOutput
from .fig11_dem import Fig11Masks, map_fig11
from .fig21_bridge import Fig21BridgeResult, map_fig21_bridge
from .module1 import DemSnapshot, Module1, Module1Output
from .scheduler import SchedulerOutput, SliceScheduler

__all__ = ["DitherWordGenerator", "DitherWordOutput", "Fig11Masks", "map_fig11", "Fig21BridgeResult", "map_fig21_bridge", "DemSnapshot", "Module1", "Module1Output", "SchedulerOutput", "SliceScheduler"]
