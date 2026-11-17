"""公共合同包的导出。"""

# =============================================================================
# 文件名     : __init__.py
# 模块名     : adi_model.contracts
# 功能       : 导出模型合同中跨组件复用的配置、记录和定点类型。
# 设计说明   :
#   * 事件记录和拓扑身份可以由调用方构造，运行配置从已登记的 ADC 查询。
#   * 对外导出名单与显式导入对应，便于在审查中发现退休符号仍被包发布。
# =============================================================================
from adi_model.contracts.events import EventLog
from adi_model.contracts.profiles import (
    ADI_40M,
    DEFAULT_PROFILE_ID,
    PROFILES,
    Profile,
    assert_caaz_free,
    load_profile,
)
# 这些记录可以序列化。校准消费者可用其中的身份字段拒绝混用。
from adi_model.contracts.specs import (
    AcquisitionCommand,
    DigitalObservation,
    EventRecord,
    FrozenCalibration,
    PlantTrace,
    SampleContext,
    SwitchCommand,
    TopologySpec,
)
from adi_model.contracts.task_types import Q32, MapperTable, SwitchState

__all__ = [
    "AcquisitionCommand",
    "ADI_40M",
    "DEFAULT_PROFILE_ID",
    "DigitalObservation",
    "EventRecord",
    "EventLog",
    "FrozenCalibration",
    "MapperTable",
    "Q32",
    "SwitchState",
    "PROFILES",
    "PlantTrace",
    "Profile",
    "SampleContext",
    "SwitchCommand",
    "TopologySpec",
    "assert_caaz_free",
    "load_profile",
]
