"""第二篇可选联网工具：只搜索、读取上海文旅官网主站的公开资料。"""
import argparse
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import os
import re
from urllib.parse import urlsplit, urlunsplit

SEARCH_ENDPOINT = 'https://api.bochaai.com/v1/web-search'
MAX_BYTES = 512 * 1024
MAX_TEXT = 6000


def checked_url(url):
    if not isinstance(url, str) or len(url) > 500 or any(c.isspace() for c in url):
        raise ValueError('网址格式不正确')
    p = urlsplit(url)
    if (p.scheme != 'https' or p.netloc != 'whlyj.sh.gov.cn'
            or not re.fullmatch(r'/[A-Za-z0-9_./-]*', p.path)
            or any(part in ('.', '..') for part in p.path.split('/'))
            or p.query or '\\' in url):
        raise ValueError('教学版只读取 https://whlyj.sh.gov.cn/ 下无查询参数的网页')
    return urlunsplit((p.scheme, p.netloc, p.path, '', ''))


class PageText(HTMLParser):
    """静态 HTML 的轻量文本抽取，不运行脚本，不保证精确还原排版。"""
    HIDDEN = {'script', 'style', 'nav', 'header', 'footer', 'aside'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden = []
        self.title_depth = 0
        self.title = []
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden.append(tag)
        if tag == 'title':
            self.title_depth += 1

    def handle_endtag(self, tag):
        if tag in self.hidden:
            self.hidden.remove(tag)
        if tag == 'title':
            self.title_depth = max(0, self.title_depth - 1)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_data(self, data):
        if self.title_depth:
            self.title.append(data)
        elif not self.hidden:
            self.parts.append(data)


class WebTools:
    def __init__(self, api_key=None, transport=None):
        self.api_key = os.getenv('BOCHA_API_KEY', '') if api_key is None else api_key
        self.transport = transport  # 本地测试注入假 HTTP；模型不能设置它。

    def _request(self, method, url, *, json_body=None, headers=None, content_type):
        try:
            import httpx
        except ImportError:
            raise ValueError('请安装 requirements-web.txt') from None
        request_headers = {'User-Agent': 'XiaodaziWeekendExample/1.0', 'Accept-Encoding': 'identity'}
        request_headers.update(headers or {})
        try:
            # 每次新建客户端，不带登录态，不跟随重定向，不读取环境代理设置。
            with httpx.Client(timeout=15, follow_redirects=False, trust_env=False,
                              transport=self.transport) as client:
                with client.stream(method, url, json=json_body, headers=request_headers) as response:
                    if not 200 <= response.status_code < 300:
                        raise ValueError(f'联网请求未成功：HTTP {response.status_code}；不自动重试或跳转')
                    mime = response.headers.get('content-type', '').split(';')[0].strip().lower()
                    if mime not in content_type:
                        raise ValueError('返回的内容类型不受支持')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise ValueError('教学版不接收压缩响应')
                    if int(response.headers.get('content-length', '0')) > MAX_BYTES:
                        raise ValueError('响应超过512KiB限制')
                    data = bytearray()
                    for chunk in response.iter_bytes(chunk_size=8192):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ValueError('响应超过512KiB限制')
                    return bytes(data)
        except httpx.HTTPError as exc:
            # 不把原始响应或请求凭据写入模型上下文。
            raise ValueError('联网请求失败：' + type(exc).__name__) from None

    def read_webpage(self, url):
        url = checked_url(url)  # 在发起网络请求前校验。
        data = self._request('GET', url, content_type={'text/html'})
        page = PageText()
        page.feed(data.decode('utf-8', errors='replace'))
        text = ' '.join(' '.join(page.parts).split())
        if not text:
            raise ValueError('未提取到网页文本')
        return {'url': url, 'title': ' '.join(page.title)[:200], 'content': text[:MAX_TEXT],
                'truncated': len(text) > MAX_TEXT, 'kind': 'page_text',
                'fetched_at': datetime.now(timezone.utc).isoformat()}

    def search_web(self, query):
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError('搜索词需要1–200字符')
        if not self.api_key:
            raise ValueError('搜索需要 BOCHA_API_KEY；仅读取已知官方网址不需要搜索密钥')
        raw = self._request('POST', SEARCH_ENDPOINT,
                            headers={'Authorization': 'Bearer ' + self.api_key},
                            json_body={'query': query + ' site:whlyj.sh.gov.cn', 'freshness': 'noLimit',
                                       'summary': True, 'count': 5}, content_type={'application/json'})
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('code', 200) != 200:
            raise ValueError('搜索服务返回失败状态')
        data = payload.get('data', payload)
        pages = data.get('webPages') if isinstance(data, dict) else None
        values = pages.get('value') if isinstance(pages, dict) else None
        if not isinstance(values, list):
            raise ValueError('搜索响应结构不符合预期')
        results = []
        for item in values:
            if not isinstance(item, dict):
                continue
            try:
                url = checked_url(item.get('url'))
            except ValueError:
                continue
            results.append({'title': str(item.get('name') or '')[:200], 'url': url,
                            'snippet': str(item.get('summary') or item.get('snippet') or '')[:600]})
            if len(results) == 3:
                break
        return {'results': results, 'kind': 'search_snippets',
                'scope': '仅保留上海文旅官网主站；摘要不代表已读正文，历史活动不代表本周有效',
                'fetched_at': datetime.now(timezone.utc).isoformat()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['read', 'search'])
    parser.add_argument('value')
    args = parser.parse_args()
    web = WebTools()
    try:
        result = web.read_webpage(args.value) if args.action == 'read' else web.search_web(args.value)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except ValueError as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
