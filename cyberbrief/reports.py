"""Discord text and PDF reports from the same attributed briefing data."""
import re
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
    if data.get('window_start'):
        yield f"Coverage window: {data['window_start']} to {data['window_end']}"
    if data.get('selection'):
        yield data['selection']
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
        if story.get('cves'):
            yield 'CVEs: ' + ', '.join(story['cves'])
        labels = {'affected': 'Affected products / versions', 'remediation': 'Patches / recommended action', 'workarounds': 'Workarounds / temporary mitigation', 'exploitation': 'Source exploitation statement'}
        detail = story.get('details', {})
        if detail.get('updated_at'):
            yield 'Source revision: ' + detail['updated_at'][:10] + ' (date precision; publication date retained above)'
        for field, label in labels.items():
            finding = detail.get('fields', {}).get(field)
            yield label
            yield (finding['text'] + (' [Excerpt truncated; follow the source for the complete section.]' if finding.get('truncated') else '')) if finding else 'Not provided in the retrieved source. Check the original advisory.'
        if detail.get('error'):
            yield detail['error']
        if story.get('kev'):
            yield 'CISA KEV matched CVEs: ' + ', '.join(story.get('known_exploited_cves', story.get('cves', [])))
        elif not story.get('kev_available', True):
            yield "KEV catalog unavailable; exploitation membership unknown."
        yield story['url']
        if detail.get('url') and detail['url'] != story['url']:
            yield detail['url']
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
    from reportlab.lib import colors
    font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font_path.exists():
        font_path = Path(reportlab.__file__).parent / 'fonts' / 'Vera.ttf'
    if 'Signal' not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont('Signal', str(font_path)))
    styles = getSampleStyleSheet()
    for name in ('Title', 'BodyText', 'Heading1', 'Heading2'):
        styles[name].fontName = 'Signal'
        styles[name].wordWrap = 'CJK'
    styles['BodyText'].leading = 15
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, title=f"Signal {data['period']} briefing", leftMargin=.65*inch, rightMargin=.65*inch)
    styles['Heading1'].textColor = colors.HexColor('#164b63')
    styles['Heading1'].spaceBefore = 14
    styles['Heading1'].keepWithNext = True
    styles['Heading2'].spaceBefore = 9
    styles['Heading2'].keepWithNext = True
    styles['BodyText'].fontSize = 9
    flow = []
    labels = {'Affected products / versions', 'Patches / recommended action', 'Workarounds / temporary mitigation', 'Source exploitation statement', 'Coverage warnings'}
    for index, line in enumerate(sections(data)):
        content = escape(line)
        if urlparse(line).scheme in ('https', 'http'):
            content = f'<link href={quoteattr(line)} color="blue">{content}</link>'
        style = 'Title' if index == 0 else 'Heading1' if re.match(r'^\d+\. ', line) or line.startswith('Weekly overview:') else 'Heading2' if line in labels else 'BodyText'
        flow.append(Paragraph(content, styles[style]))
        if style in ('BodyText', 'Title'):
            flow.append(Spacer(1, 6))
    def footer(canvas, doc):
        canvas.setFont('Signal', 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(.65*inch, .35*inch, 'Signal | Source-attributed cyber briefing')
        canvas.drawRightString(doc.pagesize[0]-.65*inch, .35*inch, f'Page {doc.page}')
    document.build(flow, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
