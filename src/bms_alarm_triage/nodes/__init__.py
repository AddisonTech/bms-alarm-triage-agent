"""The nine nodes from P0-C2, one module each.

Each node is a plain function from state to a state update, so every one
can be called and tested on its own. P0-C3 makes that a requirement rather
than a preference: a node that cannot be tested independently has the
wrong contract.
"""

from .n1_ingest import n1_ingest
from .n2_validate import n2_validate
from .n3_normalize import n3_normalize
from .n4_cluster import n4_cluster
from .n5_rank import n5_preliminary_rank
from .n6_evidence import n6_evidence
from .n7_reassess import n7_reassess
from .n8_explain import n8_explain
from .n9_report import n9_report

__all__ = [
    "n1_ingest",
    "n2_validate",
    "n3_normalize",
    "n4_cluster",
    "n5_preliminary_rank",
    "n6_evidence",
    "n7_reassess",
    "n8_explain",
    "n9_report",
]
