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
    return summary


def render(payload, summary, theme='dark'):
    validate(payload,summary)
    if theme not in ('dark','light'): raise ValueError('Choose dark or light')
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether
    import reportlab
    font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not font.exists():font=Path(reportlab.__file__).parent/'fonts'/'Vera.ttf'
    if 'SignalPreview' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('SignalPreview',str(font)))
    dark=theme=='dark';background=colors.HexColor('#10151d' if dark else '#ffffff');card=colors.HexColor('#1b2531' if dark else '#f1f5f9');ink=colors.HexColor('#eef4fa' if dark else '#152337');muted=colors.HexColor('#abb9ca' if dark else '#536579');accent=colors.HexColor('#65e0c2' if dark else '#096a80')
    def style(name,size=9,color=ink,leading=13):return ParagraphStyle(name,fontName='SignalPreview',fontSize=size,textColor=color,leading=leading,spaceAfter=6)
    body=style('body');heading=style('heading',13,ink,17);meta=style('meta',8,muted,12);title=style('title',28,ink,34)
    out=BytesIO();doc=SimpleDocTemplate(out,leftMargin=36,rightMargin=36,topMargin=42,bottomMargin=42)
    flow=[Paragraph('SIGNAL / CYBER BRIEFING',meta),Paragraph('Less noise. Clear next steps.',title),Paragraph('DESIGN PREVIEW · Editorial sample; scheduled reports are unchanged.',style('notice',9,accent,14)),Paragraph(f"{escape(payload['period'].title())} · {len(summary['stories'])} selected updates · {escape(datetime.fromisoformat(payload['generated']).astimezone(ZoneInfo('America/Toronto')).strftime('%b %d, %Y'))}",meta)]
    if payload.get('demo'):flow.append(Paragraph('FICTIONAL DEMO — not real incidents.',body))
    labels={'what':'WHAT CHANGED','why':'WHY IT MATTERS','affected':'WHO IS AFFECTED','action':'WHAT TO DO','exploitation':'EXPLOITATION'}
    for index,(source,story) in enumerate(zip(payload['stories'],summary['stories']),1):
        content=[Paragraph(f"{index:02d} / {escape(story['title'])}",heading),Paragraph(escape(source['source']+' · '+source['published'][:10]),meta)]
        for b in story['bullets']:
            content.append(Paragraph(f'<font color="{accent.hexval()}">{labels[b["kind"]]}</font><br/>{escape(b["text"])}',body))
        if story.get('missing'):content.append(Paragraph('Not supplied: '+escape(', '.join(story['missing']))+'. Check the complete source before acting.',meta))
        from urllib.parse import urlparse
        url=source['url']
        if urlparse(url).scheme=='https':content.append(Paragraph(f'<link href={quoteattr(url)} color="{accent.hexval()}">Read original source ↗</link>',body))
        cell=Table([[content]],colWidths=[doc.width]);cell.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),card),('LEFTPADDING',(0,0),(-1,-1),16),('RIGHTPADDING',(0,0),(-1,-1),16),('TOPPADDING',(0,0),(-1,-1),14),('BOTTOMPADDING',(0,0),(-1,-1),10)]))
        flow.extend([KeepTogether([cell]),Spacer(1,14)])
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
