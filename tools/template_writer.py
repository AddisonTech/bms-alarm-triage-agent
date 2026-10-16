"""The no-model N8 writer, kept importable from its old place.

It moved into the packaged bms_triage_service so a program that installs
the agent can use it too. The showcase, the demo, and the tests still
import it from here.
"""
from bms_triage_service.template_writer import (  # noqa: F401
    NAME,
    TemplateClient,
    compose,
    instrument,
    point_words,
)
