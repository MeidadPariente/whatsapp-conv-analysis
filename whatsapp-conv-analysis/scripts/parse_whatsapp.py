#!/usr/bin/env python3
"""Parse WhatsApp chat exports (.txt) in any locale into stable, auditable files.

Usage:
  python parse_whatsapp.py EXPORT.txt [EXPORT2.txt ...] --out OUTDIR [--date-order auto|DMY|MDY|YMD] [--overlap 15]

Writes to OUTDIR:
  messages.jsonl   one record per message: id, ts, date, time, speaker, text, kind, src_file, src_line
  coverage.json    counts by month and speaker, date range, parse warnings, date-order decision
  chunks/YYYY-MM.md  readable monthly chunks with message ids (plus context-only overlap from the previous month)

Design notes:
- Handles Android ("12/03/2021, 14:05 - Name: text") and iOS ("[12/03/2021, 14:05:33] Name: text") layouts,
  12h and 24h clocks, 2 or 4 digit years, ./-// separators, and invisible direction marks (LRM/RLM, NNBSP).
- Lines that do not start with a timestamp are continuations of the previous message (multi-line messages).
- Lines with a timestamp but no "Speaker: " part are kind=system. iOS notices (body starting with an invisible LRM) are also kind=system. Media placeholders are kind=media_omitted.
- Merging several files drops only overlap duplicates: for each identical (ts, speaker, text) key the highest
  per-file count is kept, so genuine repeated messages survive.
- Nothing is guessed silently: ambiguous day/month order and unparseable lines are reported in coverage.json.
"""
import argparse, json, os, re, sys
from collections import Counter, defaultdict
from datetime import datetime

INVIS = dict.fromkeys(map(ord, "‎‏‪‫‬‭‮⁦⁧⁨⁩﻿"), None)

HEADER = re.compile(
    r"^\[?(\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}),?\s+"      # date
    r"(\d{1,2}[:.]\d{2}(?::\d{2})?)"                       # time
    r"(?:\s?([AaPp]\.?\s?[Mm]\.?))?"                       # optional AM/PM
    r"\]?\s*(?:[-–—]\s+)?(.*)$"                  # rest (after optional dash)
)
MEDIA_HINTS = re.compile(
    r"(<[^<>]{2,40}>|image omitted|video omitted|audio omitted|sticker omitted|GIF omitted|"
    r"document omitted|media omitted|\(file attached\)|\.(jpg|jpeg|png|webp|opus|mp4|pdf|vcf)\b)",
    re.I)


def clean(s):
    return s.translate(INVIS).replace(" ", " ").replace(" ", " ")


def is_notice(raw_rest):
    """iOS exports prefix system notices (encryption banner, security code changes, deleted messages,
    omitted media) with an invisible LRM right after "Speaker: ". Real typed text does not start with it."""
    return raw_rest.startswith("\u200e")


def parse_file(path):
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1255", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except Exception:
            continue
    recs, bad = [], []
    cur = None
    for i, line in enumerate(text.splitlines(), 1):
        c = clean(line)
        raw_clean = line.translate({k: None for k in range(0x202a, 0x202f)}).replace("\u202f", " ").replace("\u00a0", " ").lstrip("\ufeff")
        m = HEADER.match(c)
        if m:
            date_s, time_s, ampm, rest = m.groups()
            speaker, body, kind = None, rest, "system"
            if ": " in rest:
                sp, body = rest.split(": ", 1)
                if 0 < len(sp) <= 60:
                    speaker, kind = sp.strip(), "message"
                else:
                    body = rest
            if kind == "message" and MEDIA_HINTS.search(body) and len(body) < 80:
                kind = "media_omitted"
            elif kind == "message" and ": " in raw_clean:
                raw_body = raw_clean.split(": ", 1)[1] if ": " in raw_clean else ""
                # locate the body after the header's "Speaker: " in the raw line
                idx = raw_clean.find(speaker + ": ")
                if idx >= 0 and raw_clean[idx + len(speaker) + 2:].startswith("\u200e"):
                    kind = "system"
            cur = dict(date_s=date_s, time_s=time_s, ampm=ampm, speaker=speaker, text=body,
                       kind=kind, src_file=os.path.basename(path), src_line=i)
            recs.append(cur)
        elif cur is not None:
            cur["text"] += "\n" + c
        else:
            if c.strip():
                bad.append({"file": os.path.basename(path), "line": i, "text": c[:80]})
    return recs, bad


def decide_order(recs, forced):
    if forced != "auto":
        return forced, False
    a_gt12 = b_gt12 = False
    for r in recs:
        parts = re.split(r"[./\-]", r["date_s"])
        if len(parts[0]) == 4:
            return "YMD", False
        x, y = int(parts[0]), int(parts[1])
        a_gt12 |= x > 12
        b_gt12 |= y > 12
    if a_gt12 and not b_gt12:
        return "DMY", False
    if b_gt12 and not a_gt12:
        return "MDY", False
    if a_gt12 and b_gt12:
        return "MIXED", True
    return "DMY", True  # ambiguous: default DMY and flag it


