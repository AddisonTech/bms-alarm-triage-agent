"""The evaluation harness. Deliberately outside the agent package.

P2-C5 is explicit that the judgment is external and objective and that the
agent never rates its own output. Keeping the harness here rather than in
src/ is the structural expression of that: no node can reach the ground
truth, and a test asserts as much.

What the harness measures is the three criteria from P5-C1, the diagnostic
recall alongside them, and the two baselines the same clause requires the
agent to be reported against. HVAC fault correctness is scored against
ground truth this project did not create, per P3-C2.
"""

HARNESS_VERSION = "1.0.0"
