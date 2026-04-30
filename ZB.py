import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import random
import os
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================= 代理相关配置 =================
PROXY_URLS = [
    'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt',
    'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/https/data.txt',
]
PROXY_TEST_URL = 'https://tonkiang.us/'
PROXY_TIMEOUT = 8          # 单个代理测试超时
MAX_PROXIES_TO_TEST = 200  # 最多测试前 N 个（避免耗时过久）
MIN_WORKING_PROXIES = 5    # 至少需要的可用代理数
# ==============================================

def download_proxy_list():
    """下载代理列表并去重，返回 list of 'http://ip:port'"""
    raw_proxies = set()
    for url in PROXY_URLS:
        try:
            resp = requests.get(url, timeout=15)
            lines = resp.text.strip().split()
            for line in lines:
                line = line.strip()
                if line.startswith('http://') and ':' in line:
                    raw_proxies.add(line)
        except Exception as e:
            print(f"  下载代理列表失败 ({url}): {e}")
    proxies = list(raw_proxies)
    random.shuffle(proxies)
    return proxies

def test_one_proxy(proxy):
    """测试单个代理是否可用（200 即视为可用）"""
    try:
        resp = requests.get(PROXY_TEST_URL,
                            proxies={'http': proxy, 'https': proxy},
                            timeout=PROXY_TIMEOUT)
        return proxy if resp.status_code == 200 else None
    except Exception:
        return None

def get_working_proxies(num_workers=30):
    """下载并筛选可用代理，返回 list"""
    print("正在下载 Proxifly 代理列表...")
    all_proxies = download_proxy_list()
    print(f"共获取 {len(all_proxies)} 个代理，开始验证（最多测试 {MAX_PROXIES_TO_TEST} 个）...")
    
    test_candidates = all_proxies[:MAX_PROXIES_TO_TEST]
    working = []
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = {executor.submit(test_one_proxy, p): p for p in test_candidates}
        for future in as_completed(futures):
            result = future.result()
            if result:
                working.append(result)
            if len(working) % 10 == 0 and len(working) > 0:
                print(f"  已找到 {len(working)} 个可用代理...")
            if len(working) >= MAX_PROXIES_TO_TEST // 2:
                # 提前终止：已经找到足够多
                break
    print(f"验证完成，共 {len(working)} 个可用代理")
    return working

def create_scraper():
    """创建 cloudscraper 实例（不预设代理）"""
    return cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'mobile': False},
        delay=10
    )

def fetch_html(scraper, url, referer, proxy_pool=None, retries=3):
    """带代理轮换的请求函数"""
    headers = {
        'User-Agent': random.choice([
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        ]),
        'Referer': referer,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }

    for attempt in range(1, retries + 1):
        # 选择代理：有可用代理池则随机选取
        proxies = None
        if proxy_pool:
            proxy = random.choice(proxy_pool)
            proxies = {'http': proxy, 'https': proxy}
        
        try:
            if proxies:
                resp = scraper.get(url, headers=headers, proxies=proxies, timeout=25)
            else:
                resp = scraper.get(url, headers=headers, timeout=25)
            
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding
                return resp.text
            else:
                print(f"  {url} 状态码 {resp.status_code}，尝试 {attempt}/{retries}")
                # 如果使用了代理且失败，从池中移除（可选）
                if proxy_pool and proxies and resp.status_code in (403, 429, 502, 503):
                    try:
                        proxy_pool.remove(proxy)
                    except ValueError:
                        pass
        except Exception as e:
            print(f"  请求异常: {e}，尝试 {attempt}/{retries}")
            if proxy_pool and proxies:
                try:
                    proxy_pool.remove(proxy)
                except ValueError:
                    pass
        
        if attempt < retries:
            wait = 5 * attempt
            print(f"  等待 {wait} 秒后重试...")
            time.sleep(wait)
    return None

# ================= 以下解析函数保持不变 =================

def parse_ip_list(html):
    """解析列表页，提取 IP、地区、运营商"""
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

def parse_channel_page(html):
    """解析频道详情页，提取频道名和 m3u8 地址"""
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

def crawl_source(scraper, base_url, list_php, total_pages, output_file, proxy_pool):
    """抓取指定来源的所有频道，写入文件"""
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
        list_html = fetch_html(scraper, list_url, referer, proxy_pool)
        if not list_html:
            print(f"[{list_php}] 第 {page} 页获取失败，跳过")
            continue

        entries = parse_ip_list(list_html)
        print(f"[{list_php}] 第 {page} 页提取到 {len(entries)} 个有效条目")
        time.sleep(random.uniform(2, 4))

        for entry in entries:
            ip, tk, p = entry['ip'], entry['tk'], entry['p']
            region_isp = entry['region_isp']
            detail_url = f"{base_url}/getall26.php?ip={ip}&c=&tk={tk}&p={p}"
            channel_ref = f"{base_url}/channellist.html?ip={ip}&tk={tk}&p={p}"

            print(f"  [{list_php}] 抓取 {region_isp} 的频道...")
            detail_html = fetch_html(scraper, detail_url, channel_ref, proxy_pool, retries=2)
            if not detail_html:
                continue

            channels = parse_channel_page(detail_html)
            print(f"  [{list_php}] 获取到 {len(channels)} 个频道")
            if channels:
                all_lines.append(f"{region_isp},#genre#")
                for ch in channels:
                    all_lines.append(f"{ch['channel_name']},{ch['m3u8_url']}")
            time.sleep(random.uniform(0.8, 1.5))

    if all_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))
        print(f"[{list_php}] 完成！共写入 {len(all_lines)} 行至 {output_file}")
    else:
        print(f"[{list_php}] 未获取到有效数据，{output_file} 留空")

def run_crawler(total_pages=1):
    """主函数：获取代理池 -> 预热 cloudscraper -> 爬取两个源"""
    base_url = 'https://tonkiang.us'
    
    # 1. 获取并验证代理
    proxy_pool = get_working_proxies()
    if len(proxy_pool) < MIN_WORKING_PROXIES:
        print(f"警告：可用代理不足（{len(proxy_pool)} < {MIN_WORKING_PROXIES}），将混合使用代理与直连")
    else:
        print(f"使用 {len(proxy_pool)} 个代理进行爬取")
    
    # 2. 创建 cloudscraper（预热首页）
    scraper = create_scraper()
    try:
        scraper.get('https://tonkiang.us/', timeout=20)
        time.sleep(1)
    except Exception as e:
        print(f"预热首页异常（不影响后续）: {e}")
    
    # 3. 依次爬取
    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}
    ]
    for source in sources:
        print(f"\n开始抓取 {source['php']} ...")
        crawl_source(scraper, base_url, source['php'], total_pages, source['output'], proxy_pool)
        print(f"{source['php']} 抓取结束\n")

if __name__ == '__main__':
    pages = int(os.getenv('TOTAL_PAGES', 1))
    run_crawler(total_pages=pages)
