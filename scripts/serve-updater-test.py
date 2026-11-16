"""Canal de teste estritamente loopback; serve somente manifesto e instaladores assinados."""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
import re


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        name = unquote(urlsplit(self.path).path)
        if name != '/latest.json' and not re.fullmatch(r'/\d+\.\d+\.\d+/setup\.exe(?:\.sig)?', name):
            self.send_error(404)
            return
        target = Path(self.directory) / name.lstrip('/')
        def link(path):
            return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())
        if link(target) or link(target.parent) or not target.is_file():
            self.send_error(404)
            return
        super().do_GET()

    def do_HEAD(self):
        self.send_error(405)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    parser.add_argument('--port',type=int,default=19420)
    args=parser.parse_args()
    if args.port in {1420,8765} or not 1024 <= args.port <= 65535:
        parser.error('porta reservada ou inválida')
    root=args.directory.resolve(strict=True)
    if not root.is_dir(): parser.error('diretório inválido')
    server=ThreadingHTTPServer(('127.0.0.1',args.port),partial(Handler,directory=str(root)))
    print(f'Canal de teste em http://127.0.0.1:{args.port}; Ctrl+C para encerrar',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
