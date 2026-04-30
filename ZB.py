import cloudscraper
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import random
import os

# 可选 User-Agent 池，但 cloudscraper 会自动覆盖大部分场景，保留备用
USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
]

def create_scraper():
    """创建 cloudscraper 实例，模拟 Chrome 浏览器绕过 Cloudflare"""
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'mobile': False
        },
        delay=10  # 遇到 challenge 时等待时间
    )
    # 先预热，访问首页获取 cookies
    try:
        scraper.get('https://tonkiang.us/', timeout=20)
        time.sleep(1)
    except Exception as e:
        print(f"预热首页异常（不影响后续）: {e}")
    return scraper

def fetch_html(scraper, url, referer, retries=3):
    """带重试的请求函数，返回 HTML 文本"""
    headers = {
        'User-Agent': random.choice(USER_AGENTS),
        'Referer': referer,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9',
    }
    for attempt in range(1, retries + 1):
        try:
            resp = scraper.get(url, headers=headers, timeout=25)
            if resp.status_code == 200:
                resp.encoding = resp.apparent_encoding
                return resp.text
            else:
                print(f"  {url} 返回状态码 {resp.status_code}，第 {attempt}/{retries} 次")
        except Exception as e:
            print(f"  请求异常: {e}，第 {attempt}/{retries} 次")
        if attempt < retries:
            wait = 5 * attempt
            print(f"  等待 {wait} 秒后重试...")
            time.sleep(wait)
    return None

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

def crawl_source(scraper, base_url, list_php, total_pages, output_file):
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
        list_html = fetch_html(scraper, list_url, referer)
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
            detail_html = fetch_html(scraper, detail_url, channel_ref, retries=2)
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
    """主函数，使用 cloudscraper 爬取两个源"""
    base_url = 'https://tonkiang.us'
    scraper = create_scraper()

    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}
    ]

    for source in sources:
        print(f"\n开始抓取 {source['php']} ...")
        crawl_source(scraper, base_url, source['php'], total_pages, source['output'])
        print(f"{source['php']} 抓取结束\n")

if __name__ == '__main__':
    pages = int(os.getenv('TOTAL_PAGES', 1))
    run_crawler(total_pages=pages)
