"""Build a small, standalone browsing copy; never overwrite source PNGs or film HTML."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
from xml.sax.saxutils import escape

from PIL import Image


def xml_metadata(video_id: str, source_hash: str) -> bytes:
    # This declares provenance for a resized derivative. It is not an original C2PA signature.
    return f'''<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"><rdf:Description xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:iptcExt="http://iptc.org/std/Iptc4xmpExt/2008-02-29/" iptcExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/trainedAlgorithmicMedia"><dc:description><rdf:Alt><rdf:li xml:lang="x-default">AI-generated AiliAili cover, resized and compressed for web browsing. This derivative has no inherited cryptographic C2PA validation.</rdf:li></rdf:Alt></dc:description><dc:source>video_id={escape(video_id)}; original_sha256={escape(source_hash)}</dc:source></rdf:Description></rdf:RDF></x:xmpmeta>'''.encode('utf-8')


def convert(job: dict) -> dict:
    source = Path(job['source'])
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if original_hash != job['sha256']:
        raise ValueError(f"Source hash mismatch for {job['video_id']}")
    row = {'video_id': job['video_id'], 'source_sha256': original_hash,
           'source_bytes': source.stat().st_size, 'variants': {}}
    with Image.open(source) as original:
        row['source_dimensions'] = list(original.size)
        original.load()
        if original.mode not in ('RGB', 'RGBA'):
            original = original.convert('RGBA' if 'A' in original.getbands() else 'RGB')
        for width, quality in ((640, 80), (1280, 88)):
            target = Path(job['output']) / 'assets' / str(width) / (job['video_id'] + '.webp')
            copy = original.copy()
            if copy.width > width:
                copy = copy.resize((width, round(copy.height * width / copy.width)), Image.Resampling.LANCZOS)
            copy.save(target, 'WEBP', quality=quality, method=4,
                      xmp=xml_metadata(job['video_id'], original_hash))
            row['variants'][str(width)] = {
                'path': target.relative_to(job['output']).as_posix(),
                'width': copy.width, 'height': copy.height, 'quality': quality,
                'bytes': target.stat().st_size,
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            }
    return row


def load_film_world(source: Path) -> dict:
    data = json.loads((source / 'data/world.json').read_text(encoding='utf-8'))
    community = data.get('community', {}) if isinstance(data, dict) else {}
    scope = data.get('export_scope', {}) if isinstance(data, dict) else {}
    if (not isinstance(community, dict)
            or not isinstance(community.get('snapshots'), dict)
            or not community['snapshots']
            or not isinstance(community.get('catalog'), dict)
            or (isinstance(scope, dict) and scope.get('has_film_site_schema') is False)):
        raise ValueError(
            'This builder requires the film-site data/world.json with homepage snapshots. '
            'An analysis projection from export_run_data.py is not compatible; '
            'use the new run\'s own report or the data/inspiration tools instead.'
        )
    return data


def build_html(source: Path, output: Path, rows: list[dict], data: dict) -> dict:
    html = source.read_text(encoding='utf-8')
    pattern = r'(<script id="stage-source" type="application/json">)([\s\S]*?)(</script>)'
    matched = re.search(pattern, html)
    if not matched:
        raise ValueError('Expected embedded stage-source data')
    data['community']['cover_assets'] = {r['video_id']: r['variants']['1280']['path'] for r in rows}
    data['community']['cover_thumbnails'] = {r['video_id']: r['variants']['640']['path'] for r in rows}
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    html = re.sub(pattern, lambda m: m[1] + payload + m[3], html, count=1)
    original_tag = '<img src="${esc(img)}" alt="${esc(w.cover_description)}" loading="eager">'
    card_tag = '<img ${webCoverAttrs(w)} alt="${esc(w.cover_description)}">'
    if html.count(original_tag) != 1:
        raise ValueError('Unexpected card image template')
    html = html.replace(original_tag, card_tag)
    original_lead = '<img src="${esc(img)}" alt="${esc(w.title)}">'
    if html.count(original_lead) != 1:
        raise ValueError('Unexpected lead image template')
    html = html.replace(original_lead, '<img ${webCoverAttrs(w,true)} alt="${esc(w.title)}">')
    helper = '''
function webCoverAttrs(w,lead=false){
  const full=COMMUNITY.cover_assets[w.video_id],thumb=COMMUNITY.cover_thumbnails[w.video_id];
  if(state.mode==='film')return `src="${esc(full)}" loading="eager" decoding="async"`;
  const sizes=lead?'(max-width: 760px) 100vw, 50vw':'(max-width: 600px) 50vw, (max-width: 1000px) 33vw, 360px';
  return `src="${esc(thumb)}" srcset="${esc(thumb)} 640w, ${esc(full)} 1280w" sizes="${sizes}" loading="${lead?'eager':'lazy'}" decoding="async"${lead?' fetchpriority="high"':''}`;
}
'''
    html = html.replace('function communityCover(w){', helper + '\nfunction communityCover(w){', 1)
    for asset in ('browse-core.js', 'explore.js', 'explore.css', 'recommendation-core.js', 'recommendation-data.js', 'recommendation-ui.js'):
        shutil.copyfile(source.parent / asset, output / asset)
        fingerprint = hashlib.sha256((output / asset).read_bytes()).hexdigest()[:12]
        html = re.sub(r'((?:src|href)="' + re.escape(asset) + r')(?:\?[^"\s]*)?(\")',
                      lambda m: f'{m[1]}?v={fingerprint}{m[2]}', html)
    (output / 'index.html').write_text(html, encoding='utf-8', newline='\n')
    encoded = html.encode('utf-8')
    rowmap = {r['video_id']: r for r in rows}
    snapshots = []
    for snapshot in data['community']['snapshots'].values():
        ids = [c['video_id'] for c in snapshot['feed']['cards']]
        records = [rowmap[i] for i in ids if i in rowmap]
        snapshots.append({
            'day': snapshot['session']['day'], 'cards': len(ids),
            'original_images_bytes': sum(r['source_bytes'] for r in records),
            'all_640_images_bytes': sum(r['variants']['640']['bytes'] for r in records),
            'all_1280_images_bytes': sum(r['variants']['1280']['bytes'] for r in records),
        })
    return {'html_bytes': len(encoded), 'html_gzip_bytes_estimate': len(gzip.compress(encoded)),
            'snapshots': snapshots}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1], help='Project root with data/world.json and site/index.html')
    parser.add_argument('--output', type=Path, default=Path('dist/site'))
    parser.add_argument('--text-only', action='store_true', help='Build without covers; no PNG download required')
    parser.add_argument('--workers', type=int, default=4)
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise SystemExit('Output already exists; choose a new destination to avoid overwriting.')
    if output == source or output == source / 'site':
        raise SystemExit('Output must not overwrite source root or site/.')
    try:
        data = load_film_world(source)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    for width in (640, 1280):
        (output / 'assets' / str(width)).mkdir(parents=True)
    covers = json.loads((source / 'data/covers.json').read_text(encoding='utf-8'))['covers']
    jobs = []
    for c in ([] if args.text_only else covers):
        original = (source / 'site' / c['path']).resolve()
        if source not in original.parents:
            raise ValueError('Source path escapes repository')
        jobs.append({**c, 'source': str(original), 'output': str(output)})
    started = time.monotonic()
    rows = []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(convert, job) for job in jobs]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 200 == 0 or len(rows) == len(jobs):
                print(json.dumps({'converted': len(rows), 'total': len(jobs),
                                  'elapsed_seconds': round(time.monotonic() - started)}), flush=True)
    rows.sort(key=lambda r: r['video_id'])
    summary = build_html(source / 'site/index.html', output, rows, data)
    for notice in ('LICENSE', 'LICENSE-CONTENT', 'RIGHTS.md'):
        shutil.copyfile(source / notice, output / notice)
    (output / '.nojekyll').write_text('', encoding='utf-8')
    provenance = {'schema': 'ailiaili-web-derivatives-v1',
                  'notice': 'AI-generated covers. Web variants are resized derivatives; original PNGs and original provenance remain in the separate source release. XMP is a declaration, not a new cryptographic signature.',
                  'covers': rows}
    (output / 'cover-derivatives.json').write_text(json.dumps(provenance, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    summary.update({'created_utc': datetime.now(timezone.utc).isoformat(),
                    'covers': len(rows), 'files_per_cover': 2,
                    'original_image_bytes': sum(r['source_bytes'] for r in rows),
                    'variant_bytes': {str(w): sum(r['variants'][str(w)]['bytes'] for r in rows) for w in (640, 1280)},
                    'site_bytes': sum(p.stat().st_size for p in output.rglob('*') if p.is_file()),
                    'elapsed_seconds': round(time.monotonic() - started, 1),
                    'source_html_sha256': hashlib.sha256((source/'site/index.html').read_bytes()).hexdigest(),
                    'status': 'local build; deployment is a separate step',
                    'text_only': args.text_only,
                    'quality_review': 'No per-cover visual or content review is performed by this tool'})
    summary['under_one_billion_bytes'] = summary['site_bytes'] + 10_000 < 1_000_000_000
    (output / 'build-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    if not summary['under_one_billion_bytes']:
        raise SystemExit('Site exceeds the 1 GB deployment budget')


if __name__ == '__main__':
    main()
