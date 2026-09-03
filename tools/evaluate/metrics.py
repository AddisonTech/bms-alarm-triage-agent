"""The metrics from P5-C1 and P4-C7, computed in one place.

P5-C1 sets three criteria and one diagnostic:

  1. Top-five escalation capture. On a window with five conditions that
     should be escalated, at least four appear in the agent's top five.
  2. Volume reduction. An order of magnitude fewer items requiring human
     review than raw alarm events in.
  3. False escalation rate at or below 20 percent, the ceiling fixed on
     2026-09-03 before any holdout data was opened.

  Overall escalation recall is reported alongside, so a good top-five
  result cannot hide faults that disappeared from the escalated set
  entirely.

P4-C7 adds escalated count, unresolved count, false escalation count, and
run time to the recorded set.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

ORDER_OF_MAGNITUDE = 10.0


@dataclass(frozen=True)
class Result:
    """One window's numbers, for the agent or for a baseline."""

    label: str
    window: str
    alarm_events_in: int
    distinct_conditions: int
    escalated_count: int
    nuisance_count: int
    unresolved_count: int
    items_for_review: int
    true_escalation_count: int
    top_n: int
    top_n_capture: int
    top_n_required: int
    overall_recall_numerator: int
    false_escalation_count: int
    run_time_s: float

    # ------------------------------------------------------- derived

    @property
    def volume_reduction(self) -> float:
        if self.items_for_review == 0:
            return float("inf") if self.alarm_events_in else 0.0
        return self.alarm_events_in / self.items_for_review

    @property
    def false_escalation_rate(self) -> float:
        """Escalations with no corresponding fault, over all escalations.

        An empty escalated list has no wrong escalations in it, so the
        rate is zero. That is only a pass in combination with the capture
        and recall criteria, which an empty list fails outright.
        """
        if self.escalated_count == 0:
            return 0.0
        return self.false_escalation_count / self.escalated_count

    @property
    def overall_recall(self) -> float:
        if self.true_escalation_count == 0:
            return 0.0
        return self.overall_recall_numerator / self.true_escalation_count

    # -------------------------------------------------------- criteria

    def meets_capture(self) -> bool:
        return self.top_n_capture >= self.top_n_required

    def meets_volume_reduction(self) -> bool:
        return self.volume_reduction >= ORDER_OF_MAGNITUDE

    def meets_false_escalation_ceiling(self, ceiling: float) -> bool:
        return self.false_escalation_rate <= ceiling

    def passes(self, ceiling: float) -> bool:
        return (
            self.meets_capture()
            and self.meets_volume_reduction()
            and self.meets_false_escalation_ceiling(ceiling)
        )

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            {
                "volume_reduction": round(self.volume_reduction, 3),
                "false_escalation_rate": round(self.false_escalation_rate, 4),
                "overall_recall": round(self.overall_recall, 4),
            }
        )
        return payload


def score(
    label: str,
    window: str,
    alarm_events_in: int,
    distinct_conditions: int,
    escalated_ids: list[str],
    nuisance_count: int,
    unresolved_count: int,
    true_ids: set[str],
    top_n: int,
    top_n_required: int,
    run_time_s: float,
) -> Result:
    """Score one ranked escalation list against the ground truth.

    escalated_ids must be in the order the consumer would read them, since
    the top-five criterion is about position rather than membership.

    Items requiring human review counts the unresolved list as well as the
    escalated one. Both land on a person's desk, so leaving unresolved out
    would let a run claim a volume reduction it did not achieve by
    declining to decide.
    """
    top_slice = escalated_ids[:top_n]
    return Result(
        label=label,
        window=window,
        alarm_events_in=alarm_events_in,
        distinct_conditions=distinct_conditions,
        escalated_count=len(escalated_ids),
        nuisance_count=nuisance_count,
        unresolved_count=unresolved_count,
        items_for_review=len(escalated_ids) + unresolved_count,
        true_escalation_count=len(true_ids),
        top_n=top_n,
        top_n_capture=len(set(top_slice) & true_ids),
        top_n_required=top_n_required,
        overall_recall_numerator=len(set(escalated_ids) & true_ids),
        false_escalation_count=len([i for i in escalated_ids if i not in true_ids]),
        run_time_s=round(run_time_s, 4),
    )


def aggregate(label: str, results: list[Result]) -> Result:
    """Pool a set of windows into one line.

    Counts are summed rather than averaged, so a window with more alarms
    weighs more than one with fewer, which is how a rate over a whole
    corpus should behave.
    """
    if not results:
        raise ValueError("cannot aggregate an empty result set")
    return Result(
        label=label,
        window="all (%d windows)" % len(results),
        alarm_events_in=sum(r.alarm_events_in for r in results),
        distinct_conditions=sum(r.distinct_conditions for r in results),
        escalated_count=sum(r.escalated_count for r in results),
        nuisance_count=sum(r.nuisance_count for r in results),
        unresolved_count=sum(r.unresolved_count for r in results),
        items_for_review=sum(r.items_for_review for r in results),
        true_escalation_count=sum(r.true_escalation_count for r in results),
        top_n=results[0].top_n,
        top_n_capture=sum(r.top_n_capture for r in results),
        top_n_required=sum(r.top_n_required for r in results),
        overall_recall_numerator=sum(r.overall_recall_numerator for r in results),
        false_escalation_count=sum(r.false_escalation_count for r in results),
        run_time_s=round(sum(r.run_time_s for r in results), 4),
    )
