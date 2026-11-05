"""供不可变数值接口共用的数组所有权约定。"""

import numpy as np


def readonly(value, dtype=float):
    """复制为不可变字节存储上的数组，不能重新恢复可写标志。"""
    array = np.ascontiguousarray(value, dtype=dtype)
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)
