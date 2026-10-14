"""Release deployment must reject corrupt packages and private/non-site files."""
import hashlib
import importlib.util
from pathlib import Path
import stat
import zipfile

import pytest

spec = importlib.util.spec_from_file_location(
    'prepare_pages', Path(__file__).resolve().parents[1] / 'tools/prepare_pages.py')
pages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pages)


def make_package(tmp_path, extra=None):
    path = tmp_path / 'ailiaili-web.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name in sorted(pages.REQUIRED_FILES):
            archive.writestr(name, '<html>example</html>' if name == 'index.html' else '')
        archive.writestr('assets/640/video_creator_001_d000_01.webp', b'example-image')
        if extra:
            archive.writestr(*extra)
    sums = tmp_path / 'SHA256SUMS.txt'
    sums.write_text(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
    return path, sums


def test_valid_package_and_existing_directory(tmp_path):
    path, sums = make_package(tmp_path)
    output = tmp_path / 'public'
    result = pages.prepare_pages(path, sums, output)
    assert (output / 'index.html').read_text() == '<html>example</html>'
    assert result['site_bytes'] == sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    with pytest.raises(ValueError, match='already exists'):
        pages.prepare_pages(path, sums, output)


def test_bad_checksum_never_creates_output(tmp_path):
    path, sums = make_package(tmp_path)
    sums.write_text('0' * 64 + '  ' + path.name + '\n')
    with pytest.raises(ValueError, match='SHA256'):
        pages.prepare_pages(path, sums, tmp_path / 'public')
    assert not (tmp_path / 'public').exists()


@pytest.mark.parametrize('name', ['LICENSE', 'LICENSE-CONTENT', 'RIGHTS.md'])
def test_release_notices_are_preserved(tmp_path, name):
    path, sums = make_package(tmp_path, (name, 'Public license notice'))
    output = tmp_path / 'public'
    pages.prepare_pages(path, sums, output)
    assert (output / name).read_text() == 'Public license notice'


@pytest.mark.parametrize('name', ['../private.txt', 'assets/../.env', 'runs/world.db', '.git/config', 'assets/private.json'])
def test_non_public_paths_rejected_before_writing(tmp_path, name):
    path, sums = make_package(tmp_path, (name, 'private'))
    with pytest.raises(ValueError, match='archive path|Unexpected file'):
        pages.prepare_pages(path, sums, tmp_path / 'public')
    assert not (tmp_path / 'public').exists()


def test_symlink_rejected(tmp_path):
    link = zipfile.ZipInfo('cover-derivatives.json')
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    path, sums = make_package(tmp_path, (link, '../private.json'))
    with pytest.raises(ValueError, match='archive path'):
        pages.prepare_pages(path, sums, tmp_path / 'public')


def test_size_limit_before_writing(tmp_path, monkeypatch):
    path, sums = make_package(tmp_path)
    monkeypatch.setattr(pages, 'MAX_SITE_BYTES', 8)
    with pytest.raises(ValueError, match='size budget'):
        pages.prepare_pages(path, sums, tmp_path / 'public')
    assert not (tmp_path / 'public').exists()