def to_ts(r, order):
    p = [int(x) for x in re.split(r"[./\-]", r["date_s"])]
    if order == "YMD":
        y, mo, d = p
    elif order == "MDY":
        mo, d, y = p
    else:
        d, mo, y = p
    if y < 100:
        y += 2000
    t = [int(x) for x in re.split(r"[:.]", r["time_s"])]
    h, mi = t[0], t[1]
    s = t[2] if len(t) > 2 else 0
    if r["ampm"]:
        pm = r["ampm"].lower().startswith("p")
        h = (h % 12) + (12 if pm else 0)
    return datetime(y, mo, d, h, mi, s)


def merge(files_recs):
    merged = list(files_recs[0])
    for recs in files_recs[1:]:
        have = Counter((r["ts"], r["speaker"], r["text"]) for r in merged)
        seen = Counter()
        for r in recs:
            k = (r["ts"], r["speaker"], r["text"])
            seen[k] += 1
            if seen[k] > have[k]:
                merged.append(r)
    return merged


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date-order", default="auto", choices=["auto", "DMY", "MDY", "YMD"])
    ap.add_argument("--overlap", type=int, default=15, help="context messages carried from the previous month")
    a = ap.parse_args()
    os.makedirs(os.path.join(a.out, "chunks"), exist_ok=True)

    all_raw, bad_all = [], []
    for f in a.files:
        r, b = parse_file(f)
        all_raw.append(r)
        bad_all += b
    order, ambiguous = decide_order([r for rs in all_raw for r in rs], a.date_order)
    if order == "MIXED":
        print("ERROR: files disagree on date order; parse them separately with --date-order.", file=sys.stderr)
        sys.exit(2)
    for rs in all_raw:
        for r in rs:
            r["ts"] = to_ts(r, order).isoformat(sep=" ")
    msgs = merge(all_raw)
    msgs.sort(key=lambda r: r["ts"])  # stable: keeps file order within the same second
    for n, r in enumerate(msgs, 1):
        r["id"] = f"M{n:06d}"
        r["date"], r["time"] = r["ts"].split(" ")

    with open(os.path.join(a.out, "messages.jsonl"), "w", encoding="utf-8") as fh:
        for r in msgs:
            fh.write(json.dumps({k: r[k] for k in ("id", "ts", "date", "time", "speaker", "text", "kind", "src_file", "src_line")},
                                ensure_ascii=False) + "\n")

    by_month = defaultdict(list)
    for r in msgs:
        by_month[r["ts"][:7]].append(r)
    cov = {
        "files": [os.path.basename(f) for f in a.files],
        "date_order": order, "date_order_ambiguous": ambiguous,
        "total_records": len(msgs),
        "by_kind": dict(Counter(r["kind"] for r in msgs)),
        "speakers": dict(Counter(r["speaker"] for r in msgs if r["kind"] != "system")),
        "first_ts": msgs[0]["ts"] if msgs else None, "last_ts": msgs[-1]["ts"] if msgs else None,
        "months": {m: dict(Counter(r["speaker"] for r in rs if r["kind"] != "system")) for m, rs in sorted(by_month.items())},
        "unparsed_leading_lines": bad_all[:50],
        "warnings": [],
    }
    if ambiguous:
        cov["warnings"].append("Day/month order could not be proven from the data; DMY assumed. Confirm with the user.")
    if len(cov["speakers"]) > 2:
        cov["warnings"].append("More than two speakers: likely a group chat. Pair analysis needs identifiable addressees.")
    # Gaps: calendar months with zero records between first and last
    if msgs:
        y0, m0 = map(int, cov["first_ts"][:7].split("-"))
        y1, m1 = map(int, cov["last_ts"][:7].split("-"))
        gaps, y, m = [], y0, m0
        while (y, m) <= (y1, m1):
            k = f"{y:04d}-{m:02d}"
            if k not in by_month:
                gaps.append(k)
            m += 1
            if m > 12:
                y, m = y + 1, 1
        cov["empty_months_in_range"] = gaps
    json.dump(cov, open(os.path.join(a.out, "coverage.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    prev = []
    for mkey, rs in sorted(by_month.items()):
        with open(os.path.join(a.out, "chunks", f"{mkey}.md"), "w", encoding="utf-8") as fh:
            fh.write(f"# Chunk {mkey}: {len(rs)} records, ids {rs[0]['id']}..{rs[-1]['id']}\n\n")
            if prev and a.overlap:
                fh.write("## CONTEXT ONLY (previous month tail, do not count)\n")
                for r in prev[-a.overlap:]:
                    fh.write(f"[{r['id']}] {r['date']} {r['time']} | {r['speaker'] or '(system)'}: {r['text']}\n")
                fh.write("\n## CHUNK BODY\n")
            for r in rs:
                fh.write(f"[{r['id']}] {r['date']} {r['time']} | {r['speaker'] or '(system)'}: {r['text']}\n")
        prev = rs
    print(json.dumps({"records": len(msgs), "date_order": order, "ambiguous": ambiguous,
                      "speakers": cov["speakers"], "range": [cov["first_ts"], cov["last_ts"]],
                      "chunks": len(by_month), "warnings": cov["warnings"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
