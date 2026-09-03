"""Deterministic value series for the synthetic source layer.

Every function here takes an explicit seeded random.Random and returns a
list of floats of the requested length. Nothing reads the clock, nothing
touches module-level random state, and every value is rounded on the way
out so two runs produce byte-identical formatting.

These waveforms stand in for LBNL trend data. They are shaped to produce
the equipment behaviors the build guide's fixture requires, but they are
not claimed to be physically calibrated; that is the carried limitation
recorded in the build guide's CARRIED LIMITATIONS section 3.
"""
from __future__ import annotations

import math
from random import Random

VALUE_DECIMALS = 3


def _round(value: float) -> float:
    return round(value, VALUE_DECIMALS)


def _jitter(rng: Random, amplitude: float) -> float:
    """Symmetric noise drawn from the supplied generator only."""
    return rng.uniform(-amplitude, amplitude)


def steady(n: int, base: float, noise: float, rng: Random) -> list[float]:
    """Flat series with small noise. Never approaches a limit."""
    return [_round(base + _jitter(rng, noise)) for _ in range(n)]


def boundary_noise(
    n: int,
    base: float,
    center: float,
    swing: float,
    noise: float,
    window: tuple[int, int],
    period: int,
    rng: Random,
) -> list[float]:
    """Oscillate across a limit inside a burst window.

    Produces a chattering point. The oscillation has to be wide enough to
    carry the value back through the deadband, otherwise the alarm latches
    once and never clears and the point does not chatter at all. What
    stays small is the overshoot past the limit, and that is what R-D1
    TREND_WITHIN_DEADBAND measures: the caller places the center just
    inside the limit so the peaks barely pass it while the troughs clear
    the deadband comfortably.
    """
    start, end = window
    out: list[float] = []
    for i in range(n):
        if start <= i < end:
            phase = math.sin(2.0 * math.pi * (i - start) / period)
            out.append(_round(center + swing * phase + _jitter(rng, noise)))
        else:
            out.append(_round(base + _jitter(rng, noise)))
    return out


def single_short_excursion(
    n: int,
    base: float,
    peak: float,
    noise: float,
    window: tuple[int, int],
    rng: Random,
) -> list[float]:
    """One brief departure from normal that returns and does not repeat.

    Produces a fleeting alarm. The dwell beyond the limit is short by
    construction, which is what R-D2 detects.
    """
    start, end = window
    out: list[float] = []
    for i in range(n):
        if start <= i < end:
            out.append(_round(peak + _jitter(rng, noise)))
        else:
            out.append(_round(base + _jitter(rng, noise)))
    return out


def step_and_hold(
    n: int,
    base: float,
    held: float,
    noise: float,
    step_at: int,
    rng: Random,
) -> list[float]:
    """Step past the limit and never come back inside the window.

    Produces a stale alarm and the standing condition that R-P1
    SUSTAINED_EXCURSION is meant to promote.
    """
    out: list[float] = []
    for i in range(n):
        level = held if i >= step_at else base
        out.append(_round(level + _jitter(rng, noise)))
    return out


def repeated_large_excursions(
    n: int,
    base: float,
    peak: float,
    noise: float,
    window: tuple[int, int],
    period: int,
    duty: float,
    rng: Random,
    end_on: bool = True,
) -> list[float]:
    """Excursions that recur through the window and are still active at the
    end of it.

    Deliberately shaped so no demote rule fires: the deviation clears the
    deadband, the dwell exceeds the minimum duration, and there is no
    stable normal tail. end_on forces the final burst to run to the last
    sample, because whether the window happens to end mid-burst would
    otherwise depend on how evenly the period divides the window, and a
    case that ends inside normal would be demoted by R-D3 instead.
    """
    start, end = window
    on_samples = max(1, int(period * duty))
    out: list[float] = []
    for i in range(n):
        if start <= i < end:
            in_burst = ((i - start) % period) < on_samples
            if end_on and i >= end - on_samples:
                in_burst = True
            level = peak if in_burst else base
            out.append(_round(level + _jitter(rng, noise)))
        else:
            out.append(_round(base + _jitter(rng, noise)))
    return out


def excursion_then_stable(
    n: int,
    base: float,
    center: float,
    swing: float,
    recovered: float,
    noise: float,
    window: tuple[int, int],
    period: int,
    rng: Random,
) -> list[float]:
    """Look bad for hours, then recover and sit well inside normal.

    This is the build guide's required demote case, and what it has to
    demonstrate is that trend evidence changed a judgment. That only works
    if the condition would not already have been called nuisance without
    the trend: a chattering excursion here would be classified into a
    nuisance category at N4 and the reassessment would have changed
    nothing. So the excursion is sustained rather than chattering, on a
    high priority point. It scores well on alarm-side evidence alone and
    reads as a real standing condition, until the trend shows it cleared
    itself hours ago and stayed clear.

    A swing of zero gives that flat sustained excursion. A non-zero swing
    oscillates across the limit instead, which is available but is not
    what the demote case uses. Either way the peaks sit far enough past
    the limit that R-D1 cannot call it boundary noise, leaving R-D3
    SELF_RECOVERED_AND_STABLE as the rule that fires.
    """
    start, end = window
    out: list[float] = []
    for i in range(n):
        if i < start:
            out.append(_round(base + _jitter(rng, noise)))
        elif start <= i < end:
            phase = math.sin(2.0 * math.pi * (i - start) / period)
            out.append(_round(center + swing * phase + _jitter(rng, noise)))
        else:
            out.append(_round(recovered + _jitter(rng, noise)))
    return out


def linear_drift(
    n: int,
    start_value: float,
    end_value: float,
    noise: float,
    rng: Random,
) -> list[float]:
    """Monotonic ramp across the whole window.

    This is the locked-decision promote case: the alarm side sees only a
    few late transitions and scores the condition modestly, while the
    trend shows steady degradation. R-P2 MONOTONIC_DRIFT promotes it.

    Noise is kept below the reversal tolerance so the ramp reads as
    monotonic rather than as a noisy plateau.
    """
    if n < 2:
        return [_round(start_value)]
    span = end_value - start_value
    out: list[float] = []
    for i in range(n):
        level = start_value + span * (i / (n - 1))
        out.append(_round(level + _jitter(rng, noise)))
    return out


def moderate_excursions(
    n: int,
    base: float,
    peak: float,
    noise: float,
    window: tuple[int, int],
    period: int,
    duty: float,
    rng: Random,
) -> list[float]:
    """Excursions too long to be fleeting, too small to be severe, and
    still active at the end of the window.

    Shaped so that no rule fires at all, which is how the UNCHANGED band
    gets exercised. The same shape at a short period produces a repeating
    alarm, which re-annunciates almost immediately after clearing; only
    the period and duty differ, so both live behind this one function.
    """
    return repeated_large_excursions(
        n=n,
        base=base,
        peak=peak,
        noise=noise,
        window=window,
        period=period,
        duty=duty,
        rng=rng,
    )
