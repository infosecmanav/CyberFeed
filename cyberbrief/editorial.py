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


def pack(report):
    stories = []
    for index, source in enumerate(report['stories'], 1):
        ident = f's{index}'
        evidence = []
        fields = source.get('details', {}).get('fields', {})
        for field, budget in BUDGETS.items():
            text = source.get('summary', '') if field == 'summary' else fields.get(field, {}).get('text', '')
            if text:
                if field == 'summary':
                    sentences = re.split(r'(?<=[.!?])\s+', text)
                    focused = [sentence for sentence in sentences if re.search(r'may allow|could allow|successful exploitation|actively exploited|exploited in the wild', sentence, re.I)]
                    if focused:
                        text = ' '.join(focused)
                words = text.split()
                evidence.append({'id': ident+'.'+field, 'text': ' '.join(words[:budget]), 'truncated': len(words)>budget or fields.get(field, {}).get('truncated', False)})
        if source.get('known_exploited_cves'):
            evidence.append({'id': ident+'.kev', 'text': 'CISA KEV matched CVEs: '+', '.join(source['known_exploited_cves']), 'truncated': False})
        stories.append({'id': ident, 'title': source['title'], 'source': source['source'], 'published': source['published'], 'url': source.get('details', {}).get('url',source['url']), 'evidence': evidence})
    return {'period': report['period'], 'generated': report['generated'], 'demo': report.get('demo',False), 'window_start':report.get('window_start'), 'window_end':report.get('window_end'), 'errors':report['errors'], 'stories': stories}


def validate(payload, summary):
    if not isinstance(summary, dict) or not isinstance(summary.get('stories'), list):
        raise ValueError('Expected a stories array')
    if [s.get('id') for s in summary['stories']] != [s['id'] for s in payload['stories']]:
        raise ValueError('Story IDs must match input order exactly')
    for source, story in zip(payload['stories'],summary['stories']):
        title = story.get('title')
        if not isinstance(title,str) or not title.strip() or len(title)>140:
            raise ValueError('Title must be nonempty and at most 140 characters')
        bullets = story.get('bullets')
        if not isinstance(bullets,list) or not 3<=len(bullets)<=5:
            raise ValueError('Each story requires 3–5 bullets')
        evidence = {e['id']:e['text'] for e in source['evidence']}
        for bullet in bullets:
            if bullet.get('kind') not in ('what','why','affected','action','exploitation'):
                raise ValueError('Invalid bullet kind')
            text,quote = bullet.get('text'),bullet.get('quote')
            if not isinstance(text,str) or not text.strip() or not isinstance(quote,str) or not quote.strip():
                raise ValueError('Bullets require text and exact evidence quotations')
            if bullet.get('evidence_id') not in evidence or quote not in evidence[bullet['evidence_id']]:
                raise ValueError('Evidence ID or quotation does not match the input')
        if len((' '.join([title]+[b['text'] for b in bullets])).split())>120:
            raise ValueError('Story exceeds 120-word editorial limit')
        missing = story.get('missing',[])
        if not isinstance(missing,list) or any(m not in ('versions','remediation','exploitation','workaround') for m in missing):
            raise ValueError('Invalid missing-field list')
    def cited(item, evidence):
        text, quote = item.get('text'), item.get('quote')
        if not isinstance(text, str) or not text.strip() or not isinstance(quote, str) or not quote.strip():
            raise ValueError('Editorial entries require text and exact quotations')
        if item.get('evidence_id') not in evidence or quote not in evidence[item['evidence_id']]:
            raise ValueError('Editorial citation or quotation does not match input')
    sources = {source['id']: source for source in payload['stories']}
    overview = summary.get('overview')
    if not isinstance(overview, list) or not 1 <= len(overview) <= 5:
        raise ValueError('AI summary requires 1–5 overview bullets')
    for item in overview:
        if item.get('story_id') not in sources:
            raise ValueError('Overview must reference a supplied story')
        cited(item, {e['id']: e['text'] for e in sources[item['story_id']]['evidence']})
        if len(item['text'].split()) > 40:
            raise ValueError('Overview bullet exceeds 40 words')
    for source, story in zip(payload['stories'], summary['stories']):
        details = story.get('details')
        if not isinstance(details, list) or not 3 <= len(details) <= 6:
            raise ValueError('Detailed report requires 3–6 supported entries per story')
        for item in details:
            if item.get('section') not in ('context', 'affected', 'action', 'exploitation'):
                raise ValueError('Invalid detailed report section')
            cited(item, {e['id']: e['text'] for e in source['evidence']})
        if len(' '.join(item['text'] for item in details).split()) > 220:
            raise ValueError('Detailed report exceeds 220 words per story')
    return summary


