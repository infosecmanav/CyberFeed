import argparse, json
from pathlib import Path
from .engine import build, ROOT

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['build']);p.add_argument('--out',default='site');p.add_argument('--fixtures',action='store_true');p.add_argument('--period',choices=['daily','weekly'],default='daily');a=p.parse_args()
    data=build(a.period,a.fixtures);out=Path(a.out);out.mkdir(parents=True,exist_ok=True)
    (out/'briefing.json').write_text(json.dumps(data,indent=2))
    print('Wrote '+str(out/'briefing.json'))
if __name__=='__main__':main()
