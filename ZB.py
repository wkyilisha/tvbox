import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import random
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib3

# 禁用 SSL 警告（仅用于爬虫场景）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------- 代理池管理（使用 proxifly 免费代理列表）---------------------
PROXY_TXT_URL = 'https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt'

def fetch_and_update_proxy_list():
    """从 proxifly CDN 直接获取最新代理列表，并验证可用性"""
    try:
        response = requests.get(PROXY_TXT_URL, timeout=10)
        if response.status_code != 200:
            print(f"[代理] 获取失败，状态码：{response.status_code}")
            return []
        # 解析 TXT 文件，每行一个代理
        raw_proxies = response.text.strip().split('\n')
        proxies = [p.strip() for p in raw_proxies if p.strip()]
        if not proxies:
            return []
        print(f"[代理] 成功获取 {len(proxies)} 个代理，开始验证前20个...")
        
        valid_proxies = []
        for proxy in proxies[:20]:   # 只取前20个验证，避免耗时过长
            if verify_proxy(proxy):
                valid_proxies.append(proxy)
                print(f"  ✓ 可用代理: {proxy}")
        if not valid_proxies:
            print("[代理] 未验证到可用代理，将使用直连模式")
        return valid_proxies
    except Exception as e:
        print(f"[代理] 请求异常: {e}")
        return []

def verify_proxy(proxy, test_url="https://httpbin.org/ip", timeout=5):
    """测试代理是否可用"""
    try:
        proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        response = requests.get(test_url, proxies=proxies, timeout=timeout, verify=False)
        return response.status_code == 200
    except:
        return False

# 全局代理池（启动时获取一次）
PROXY_LIST = []

def init_proxy_pool():
    """初始化全局代理池"""
    global PROXY_LIST
    if not PROXY_LIST:
        PROXY_LIST = fetch_and_update_proxy_list()

def get_random_proxy():
    """随机获取一个代理"""
    if not PROXY_LIST:
        return None
    return random.choice(PROXY_LIST)

# ---------------------- 增强的网络请求函数（带代理轮换）---------------------
def fetch_html(url, referer, headers=None, max_retries=3):
    """使用代理轮询请求，失败后自动切换代理重试，最终降级到直连"""
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

    init_proxy_pool()   # 确保代理池已初始化

    for attempt in range(max_retries):
        proxy = get_random_proxy()
        proxies = None
        proxy_display = "直连"
        if proxy:
            proxies = {"http": f"http://{proxy}", "https": f"http://{proxy}"}
            proxy_display = proxy

        print(f"    [尝试 {attempt+1}] 使用 {proxy_display}")

        session = requests.Session()
        # 设置重试策略（连接层）
        retries = Retry(total=2, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
        session.mount('http://', HTTPAdapter(max_retries=retries))
        session.mount('https://', HTTPAdapter(max_retries=retries))

        try:
            response = session.get(url, headers=default_headers, proxies=proxies, timeout=(10, 30), verify=False)
            if response.status_code == 200:
                response.encoding = response.apparent_encoding
                return response.text
            else:
                print(f"    [失败] 状态码 {response.status_code}")
        except Exception as e:
            print(f"    [异常] {str(e)}")
        finally:
            session.close()

        time.sleep(1)   # 重试前等待

    print(f"    [错误] 所有尝试均失败: {url}")
    return None

# ---------------------- 解析 IP 列表页 ----------------------
def parse_ip_list(html):
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
    list_base = f'{base_url}/{list_php}'
    all_lines = []

    for page in range(1, total_pages + 1):
        if page == 1:
            list_url = list_base
            referer = base_url + '/'
        else:
            list_url = f'{list_base}?page={page}&iphone16=&code='
            referer = list_base if page == 2 else f'{list_base}?page={page-1}&iphone16=&code='

        print(f"[{list_php}] 正在抓取第 {page} 页: {list_url}")
        list_html = fetch_html(list_url, referer)
        if not list_html:
            print(f"[{list_php}] 第 {page} 页获取失败，跳过")
            continue

        entries = parse_ip_list(list_html)
        print(f"[{list_php}] 第 {page} 页提取到 {len(entries)} 个有效条目")
        time.sleep(2)

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
def run_crawler(total_pages=1):
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
    run_crawler(total_pages=1)
