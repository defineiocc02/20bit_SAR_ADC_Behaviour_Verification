"""Small array ownership contracts shared by immutable numeric interfaces."""

import numpy as np


def readonly(value, dtype=float):
    """Copy into immutable bytes-backed storage, preventing writable flag recovery."""
    array = np.ascontiguousarray(value, dtype=dtype)
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)
