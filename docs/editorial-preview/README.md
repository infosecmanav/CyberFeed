# Editorial design preview

These ten historical source-backed updates demonstrate the black-and-blue layout. Detailed sections retain all supplied technical facts without a fixed word limit. Summary bullets remain short. Each update starts on a new page and has its own a. Summary, b. Detailed Reports (powered by AI), and c. References. There is no shared summary or shared reference section. Diagrams are optional; none is forced into this sample. Sparse extracts are explicitly marked as incomplete rather than padded with unsupported claims. Summaries were prepared during this review, without calling an external AI API. They are not a newly fetched daily report. The standard AI generation path now uses this layout. These files remain historical review samples.

- [Dark PDF](dark.pdf)
- [Light PDF](light.pdf)
- [First-page image](dark-preview.png)
- [Full supplied evidence](evidence.json)
- [Summary with evidence quotations](summary.json)
- [Portable agent instructions](../../SKILL.md)

Use GitHub's download/raw button on a PDF to save it. Review the wording, readability, colors, and information density before approving production integration. Configure your API provider and model in the application; the standalone instructions can also be used manually and do not guarantee free availability.

```bash
python -m cyberbrief.editorial pack --input site/briefing.json --output evidence.json
# Supply SKILL.md and evidence.json to the agent; save its JSON as summary.json.
python -m cyberbrief.editorial render --input evidence.json --summary summary.json --output preview.pdf --theme dark
```

Exact quotation checks prevent fabricated citations but cannot prove that a paraphrase follows from the source. Human review remains required. The supplied inputs may still be excerpts, so complete version tables must remain available through the original source and existing detailed report. Scheduled and interactive delivery use the validated editorial output; this directory preserves the original review samples.
