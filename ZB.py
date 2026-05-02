import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import ssl
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib3.util.ssl_ import create_urllib3_context

# ---------------------- 自定义 TLS Adapter 强制 TLSv1.2 ----------------------
class TLSAdapter(HTTPAdapter):
    """强制使用 TLSv1.2，解决部分服务器在 GitHub Actions 环境的握手失败问题"""
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

# ---------------------- 增强的网络请求函数 ----------------------
def fetch_html(url, referer, headers=None, max_retries=3):
    """
    增强版请求函数，适合 GitHub Actions 环境：
    - 使用 Session 配置重试策略
    - 强制 TLSv1.2
    - 禁用证书验证（仅爬虫场景）
    - 输出调试信息
    """
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    if headers:
        default_headers.update(headers)
    if referer:
        default_headers['Referer'] = referer

    # 创建带重试的 Session
    session = requests.Session()
    retry_strategy = Retry(
        total=max_retries,
        backoff_factor=1,          # 重试间隔：1, 2, 4 秒
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"]
    )
    http_adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", http_adapter)
    session.mount("https://", TLSAdapter())   # 使用自定义 TLS 适配器

    try:
        response = session.get(
            url,
            headers=default_headers,
            timeout=(10, 30),     # (连接超时, 读取超时)
            verify=False          # 跳过证书验证（仅用于爬虫）
        )
        # 调试输出（可在 GitHub Actions 日志中查看）
        print(f"    [调试] {url} -> 状态码: {response.status_code}, 内容长度: {len(response.text)}")
        if response.status_code != 200:
            print(f"    [警告] 非200响应，预览: {response.text[:200]}")
            return None

        response.encoding = response.apparent_encoding
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"    请求失败: {url}, 错误: {e}")
        return None
    finally:
        session.close()

# ---------------------- 解析 IP 列表页 ----------------------
def parse_ip_list(html):
    """解析列表页，提取IP、地区、运营商及参数"""
    soup = BeautifulSoup(html, 'html.parser')
    entries = []
    result_divs = soup.find_all('div', class_='result')

    for div in result_divs:
        if '暂时失效' in div.get_text():
            continue
        channel_link = div.find('a', href=re.compile(r'channellist\.html\?ip='))
        if not channel_link:
            continue
        href = channel_link.get('href')
        params = parse_qs(urlparse(href).query)
        ip = params.get('ip', [''])[0]
        tk = params.get('tk', [''])[0]
        p_val = params.get('p', ['1'])[0]
        if not ip or not tk:
            continue

        info_tag = div.find('i')
        location, isp = '未知地区', '未知运营商'
        if info_tag:
            info_text = info_tag.get_text(strip=True)
            parts = re.split(r'\d{2}:\d{2}上线\s*', info_text)
            if len(parts) > 1:
                geo_isp = parts[-1].strip()
                match = re.match(
                    r'(.+?)\s+((?:[\u4e00-\u9fa5]+)?(?:电信|联通|移动|广电|铁通|长宽|教育网))\s*$',
                    geo_isp
                )
                if match:
                    location = match.group(1).strip()
                    isp = match.group(2).strip()
                else:
                    location = geo_isp

        entries.append({
            'ip': ip,
            'tk': tk,
            'p': p_val,
            'region_isp': f"{location} {isp}"
        })
    return entries

# ---------------------- 解析频道详情页 ----------------------
def parse_channel_page(html):
    """解析频道详情页，提取频道名和m3u8地址"""
    soup = BeautifulSoup(html, 'html.parser')
    channels = []
    result_divs = soup.find_all('div', class_='result')

    for div in result_divs:
        channel_div = div.find('div', class_='channel')
        if not channel_div:
            continue
        tip_div = channel_div.find('div', class_='tip')
        if not tip_div:
            continue
        channel_name = tip_div.get_text(strip=True)
        if not channel_name:
            continue
        m3u8_div = div.find('div', class_='m3u8')
        if not m3u8_div:
            continue
        m3u8_url = ''
        for td in m3u8_div.find_all('td'):
            text = td.get_text(strip=True)
            if text.startswith('http'):
                m3u8_url = text
                break
        if m3u8_url:
            channels.append({'channel_name': channel_name, 'm3u8_url': m3u8_url})
    return channels

# ---------------------- 抓取单个源的所有频道 ----------------------
def crawl_source(base_url, list_php, total_pages, output_file):
    """抓取指定来源的所有频道，并写入文件"""
    list_base = f'{base_url}/{list_php}'
    all_lines = []

    for page in range(1, total_pages + 1):
        # 构造列表页URL和Referer
        if page == 1:
            list_url = list_base
            referer = base_url + '/'
        else:
            list_url = f'{list_base}?page={page}&iphone16=&code='
            referer = list_base if page == 2 else f'{list_base}?page={page-1}&iphone16=&code='

        print(f"[{list_php}] 正在抓取第 {page} 页: {list_url}")
        list_html = fetch_html(list_url, referer)
        if not list_html:
            continue

        entries = parse_ip_list(list_html)
        print(f"[{list_php}] 第 {page} 页提取到 {len(entries)} 个有效条目")
        time.sleep(2)   # 加大延迟，避免请求过快

        for entry in entries:
            ip, tk, p = entry['ip'], entry['tk'], entry['p']
            region_isp = entry['region_isp']
            detail_url = f"{base_url}/getall26.php?ip={ip}&c=&tk={tk}&p={p}"
            channel_ref = f"{base_url}/channellist.html?ip={ip}&tk={tk}&p={p}"

            print(f"  [{list_php}] 抓取 {region_isp} 的频道...")
            detail_html = fetch_html(detail_url, channel_ref)
            if not detail_html:
                continue

            channels = parse_channel_page(detail_html)
            print(f"  [{list_php}] 获取到 {len(channels)} 个频道")
            if channels:
                all_lines.append(f"{region_isp},#genre#")
                for ch in channels:
                    all_lines.append(f"{ch['channel_name']},{ch['m3u8_url']}")
            time.sleep(1)

    if all_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))
        print(f"[{list_php}] 完成！共写入 {len(all_lines)} 行至 {output_file}")
    else:
        print(f"[{list_php}] 未获取到有效数据，{output_file} 留空")

# ---------------------- 主函数 ----------------------
def run_crawler(total_pages=3):
    """依次爬取两个源，生成两个文件"""
    base_url = 'https://tonkiang.us'
    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}
    ]

    for source in sources:
        print(f"\n开始抓取 {source['php']} ...")
        crawl_source(base_url, source['php'], total_pages, source['output'])
        print(f"{source['php']} 抓取结束\n")

if __name__ == '__main__':
    # 可根据需要修改 total_pages（例如抓取前3页）
    run_crawler(total_pages=1)
