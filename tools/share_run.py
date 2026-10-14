"""Make a portable result-and-reading ZIP for your completed run (stdlib only)."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

from export_run_data import export
from export_inspiration import export_inspiration


def share_run(run_dir: Path, output: Path) -> dict:
    output = output.resolve()
    archive = output.with_name(output.name + '.zip')
    if output.exists() or archive.exists():
        raise ValueError('Choose a new output directory and ZIP name; existing results are preserved.')
    result = export(run_dir, output / 'data')
    reading = export_inspiration(output / 'data/world.json', output / 'reading', include_outlines=True)
    (output / 'README.md').write_text(
        '# 我的 AiliAili 世界\n\n'
        '[从账号与作品开始读](reading/README.md) · [数据说明](data/README.md)\n\n'
        f"这里记录一次AI内容社区模拟，截至Day {result['last_completed_day']}，"
        f"包含{result['creators']}个账号、{result['works']}份作品。作品是标题、简介与大纲，并非已经制作的视频。\n\n"
        'reading/中的Markdown可以本机阅读，也可上传自己的GitHub后直接浏览；不要求读者运行Python。'
        'data/包含已完成日的作品、指标与少量配置字段，可交给项目的数据分析工具。\n\n'
        '本包不含原始数据库、运行记忆、模型请求响应、凭据、个人讨论、图片或项目开发历史。'
        '人设和作品文本按原样导出，分享者仍需确认自己加入的内容适合公开。'
        '本包不是可续跑快照，也不是成片版网站。模型模式与公开配置可见data/world.json。\n\n'
        'manifest.json列出了文件与SHA256，便于确认这份结果是否改变。'
        '自己的新作品如何授权由分享者说明；随包模板的许可状态见[RIGHTS](reading/RIGHTS.md)。\n',
        encoding='utf-8')
    files = sorted(p for p in output.rglob('*') if p.is_file())
    allowed = {'README.md', 'data/README.md', 'data/world.json', 'data/daily_metrics.csv',
               'reading/LICENSE', 'reading/LICENSE-CONTENT'}
    for path in files:
        relative = path.relative_to(output).as_posix()
        if relative not in allowed and not (relative.startswith('reading/') and relative.endswith('.md')):
            raise ValueError(f'Unexpected file in public result bundle: {relative}')
    manifest = {'schema': 'ailiaili-shared-results-v1',
                'run_id': result['run_id'], 'last_completed_day': result['last_completed_day'],
                'creators': reading['creators'], 'works': reading['works'],
                'files': [{'path': p.relative_to(output).as_posix(), 'bytes': p.stat().st_size,
                           'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]}
    manifest_path = output / 'manifest.json'
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in [*files, manifest_path]:
            bundle.write(path, 'ailiaili-world/' + path.relative_to(output).as_posix())
    return {**result, 'output': str(output), 'archive': str(archive), 'zip_bytes': archive.stat().st_size}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(share_run(args.run_dir, args.output), ensure_ascii=False))
    except (ValueError, OSError, sqlite3.DatabaseError) as exc:
        parser.exit(2, str(exc)+'\n')


if __name__ == '__main__':
    main()
