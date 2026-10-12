# whatsapp-conv-analysis

A Claude skill for evidence-based, longitudinal analysis of WhatsApp (or similar) chat exports in any language.

It produces a coverage map, tentative per-speaker profiles, relationship and reciprocity, turning points with before/after comparison, and a 5-domain score (reliability, friendship, sacrifice, support, caring), with verbatim quotes and message ids.

## Layout

- `whatsapp-conv-analysis/SKILL.md`: workflow and evidence rules
- `whatsapp-conv-analysis/references/`: scoring anchors, analysis guides, report structure
- `whatsapp-conv-analysis/scripts/parse_whatsapp.py`: export parser (Android and iOS, stable message ids, monthly chunks)

## Install

Zip the `whatsapp-conv-analysis` folder as `whatsapp-conv-analysis.skill` and add it as a skill in Claude, or copy the folder into your skills directory.

## Parser

```
python3 -I whatsapp-conv-analysis/scripts/parse_whatsapp.py EXPORT.txt --out OUTDIR
```

No chat data is included in this repository.

## License

MIT, see `LICENSE`.
