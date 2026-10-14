"""Verify a release web archive and stage only its public static files for Pages."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import zipfile


MAX_SITE_BYTES = 1_000_000_000
ROOT_FILES = {
    'index.html', '.nojekyll', 'browse-core.js', 'explore.js', 'explore.css',
    'recommendation-core.js', 'recommendation-data.js', 'recommendation-ui.js',
    'cover-derivatives.json',
}
REQUIRED_FILES = ROOT_FILES - {'cover-derivatives.json'}
IMAGE_PATH = re.compile(r'assets/(640|1280)/video_creator_[0-9]{3}_d[0-9]{3}_[0-9]{2}\.webp')


def prepare_pages(archive_path: Path, checksums_path: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise ValueError('Output already exists; choose a new directory')
    matching = []
    for line in checksums_path.read_text(encoding='utf-8-sig').splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)', line)
        if match and match[2] == archive_path.name:
            matching.append(match[1].lower())
    if len(matching) != 1:
        raise ValueError('Checksum list must contain this archive exactly once')
    with archive_path.open('rb') as stream:
        actual_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    if actual_hash != matching[0]:
        raise ValueError('Archive SHA256 mismatch')

    with zipfile.ZipFile(archive_path) as archive:
        files = []
        seen = set()
        total = 0
        for info in archive.infolist():
            name = info.filename
            path = PurePosixPath(name)
            normalized = name.rstrip('/') if info.is_dir() else name
            mode = stat.S_IFMT(info.external_attr >> 16)
            if (path.is_absolute() or '..' in path.parts or '\\' in name
                    or ':' in name or path.as_posix() != normalized
                    or mode not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or name.casefold() in seen):
                raise ValueError(f'Unsafe or duplicate archive path: {name}')
            seen.add(name.casefold())
            if info.is_dir():
                if name not in {'assets/', 'assets/640/', 'assets/1280/'}:
                    raise ValueError(f'Unexpected directory in web archive: {name}')
                continue
            if mode == stat.S_IFDIR or (name not in ROOT_FILES and not IMAGE_PATH.fullmatch(name)):
                raise ValueError(f'Unexpected file in web archive: {name}')
            total += info.file_size
            if total >= MAX_SITE_BYTES:
                raise ValueError('Unpacked site exceeds the Pages size budget')
            files.append(info)
        if not REQUIRED_FILES.issubset({info.filename for info in files}):
            raise ValueError('Web archive is missing required page assets')

        # Validate before writing; a failed extraction never becomes the deploy directory.
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pages-', dir=output.parent) as temporary:
            staged = Path(temporary) / 'site'
            staged.mkdir()
            for info in files:
                destination = staged / info.filename
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open('xb') as target:
                    shutil.copyfileobj(source, target)
                if destination.stat().st_size != info.file_size:
                    raise ValueError(f'Incomplete extracted file: {info.filename}')
            staged.rename(output)
    return {'files': len(files), 'site_bytes': total, 'archive_sha256': actual_hash}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--checksums', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare_pages(args.archive, args.checksums, args.output)
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.exit(2, f'{error}\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
