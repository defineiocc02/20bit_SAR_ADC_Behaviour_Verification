"""Backward-compatible alias for :mod:`adi_model.behavioral.charge_ref_task`.

History: this module and `charge_ref_task` carried byte-identical copies of
`_network_inputs` and `ref_ra_charge`, plus two versions of
`ref_ra_charge_nodal`. The `charge_ref_task` version is a strict superset: it
adds an optional `extra_sub_charge_c` (default 0.0) to the sub-node equation's
right-hand side, so every existing call behaves identically.

Two copies of one charge formula are exactly the "multiple drifting authorities"
the engineering rules forbid, so the duplicate bodies were removed and this
module now re-exports the single implementation. The public names are unchanged.

Evidence that the collapse is behaviour-preserving: both entry points were hashed
on a fixed linear network before and after the change and produced the same
digest.
"""

from adi_model.behavioral.charge_ref_task import (  # noqa: F401
    _network_inputs,
    ref_ra_charge,
    ref_ra_charge_nodal,
)
