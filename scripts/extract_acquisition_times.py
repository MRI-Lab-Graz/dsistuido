#!/usr/bin/env python3
"""Extract T1w acquisition datetimes per subject/session from a BIDS rawdata tree
and report the time-of-day difference between ses-1 and ses-2."""

import json
import re
from datetime import datetime, time
from pathlib import Path

RAWDATA = Path("/data/local/129_PK01/rawdata")
OUTFILE = Path("/data/local/129_PK01/rawdata/code/acquisition_times.tsv")

SUB_RE = re.compile(r"sub-(\d+)")
SES_RE = re.compile(r"ses-(\d+)")


def find_t1w_jsons():
    return sorted(RAWDATA.glob("sub-*/ses-*/anat/*T1w.json"))


def time_of_day_diff_minutes(t1: time, t2: time) -> float:
    """Absolute difference between two times-of-day, in minutes (0-720 range)."""
    m1 = t1.hour * 60 + t1.minute + t1.second / 60
    m2 = t2.hour * 60 + t2.minute + t2.second / 60
    diff = abs(m1 - m2)
    return min(diff, 1440 - diff)


def main():
    records = {}  # (sub, ses) -> datetime

    for jf in find_t1w_jsons():
        sub_m = SUB_RE.search(jf.parts[-4])
        ses_m = SES_RE.search(jf.parts[-3])
        if not sub_m or not ses_m:
            continue
        sub = f"sub-{sub_m.group(1)}"
        ses = f"ses-{ses_m.group(1)}"

        with open(jf) as f:
            data = json.load(f)

        dt_str = data.get("AcquisitionDateTime")
        if not dt_str:
            t_str = data.get("AcquisitionTime")
            dt_str = None
        else:
            dt_str = dt_str.strip()

        dt = None
        if dt_str:
            try:
                dt = datetime.fromisoformat(dt_str)
            except ValueError:
                dt = None

        records[(sub, ses)] = dt

    subs = sorted({s for s, _ in records}, key=lambda x: int(x.split("-")[1]))

    lines = []
    header = "subject_id\tacq_time_ses-1\tacq_time_ses-2\tacq_time_ses-3\tdiff_ses1_ses2_hh:mm\tdiff_ses1_ses2_min"
    lines.append(header)

    for sub in subs:
        dt1 = records.get((sub, "ses-1"))
        dt2 = records.get((sub, "ses-2"))
        dt3 = records.get((sub, "ses-3"))

        t1_str = dt1.time().isoformat(timespec="seconds") if dt1 else "n/a"
        t2_str = dt2.time().isoformat(timespec="seconds") if dt2 else "n/a"
        t3_str = dt3.time().isoformat(timespec="seconds") if dt3 else "n/a"

        if dt1 and dt2:
            diff_min = time_of_day_diff_minutes(dt1.time(), dt2.time())
            total_rounded = int(round(diff_min))
            h, m = divmod(total_rounded, 60)
            diff_hhmm = f"{h:02d}:{m:02d}"
            diff_min_str = f"{diff_min:.1f}"
        else:
            diff_hhmm = "n/a"
            diff_min_str = "n/a"

        lines.append(f"{sub}\t{t1_str}\t{t2_str}\t{t3_str}\t{diff_hhmm}\t{diff_min_str}")

    OUTFILE.write_text("\n".join(lines) + "\n")
    print(f"Wrote {len(subs)} subjects to {OUTFILE}")


if __name__ == "__main__":
    main()
