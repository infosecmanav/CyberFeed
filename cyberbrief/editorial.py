"""Standalone editorial input/validation and design preview; not production delivery."""
import argparse
import re
from datetime import datetime
from zoneinfo import ZoneInfo
import json
from pathlib import Path
from io import BytesIO
from xml.sax.saxutils import escape, quoteattr

BUDGETS = {'summary': 60, 'affected': 55, 'remediation': 85, 'workarounds': 45, 'exploitation': 40}


def pack(report, detail_level='full'):
    if detail_level not in ('full','compact'):
        raise ValueError('Choose full or compact evidence')
    stories = []
    for index, source in enumerate(report['stories'], 1):
        ident = f's{index}'
        evidence = []
        fields = source.get('details', {}).get('fields', {})
        for field, budget in BUDGETS.items():
            text = source.get('summary', '') if field == 'summary' else fields.get(field, {}).get('text', '')
            if text:
                if field == 'summary' and detail_level == 'compact':
                    sentences = re.split(r'(?<=[.!?])\s+', text)
                    focused = [sentence for sentence in sentences if sentence.endswith(('.', '!', '?')) and re.search(r'may allow|could allow|successful exploitation|actively exploited|exploited in the wild', sentence, re.I)]
                    if focused:
                        text = ' '.join(focused)
                words = text.split()
                evidence.append({'id': ident+'.'+field, 'text': text if detail_level == 'full' else ' '.join(words[:budget]), 'truncated': (detail_level == 'compact' and len(words)>budget) or fields.get(field, {}).get('truncated', False)})
        if source.get('known_exploited_cves'):
            evidence.append({'id': ident+'.kev', 'text': 'CISA KEV matched CVEs: '+', '.join(source['known_exploited_cves']), 'truncated': False})
        references = []
        for label,url in [(source['source'],source.get('details',{}).get('url',source['url'])),(source['source'],source['url'])]+[(e.get('source','Supporting source'),e['url']) for e in source.get('evidence',[]) if e.get('url')]:
            if url not in [r['url'] for r in references]:
                references.append({'source':label,'url':url,'published':source['published'] if url in (source['url'],source.get('details',{}).get('url')) else None})
        if source.get('known_exploited_cves'):
            references.append({'source':'CISA KEV official catalog mirror','url':'https://raw.githubusercontent.com/cisagov/kev-data/main/known_exploited_vulnerabilities.json'})
        stories.append({'references': references, 'id': ident, 'title': source['title'], 'source': source['source'], 'published': source['published'], 'url': source.get('details', {}).get('url',source['url']), 'evidence': evidence})
    return {'period': report['period'], 'generated': report['generated'], 'demo': report.get('demo',False), 'window_start':report.get('window_start'), 'window_end':report.get('window_end'), 'errors':report['errors'], 'stories': stories}


def fact_tokens(text):
    return {match.upper() for match in re.findall(r'\bCVE-\d{4}-\d{4,7}\b|\b\d+(?:\.[0-9A-Za-z]+)+\b', text, re.I)}


class EditorialValidationError(ValueError):
    """A precise validation failure containing no model-generated text."""

    def __init__(self, code, path, message):
        super().__init__(f'{path}: {message}')
        self.issue = {'code': code, 'path': path, 'message': message}


