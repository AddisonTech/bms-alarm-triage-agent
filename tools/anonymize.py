"""Scrub a real alarm and trend export so it can be run and shown safely.

P4-C2 keeps customer data out of the repository, and the showcase is meant
to be sent to people. This sits between the two: it takes a real export
pair in the agent's input schema and writes a copy with everything that
names a place replaced, consistently across both files, so the pipeline
gives the same result on the copy as on the original.

What is replaced:
  - site, building, floor, and every other context segment of a point path
  - equipment tags, kept to their type prefix (AHU-21-B becomes AHU-01)
  - any part of a point name that is not a short generic word, so
    SA_TEMP stays SA_TEMP and RM_214_TEMP becomes RM_TEMP
  - alarm class names that are not generic words
  - row ids, renumbered
  - optionally, the dates, shifted by whole days with time of day kept

What is kept, because the agent needs it: values, limits, deadbands,
units, priorities, transitions, and the time of day.

The key that reverses the mapping is written beside the scrubbed copy as
PRIVATE_key.json. It is the one file that must not be shared. The default
output directory is under private/, which git ignores.

Run from the repository root:

    python -m tools.anonymize ALARMS.csv TRENDS.csv
    python -m tools.anonymize ALARMS.csv TRENDS.csv --out private\\anon --shift-days 90
    python -m tools.showcase --alarms private\\anon\\alarm_export.csv --trends private\\anon\\trend_export.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
KEY_FILENAME = "PRIVATE_key.json"
DEFAULT_OUT = REPO_ROOT / "private" / "anon"

# Words that say what a point or alarm class is without saying where it is.
GENERIC_WORDS = frozenset(
    """
    sa ra oa da ma ea mixed air chw hw cw htg clg sup ret supply return temp tmp
    vlv valve pos position dmpr dmpr damper sp dp static press pressure spd speed
    sf rf ef fan cond evap suct oil comp status stat duct coil disch discharge
    zone zn airflow flow firing rate stack comb combustion pan lvl level rht
    reheat approach rh humidity co2 kw kwh amps volts cmd fb out in hi lo high
    low alarm alm fault flt run enable sts pct cfm gpm freeze frz smoke filter
    dpr occ unocc eff setpt stpt heat cool pump pmp boiler blr chiller ch ahu
    vav fcu rtu tower ct hx default critical medium priority class life safety
    maintenance warning info hvac general minor major t p h
    """.split()
)

TYPE_PREFIX = re.compile(r"^([A-Za-z]{2,5})")


class Scrubber:
    """One consistent mapping for both files of an export pair."""

    def __init__(self, equipment_tags: set[str]) -> None:
        self.equipment_tags = {tag.lower(): tag for tag in equipment_tags}
        self.equipment: dict[str, str] = {}
        self.sites: dict[str, str] = {}
        self.areas: dict[str, str] = {}
        self.classes: dict[str, str] = {}
        self.points: dict[str, str] = {}
        self._type_counts: dict[str, int] = {}
        self._used_paths: set[str] = set()

    def equipment_tag(self, tag: str) -> str:
        if tag not in self.equipment:
            match = TYPE_PREFIX.match(tag)
            kind = match.group(1).upper() if match and match.group(1).lower() in GENERIC_WORDS else "EQ"
            self._type_counts[kind] = self._type_counts.get(kind, 0) + 1
            self.equipment[tag] = "%s-%02d" % (kind, self._type_counts[kind])
        return self.equipment[tag]

    def _site(self, segment: str) -> str:
        if segment not in self.sites:
            self.sites[segment] = "SITE-%s" % chr(ord("A") + len(self.sites) % 26)
        return self.sites[segment]

    def _area(self, segment: str) -> str:
        if segment not in self.areas:
            self.areas[segment] = "AREA-%02d" % (len(self.areas) + 1)
        return self.areas[segment]

    @staticmethod
    def point_name(name: str) -> str:
        """Keep the generic words of a point name, drop the rest."""
        words = [w for w in re.split(r"[^A-Za-z0-9]+", name) if w]
        kept = [w for w in words if w.lower() in GENERIC_WORDS]
        return "_".join(kept).upper() or "PT"

    def point_path(self, path: str) -> str:
        if path in self.points:
            return self.points[path]
        segments = path.split("/")
        context, name = segments[:-1], segments[-1]
        mapped = []
        for index, segment in enumerate(context):
            if segment.lower() in self.equipment_tags:
                mapped.append(self.equipment_tag(self.equipment_tags[segment.lower()]))
            elif index == 0:
                mapped.append(self._site(segment))
            else:
                mapped.append(self._area(segment))
        base = "/".join(mapped + [self.point_name(name)])
        candidate, suffix = base, 1
        while candidate in self._used_paths:
            suffix += 1
            candidate = "%s_%d" % (base, suffix)
        self._used_paths.add(candidate)
        self.points[path] = candidate
        return candidate

    def alarm_class(self, name: str) -> str:
        words = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)
        if words and all(w.lower() in GENERIC_WORDS for w in words):
            return name
        if name not in self.classes:
            self.classes[name] = "Class%d" % (len(self.classes) + 1)
        return self.classes[name]

    def key(self) -> dict:
        return {
            "note": "Reverses the scrubbing. Never share this file.",
            "sites": self.sites,
            "areas": self.areas,
            "equipment": self.equipment,
            "alarm_classes": self.classes,
            "point_paths": self.points,
        }


def _shift(stamp: str, days: int) -> str:
    if not days:
        return stamp
    return (datetime.fromisoformat(stamp) + timedelta(days=days)).isoformat()


def _read(path: Path) -> tuple[list[str], list[dict]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def _write(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def scrub(alarm_path: Path, trend_path: Path, out_dir: Path, shift_days: int = 0) -> dict:
    """Write the scrubbed pair and the key. Returns a short summary."""
    alarm_fields, alarms = _read(alarm_path)
    trend_fields, trends = _read(trend_path)
    scrubber = Scrubber({row["equipment"] for row in alarms})

    for number, row in enumerate(alarms, start=1):
        row["row_id"] = "R%05d" % number
        row["timestamp"] = _shift(row["timestamp"], shift_days)
        row["point_path"] = scrubber.point_path(row["point_path"])
        row["equipment"] = scrubber.equipment_tag(row["equipment"])
        row["alarm_class"] = scrubber.alarm_class(row["alarm_class"])
    for row in trends:
        row["timestamp"] = _shift(row["timestamp"], shift_days)
        row["point_path"] = scrubber.point_path(row["point_path"])

    out_dir.mkdir(parents=True, exist_ok=True)
    _write(out_dir / "alarm_export.csv", alarm_fields, alarms)
    _write(out_dir / "trend_export.csv", trend_fields, trends)
    key = scrubber.key()
    (out_dir / KEY_FILENAME).write_text(json.dumps(key, indent=2), encoding="utf-8")

    leaks = leftover_names(out_dir, key)
    return {
        "alarm_rows": len(alarms),
        "trend_rows": len(trends),
        "points": len(scrubber.points),
        "equipment": len(scrubber.equipment),
        "leaks": leaks,
    }


def leftover_names(out_dir: Path, key: dict) -> list[str]:
    """Original names that still appear anywhere in the scrubbed files."""
    originals = set(key["sites"]) | set(key["areas"]) | set(key["equipment"]) | set(key["alarm_classes"])
    text = "\n".join(
        (out_dir / name).read_text(encoding="utf-8") for name in ("alarm_export.csv", "trend_export.csv")
    )
    return sorted(
        name for name in originals
        if len(name) >= 3 and name.lower() not in GENERIC_WORDS
        and re.search(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(name), text)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.anonymize",
        description="Scrub site, equipment, and point names from a real export pair.",
    )
    parser.add_argument("alarm_export", type=Path)
    parser.add_argument("trend_export", type=Path)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--shift-days", type=int, default=0, help="move every date by this many days, keeping time of day"
    )
    args = parser.parse_args(argv)

    summary = scrub(args.alarm_export, args.trend_export, args.out, args.shift_days)
    print(
        "scrubbed %d alarm rows and %d trend rows: %d points on %d pieces of equipment"
        % (summary["alarm_rows"], summary["trend_rows"], summary["points"], summary["equipment"])
    )
    print("scrubbed exports: %s" % args.out)
    print("key (do not share): %s" % (args.out / KEY_FILENAME))
    if summary["leaks"]:
        print("WARNING: these original names still appear in the output: %s" % ", ".join(summary["leaks"]))
        return 1
    print("check: no original site, area, equipment, or class name remains in the output")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
