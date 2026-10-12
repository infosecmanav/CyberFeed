"""Discord text and PDF reports from the same attributed briefing data."""
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape, quoteattr


def sections(data):
    yield f"Signal — {data['period'].title()} cyber briefing"
    yield f"Generated: {data['generated']}"
    if data.get('demo'):
        yield "DEMO — fictional examples, not real incidents."
    yield f"{len(data['stories'])} updates · Source excerpts; follow the evidence before acting."
    for group in data.get('weekly_digest', []):
        yield f"Weekly overview: {group['topic']}"
        for update in group['updates']:
            yield f"• {update['title']} — {update['source']}"
    if not data['stories']:
        yield "No updates in this time window. This does not establish that no incidents occurred."
    for index, story in enumerate(data['stories'], 1):
        yield f"{index}. {story['title']}"
        yield f"{story['source']} · {story['published']}"
        yield story['summary'] or "No excerpt available; read the original source."
        if story.get('kev'):
            yield "Listed in CISA KEV (known exploited vulnerability)."
        elif not story.get('kev_available', True):
            yield "KEV catalog unavailable; exploitation membership unknown."
        yield story['url']
        for evidence in story.get('evidence', []):
            if evidence.get('url') and evidence['url'] != story['url']:
                yield f"Additional evidence: {evidence.get('source', 'Source')} — {evidence['url']}"
    if data['errors']:
        yield "Coverage warnings"
        for error in data['errors']:
            yield f"{error['source']}: {error['error']}"


def text_messages(data, limit=1900):
    """Escape source-controlled Discord markdown and respect message limits."""
    import discord
    text = '\n\n'.join(discord.utils.escape_mentions(discord.utils.escape_markdown(line)) for line in sections(data))
    messages = []
    while text:
        cut = len(text) if len(text) <= limit else text.rfind('\n', 0, limit + 1)
        if cut <= 0:
            cut = limit
        messages.append(text[:cut])
        text = text[cut:].lstrip('\n')
    return messages


def pdf_report(data):
    """Build a paginated PDF entirely in memory with embedded fonts."""
    import reportlab
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font_path.exists():
        font_path = Path(reportlab.__file__).parent / 'fonts' / 'Vera.ttf'
    if 'Signal' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('Signal', str(font_path)))
    styles = getSampleStyleSheet()
    for name in ('Title', 'BodyText'):
        styles[name].fontName = 'Signal'
        styles[name].wordWrap = 'CJK'
    styles['BodyText'].leading = 15
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, title=f"Signal {data['period']} briefing", leftMargin=.65*inch, rightMargin=.65*inch)
    flow = []
    for index, line in enumerate(sections(data)):
        content = escape(line)
        if urlparse(line).scheme in ('https', 'http'):
            content = f'<link href={quoteattr(line)} color="blue">{content}</link>'
        flow.extend([Paragraph(content, styles['Title' if index == 0 else 'BodyText']), Spacer(1, 6)])
    document.build(flow)
    return buffer.getvalue()