def validate(payload, summary):
    def fail(code, path, message):
        raise EditorialValidationError(code, path, message)

    def object_entry(item, path):
        if not isinstance(item, dict):
            fail('invalid_object', path, 'Expected a JSON object')

    def cited(item, evidence, path, *, bullet=False):
        object_entry(item, path)
        text, quote = item.get('text'), item.get('quote')
        if not isinstance(text, str) or not text.strip() or not isinstance(quote, str) or not quote.strip():
            fail('missing_text_or_quote', path, 'Editorial entries require text and exact quotations')
        ident = item.get('evidence_id')
        if not isinstance(ident, str) or ident not in evidence:
            fail('unknown_evidence_id', path + '.evidence_id', 'Evidence ID must identify an evidence entry in this story')
        if quote not in evidence[ident]:
            fail('quotation_mismatch', path + '.quote', 'Evidence quotation must be an exact substring of the identified input text')
        if not fact_tokens(text).issubset(fact_tokens(quote)):
            message = 'Bullet contains CVEs or versions absent from its quotation' if bullet else 'Editorial text contains CVEs or versions absent from its quotation'
            fail('unsupported_fact_token', path + '.text', message)

    if not isinstance(summary, dict) or not isinstance(summary.get('stories'), list):
        fail('missing_stories', 'stories', 'Expected a stories array')
    for index, story in enumerate(summary['stories']):
        object_entry(story, f'stories[{index}]')
    if [s.get('id') for s in summary['stories']] != [s['id'] for s in payload['stories']]:
        fail('story_id_mismatch', 'stories', 'Story IDs must match input order exactly')
    for index, (source, story) in enumerate(zip(payload['stories'],summary['stories'])):
        path = f'stories[{index}]'
        title = story.get('title')
        if not isinstance(title,str) or not title.strip() or len(title)>140:
            fail('title_length', path + '.title', 'Title must be nonempty and at most 140 characters')
        bullets = story.get('bullets')
        if not isinstance(bullets,list) or not 1<=len(bullets)<=5:
            fail('bullet_count', path + '.bullets', 'Each story requires 1–5 supported bullets')
        evidence = {e['id']:e['text'] for e in source['evidence']}
        for item_index, bullet in enumerate(bullets):
            item_path = f'{path}.bullets[{item_index}]'
            object_entry(bullet, item_path)
            if bullet.get('kind') not in ('what','why','affected','action','exploitation'):
                fail('bullet_kind', item_path + '.kind', 'Invalid bullet kind')
            cited(bullet, evidence, item_path, bullet=True)
        allowed = fact_tokens(source['title']+' '+ ' '.join(e['text'] for e in source['evidence']))
        if not fact_tokens(title).issubset(allowed):
            fail('unsupported_title_token', path + '.title', 'Title contains unsupported CVEs or versions')
        if len((' '.join([title]+[b['text'] for b in bullets])).split())>120:
            fail('summary_word_limit', path + '.bullets', 'Story exceeds 120-word editorial limit')
        missing = story.get('missing',[])
        if not isinstance(missing,list) or any(m not in ('versions','remediation','exploitation','workaround') for m in missing):
            fail('missing_fields', path + '.missing', 'Invalid missing-field list')
        details = story.get('details')
        if not isinstance(details, list) or not 1 <= len(details) <= 20:
            fail('detail_count', path + '.details', 'Detailed report requires 1–20 supported entries per story')
        for item_index, item in enumerate(details):
            item_path = f'{path}.details[{item_index}]'
            object_entry(item, item_path)
            if item.get('section') not in ('context', 'affected', 'action', 'exploitation'):
                fail('detail_section', item_path + '.section', 'Invalid detailed report section')
            cited(item, evidence, item_path)
        diagram = story.get('diagram')
        if diagram is not None:
            if not isinstance(diagram, dict) or not isinstance(diagram.get('nodes'), list) or not 2 <= len(diagram['nodes']) <= 6:
                fail('diagram_nodes', path + '.diagram.nodes', 'Optional diagram requires 2–6 supported nodes')
            for item_index, node in enumerate(diagram['nodes']):
                object_entry(node, f'{path}.diagram.nodes[{item_index}]')
            node_ids = [node.get('id') for node in diagram['nodes']]
            if any(not isinstance(n, str) or not n for n in node_ids) or len(set(node_ids)) != len(node_ids):
                fail('diagram_node_ids', path + '.diagram.nodes', 'Diagram node IDs must be nonempty and unique')
            for item_index, node in enumerate(diagram['nodes']):
                item_path = f'{path}.diagram.nodes[{item_index}]'
                cited(node, evidence, item_path)
                if len(node['text'].split()) > 18:
                    fail('diagram_label_length', item_path + '.text', 'Diagram label exceeds 18 words')
            edges = diagram.get('edges')
            if not isinstance(edges, list) or not 1 <= len(edges) <= 8:
                fail('diagram_edges', path + '.diagram.edges', 'Diagram requires explicit supported relationships')
            for item_index, edge in enumerate(edges):
                item_path = f'{path}.diagram.edges[{item_index}]'
                object_entry(edge, item_path)
                if edge.get('from') not in node_ids or edge.get('to') not in node_ids or edge['from'] == edge['to']:
                    fail('diagram_edge_nodes', item_path, 'Diagram edge references invalid nodes')
                quote = edge.get('quote')
                ident = edge.get('evidence_id')
                if not isinstance(quote, str) or not quote or not isinstance(ident, str) or ident not in evidence or quote not in evidence[ident]:
                    fail('diagram_edge_evidence', item_path + '.quote', 'Diagram relationship requires matching evidence')
    return summary


