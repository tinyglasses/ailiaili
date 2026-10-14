"""Install public cover archives; verifies IDs, paths, bytes and hashes."""
import argparse,hashlib,json,zipfile
from pathlib import Path,PurePosixPath
p=argparse.ArgumentParser();p.add_argument('archive_dir',type=Path);a=p.parse_args()
root=Path(__file__).resolve().parents[1]
entries=json.loads((root/'data/covers.json').read_text(encoding='utf8'))['covers']
allowed={('ailiaili/site/'+r['path']):r for r in entries}
notices={'ailiaili/LICENSE','ailiaili/LICENSE-CONTENT','ailiaili/RIGHTS.md'}
archives=sorted(a.archive_dir.glob('ailiaili-covers-*.zip'))
if not archives:raise SystemExit('No ailiaili-covers-*.zip files found')
count=0
for archive in archives:
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            if member.is_dir():continue
            if member.filename in notices:continue
            if member.filename not in allowed:raise SystemExit('Unexpected archive entry')
            info=allowed[member.filename]
            if member.file_size!=info['bytes']:raise SystemExit('Unexpected file size')
            raw=z.read(member)
            if hashlib.sha256(raw).hexdigest()!=info['sha256']:raise SystemExit('Checksum mismatch')
            target=root/'site'/Path(*PurePosixPath(info['path']).parts)
            if not target.resolve().is_relative_to((root/'site/assets').resolve()):raise SystemExit('Unsafe destination')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw);count+=1
missing=[r['video_id'] for r in entries if not (root/'site'/r['path']).is_file()]
print(f'Imported {count} covers; missing {len(missing)}')
if missing:raise SystemExit(1)