def render(payload, summary, theme='dark'):
    validate(payload,summary)
    if theme not in ('dark','light'): raise ValueError('Choose dark or light')
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    import reportlab
    font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font.exists():font=Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'
    if 'SignalPreview' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SignalPreview',str(font)))
    dark=theme=='dark';background=colors.HexColor('#070b12' if dark else '#ffffff');card=colors.HexColor('#101c2d' if dark else '#f1f5f9');ink=colors.HexColor('#eef4fa' if dark else '#152337');muted=colors.HexColor('#abb9ca' if dark else '#536579');accent=colors.HexColor('#60a5fa' if dark else '#1d4ed8')
    def style(name,size=9,color=ink,leading=13):return ParagraphStyle(name,fontName='SignalPreview',fontSize=size,textColor=color,leading=leading,spaceAfter=6)
    body=style('body');heading=style('heading',13,ink,17);meta=style('meta',8,muted,12);title=style('title',28,ink,34)
    out=BytesIO();doc=SimpleDocTemplate(out,leftMargin=36,rightMargin=36,topMargin=42,bottomMargin=42)
    flow=[Paragraph('SIGNAL / CYBER BRIEFING',meta),Paragraph('Less noise. Clear next steps.',title),Paragraph('DESIGN PREVIEW · Agent-edited sample; automated AI delivery is not connected.',style('notice',9,accent,14)),Paragraph(f"{escape(payload['period'].title())} · {len(summary['stories'])} selected updates · {escape(datetime.fromisoformat(payload['generated']).astimezone(ZoneInfo('America/Toronto')).strftime('%b %d, %Y'))}",meta)]
    if payload.get('demo'):flow.append(Paragraph('FICTIONAL DEMO — not real incidents.',body))
    flow.append(Paragraph('01 / AI summary', heading))
    source_numbers = {source['id']: index for index, source in enumerate(payload['stories'], 1)}
    for item in summary['overview']:
        flow.append(Paragraph('• '+escape(item['text'])+f" [{source_numbers[item['story_id']]}]",body))
    flow.extend([Spacer(1,16),Paragraph('02 / Detailed AI-edited report',heading)])
    labels={'context':'WHAT HAPPENED / IMPACT','affected':'AFFECTED PRODUCTS & VERSIONS','action':'PATCHES & RECOMMENDED ACTION','exploitation':'EXPLOITATION STATUS'}
    for index,(source,story) in enumerate(zip(payload['stories'],summary['stories']),1):
        content=[Paragraph(f"{index:02d} / {escape(story['title'])}",heading),Paragraph(escape(source['source']+' · '+source['published'][:10])+f' · Reference [{index}]',meta)]
        last_section = None
        for item in story['details']:
            label = f'<font color="{accent.hexval()}">{labels[item["section"]]}</font><br/>' if item['section'] != last_section else ''
            content.append(Paragraph(label+escape(item['text'])+f' [{index}]',body))
            last_section = item['section']
        if story.get('missing'):content.append(Paragraph('Not supplied: '+escape(', '.join(story['missing']))+'. Check the complete source before acting.',meta))
        cell=Table([[content]],colWidths=[doc.width]);cell.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),card),('LEFTPADDING',(0,0),(-1,-1),16),('RIGHTPADDING',(0,0),(-1,-1),16),('TOPPADDING',(0,0),(-1,-1),14),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
        flow.extend([KeepTogether([cell]),Spacer(1,14)])
    flow.extend([Spacer(1,10),Paragraph('03 / References',heading)])
    from urllib.parse import urlparse
    for index,source in enumerate(payload['stories'],1):
        flow.append(Paragraph(f"[{index}] {escape(source['source'])} · {escape(source['published'][:10])}<br/>{escape(source['title'])}",body))
        url=source['url']
        if urlparse(url).scheme=='https':
            flow.append(Paragraph(f'<link href={quoteattr(url)} color="{accent.hexval()}">{escape(url)}</link>',meta))
    if payload['errors']:
        flow.append(Paragraph('Coverage gaps',heading))
        for error in payload['errors']:flow.append(Paragraph(escape(error['source']+': '+error['error']),body))
    flow.append(Paragraph('Evidence quotations are retained in the editorial JSON. Human review is required: exact citation matches alone cannot verify a paraphrase.',meta))
    def page(canvas,document):
        canvas.setFillColor(background);canvas.rect(0,0,*document.pagesize,fill=1,stroke=0);canvas.setFont('SignalPreview',8);canvas.setFillColor(muted);canvas.drawString(36,22,'SIGNAL · Editorial design preview');canvas.drawRightString(document.pagesize[0]-36,22,str(document.page))
    doc.build(flow,onFirstPage=page,onLaterPages=page)
    return out.getvalue()


def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['pack','render']);p.add_argument('--input',required=True);p.add_argument('--output',required=True);p.add_argument('--summary');p.add_argument('--theme',choices=['dark','light'],default='dark');a=p.parse_args()
    data=json.loads(Path(a.input).read_text(encoding='utf-8'));out=Path(a.output)
    if a.command=='pack':out.write_text(json.dumps(pack(data),ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    else:
        if not a.summary:p.error('render requires --summary')
        out.write_bytes(render(data,json.loads(Path(a.summary).read_text(encoding='utf-8')),a.theme))


if __name__=='__main__':main()