def diagram_drawing(diagram, width, accent, ink, background):
    from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
    from reportlab.lib.utils import simpleSplit
    nodes=diagram['nodes'];height=len(nodes)*68
    drawing=Drawing(width,height)
    positions={node['id']:(width/2,height-52-index*68) for index,node in enumerate(nodes)}
    for edge in diagram['edges']:
        x,y=positions[edge['from']];tx,ty=positions[edge['to']]
        if ty<y:
            drawing.add(Line(x,y,tx,ty+40,strokeColor=accent,strokeWidth=1.2))
            drawing.add(Polygon([tx,ty+40,tx-4,ty+47,tx+4,ty+47],fillColor=accent,strokeColor=accent))
        else:
            # Route reverse/non-sequential relationships at the side.
            side=width-8
            drawing.add(Line(x+width/2-32,y+20,side,y+20,strokeColor=accent))
            drawing.add(Line(side,y+20,side,ty+20,strokeColor=accent))
            drawing.add(Line(side,ty+20,tx+width/2-32,ty+20,strokeColor=accent))
            drawing.add(Polygon([width-32,ty+20,width-25,ty+24,width-25,ty+16],fillColor=accent,strokeColor=accent))
    for node in nodes:
        x,y=positions[node['id']]
        drawing.add(Rect(32,y,width-64,40,rx=5,fillColor=background,strokeColor=accent,strokeWidth=.8))
        lines=simpleSplit(node['text'],'SignalPreview',8.5,width-86)
        for index,line in enumerate(lines[:2]):drawing.add(String(x,y+24-index*11,line,fontName='SignalPreview',fontSize=8.5,fillColor=ink,textAnchor='middle'))
    return drawing


