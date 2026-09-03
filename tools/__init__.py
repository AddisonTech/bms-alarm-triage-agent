"""Repository tooling that is deliberately not part of the product.

Nothing under tools/ is packaged, and nothing in src/bms_alarm_triage may
import it. It holds the alarm-log generator that builds the frozen test
corpus and the evaluation harness that scores the agent from outside.
"""
