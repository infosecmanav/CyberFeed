# Editorial design preview

These three historical source-backed stories demonstrate a proposed layout. Summaries were prepared during this review, without calling an external AI API. They are not a newly fetched daily report. Production PDF rendering and scheduled delivery have not changed.

- [Dark PDF](dark.pdf)
- [Light PDF](light.pdf)
- [First-page image](dark-preview.png)
- [Compact evidence](evidence.json)
- [Summary with evidence quotations](summary.json)
- [Portable agent instructions](../../SKILLS.md)

Use GitHub's download/raw button on a PDF to save it. Review the wording, readability, colors, and information density before approving production integration. Choose an agent/model separately; the instructions work with any capable provider but do not guarantee free availability or automatically call one.

```bash
python -m cyberbrief.editorial pack --input site/briefing.json --output evidence.json
# Supply SKILLS.md and evidence.json to the agent; save its JSON as summary.json.
python -m cyberbrief.editorial render --input evidence.json --summary summary.json --output preview.pdf --theme dark
```

Exact quotation checks prevent fabricated citations but cannot prove that a paraphrase follows from the source. Human review remains required. Compact inputs are excerpts, so complete version tables must remain available through the original source and existing detailed report. This prototype is deliberately not connected to Discord delivery.
