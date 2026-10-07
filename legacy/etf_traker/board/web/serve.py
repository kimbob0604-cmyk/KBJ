#!/usr/bin/env python3
"""
로컬 서버 — 만들어 둔 정적 사이트를 내 컴퓨터에서만 연다.

`docs/index.html` 을 파일로 바로 열어도 화면은 나오지만, 헤더의 날짜 선택기가
`d/index.json` 을 fetch 하므로 `file://` 에서는 브라우저가 막는다(CORS).
그래서 과거 날짜로 넘어가지 않는다. 로컬 HTTP 로 띄우면 그대로 동작한다.

기본 바인딩은 127.0.0.1 이다. 같은 공유기의 다른 기기에서도 보려면 --host 로
명시적으로 열어야 한다. 기본값이 조용히 외부에 열려 있으면 안 된다.
"""
import functools
import http.server
import os
import socket
import threading
import webbrowser

DEFAULT_PORT = 8787


class Handler(http.server.SimpleHTTPRequestHandler):
    """캐시를 끈다. daily 를 다시 돌렸는데 어제 화면이 보이면 안 된다."""

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store, must-revalidate')
        super().end_headers()

    def log_message(self, fmt, *args):
        # 404 만 남긴다. 200 로그가 쏟아지면 진짜 문제가 안 보인다.
        code = args[1] if len(args) > 1 else ''
        if str(code).startswith(('4', '5')):
            super().log_message(fmt, *args)


def _free_port(host, port, tries=20):
    """포트가 물려 있으면 다음 걸 쓴다. 이미 뭔가 돌고 있는 게 흔하다."""
    for i in range(tries):
        s = socket.socket()
        try:
            s.bind((host, port + i))
            return port + i
        except OSError:
            continue
        finally:
            s.close()
    raise OSError(f'{host}:{port}~{port + tries - 1} 이 전부 사용 중이다')


def serve(root, host='127.0.0.1', port=DEFAULT_PORT, open_browser=True,
          log=print, forever=True):
    """사이트를 로컬에 띄운다. 반환 (server, url)."""
    if not os.path.isdir(root):
        raise FileNotFoundError(
            f'{root} 가 없다. 먼저 화면을 만들어야 한다 — '
            'python3 -m board.run --demo (합성) 또는 --daily (실데이터)')
    if not os.path.exists(os.path.join(root, 'index.html')):
        raise FileNotFoundError(
            f'{root}/index.html 이 없다. python3 -m board.run --render 를 먼저 돌려라')

    port = _free_port(host, port)
    handler = functools.partial(Handler, directory=root)
    httpd = http.server.ThreadingHTTPServer((host, port), handler)
    url = f'http://{host}:{port}/'

    log(f'  로컬 보드 → {url}')
    if host not in ('127.0.0.1', 'localhost'):
        log('  주의: 이 주소는 같은 네트워크의 다른 기기에서도 열린다')
    log('  끄려면 Ctrl+C')

    if open_browser:
        # 서버가 뜬 다음에 열어야 첫 요청이 안 튕긴다.
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    if not forever:
        return httpd, url
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        log('\n  종료')
    finally:
        httpd.shutdown()
        httpd.server_close()
    return httpd, url
