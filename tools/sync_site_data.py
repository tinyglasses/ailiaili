import json,re
from pathlib import Path
root=Path(__file__).resolve().parents[1];p=root/'site/index.html'
d=json.loads((root/'data/world.json').read_text(encoding='utf8'))
payload=json.dumps(d,ensure_ascii=False,separators=(',',':')).replace('</',r'<\/')
pattern=r'(<script id="stage-source" type="application/json">)[\s\S]*?(</script>)'
s,n=re.subn(pattern,lambda m:m[1]+payload+m[2],p.read_text(encoding='utf8'))
if n!=1:raise SystemExit('Expected exactly one data block')
p.write_text(s,encoding='utf8')
