"""Alarm-log generator: a test fixture, never product.

This package deliberately lives outside src/bms_alarm_triage so the agent
cannot import it. Per P3-C2 of the build guide the generator is frozen
before any agent code is written and is never shipped as part of the
product. The agent is tested against the committed corpus, not against a
live call into this package.

Layer separation, which is the single most important claim in P3-C2:

  catalog.py  declares the source layer. Equipment, points, value
              waveforms and the labeled HVAC fault windows. This stands
              in for the LBNL dataset and it is the only place fault
              ground truth exists.

  isa182.py   applies BAS alarm mechanics to a value series. It receives
              values and an alarm specification and nothing else. It
              never sees a fault window, so it cannot decide whether an
              underlying HVAC fault exists.

tests/test_generator_layer_separation.py enforces that boundary.
"""

GENERATOR_VERSION = "1.0.0"