def render(payload, summary, theme='dark', *, preview=True):
    validate(payload,summary)
    if theme not in ('dark','light'): raise ValueError('Choose dark or light')
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, PageBreak
    import reportlab
    font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font.exists():font=Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'
    if 'SignalPreview' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SignalPreview',str(font)))
    dark=theme=='dark';background=colors.HexColor('#070b12' if dark else '#ffffff');card=colors.HexColor('#101c2d' if dark else '#f1f5f9');ink=colors.HexColor('#eef4fa' if dark else '#152337');muted=colors.HexColor('#abb9ca' if dark else '#536579');accent=colors.HexColor('#60a5fa' if dark else '#1d4ed8')
    def style(name,size=9,color=ink,leading=13):return ParagraphStyle(name,fontName='SignalPreview',fontSize=size,textColor=color,leading=leading,spaceAfter=6)
    body=style('body');heading=style('heading',13,ink,17);meta=style('meta',8,muted,12);title=style('title',28,ink,34)
    heading.keepWithNext = True
    out=BytesIO();doc=SimpleDocTemplate(out,leftMargin=36,rightMargin=36,topMargin=42,bottomMargin=42)
    flow=[Paragraph('Cyber Feed',title),Paragraph('DESIGN PREVIEW · Historical sample' if preview else 'AI-edited report · Source-linked evidence',style('notice',9,accent,14)),Paragraph(f"{escape(payload['period'].title())} · {len(summary['stories'])} selected updates · {escape(datetime.fromisoformat(payload['generated']).astimezone(ZoneInfo('America/Toronto')).strftime('%b %d, %Y'))}",meta)]
    if not summary['stories']:flow.append(Paragraph('No updates in this coverage window.',body))
    if payload.get('window_start') and payload.get('window_end'):
        zone = ZoneInfo('America/Toronto')
        dates = [datetime.fromisoformat(payload[key]).astimezone(zone).strftime('%b %d, %Y %H:%M %Z') for key in ('window_start','window_end')]
        flow.append(Paragraph(escape('Coverage: '+dates[0]+' to '+dates[1]+' (end exclusive)'),meta))
    if payload.get('demo'):flow.append(Paragraph('FICTIONAL DEMO — not real incidents.',body))
    labels={'context':'WHAT HAPPENED / IMPACT','affected':'AFFECTED PRODUCTS & VERSIONS','action':'PATCHES & RECOMMENDED ACTION','exploitation':'EXPLOITATION STATUS'}
    for index,(source,story) in enumerate(zip(payload['stories'],summary['stories']),1):
        if index > 1:
            flow.append(PageBreak())
        content=[Paragraph(f"{index:02d} / {escape(story['title'])}",heading),Paragraph(escape(source['source']+' · '+source['published'][:10]),meta),Paragraph('a. AI summary',heading)]
        references = source.get('references') or [{'source':source['source'],'url':source['url']}]
        def citation(item):
            ref = next((i for i,r in enumerate(references,1) if 'KEV' in r['source']),1) if item['evidence_id'].endswith('.kev') else 1
            return f' [{index}.{ref}]'
        for bullet in story['bullets']:
            content.append(Paragraph('• '+escape(bullet['text'])+citation(bullet),body))
        content.append(Paragraph('b. Detailed AI-powered report',heading))
        last_section = None
        for item in story['details']:
            label = f'<font color="{accent.hexval()}">{labels[item["section"]]}</font><br/>' if item['section'] != last_section else ''
            content.append(Paragraph(label+escape(item['text'])+citation(item),body))
            last_section = item['section']
        if story.get('missing'):content.append(Paragraph('Not supplied: '+escape(', '.join(story['missing']))+'. Check the complete source before acting.',meta))
        if story.get('diagram'):
            content.append(Paragraph('Source-backed diagram (optional)',heading))
            content.append(diagram_drawing(story['diagram'],doc.width,accent,ink,card))
        content.append(Paragraph('c. References',heading))
        from urllib.parse import urlparse
        for number,reference in enumerate(references,1):
            url=reference['url']
            content.append(Paragraph(f"[{index}.{number}] {escape(reference['source'])} · {escape(reference['published'][:10] if reference.get('published') else 'Publication date not supplied')}",meta))
            if urlparse(url).scheme=='https':
                content.append(Paragraph(f'<link href={quoteattr(url)} color="{accent.hexval()}">{escape(url)}</link>',meta))
        # Keep each entry's summary/detail/references together in document order.
        # Individual paragraphs can paginate; long reports must not overflow.
        for paragraph in content:
            flow.append(paragraph)
        flow.append(Spacer(1,20))
    if payload['errors']:
        flow.append(Paragraph('Coverage gaps',heading))
        for error in payload['errors']:flow.append(Paragraph(escape(error['source']+': '+error['error']),body))
    flow.append(Paragraph('Evidence quotations are retained in the editorial JSON. Human review is required: exact citation matches alone cannot verify a paraphrase.',meta))
    def page(canvas,document):
        canvas.setFillColor(background);canvas.rect(0,0,*document.pagesize,fill=1,stroke=0);canvas.setFont('SignalPreview',8);canvas.setFillColor(muted);canvas.drawRightString(document.pagesize[0]-36,22,str(document.page))
    doc.build(flow,onFirstPage=page,onLaterPages=page)
    return out.getvalue()


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['pack','render']);p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--summary');p.add_argument('--detail-level',choices=['full','compact'],default='full');p.add_argument('--theme',choices=['dark','light'],default='dark');a=p.parse_args()
    data=json.loads(Path(a.input).read_text(encoding='utf-8'));out=Path(a.output)
    if a.command=='pack':out.write_text(json.dumps(pack(data,a.detail_level),ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    else:
        if not a.summary:p.error('render requires --summary')
        out.write_bytes(render(data,json.loads(Path(a.summary).read_text(encoding='utf-8')),a.theme))


if __name__=='__main__':main()
