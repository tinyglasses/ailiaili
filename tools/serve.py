"""Serve one static output directory locally, without model or package dependencies."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--directory', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'site',
                        help='Directory containing index.html; defaults to the published-world site.')
    args = parser.parse_args()
    directory = args.directory.resolve()
    if not (directory / 'index.html').is_file():
        parser.error('The selected directory must contain index.html.')
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    with ThreadingHTTPServer(('127.0.0.1', args.port),
                             partial(SimpleHTTPRequestHandler, directory=str(directory))) as server:
        print(f'Open http://127.0.0.1:{args.port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == '__main__':
    main()
