import argparse
import json
from pathlib import Path
from .engine import build
from .reports import pdf_report, sections


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['build'])
    parser.add_argument('--out', default='site')
    parser.add_argument('--fixtures', action='store_true')
    parser.add_argument('--period', choices=['daily', 'weekly'], default='daily')
    parser.add_argument('--format', choices=['json', 'text', 'pdf', 'all'], default='json')
    parser.add_argument('--strict', action='store_true', help='Exit with status 2 if any source or advisory retrieval failed')
    args = parser.parse_args()
    data = build(args.period, args.fixtures)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.format in ('json', 'all'):
        (out / 'briefing.json').write_text(json.dumps(data, indent=2), encoding='utf-8')
    if args.format in ('text', 'all'):
        (out / 'briefing.txt').write_text('\n\n'.join(sections(data)), encoding='utf-8')
    if args.format in ('pdf', 'all'):
        (out / 'briefing.pdf').write_bytes(pdf_report(data))
    print(f"Wrote {args.format} briefing to {out}; {len(data['stories'])} updates, {len(data['errors'])} coverage errors")
    if args.strict and data['errors']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
