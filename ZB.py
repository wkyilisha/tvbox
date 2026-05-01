import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import random

def fetch_html(url, referer, headers=None):
    """通用请求函数 - 增加更友好的请求间隔"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
    }
    if headers:
        default_headers.update(headers)
    if referer:
        default_headers['Referer'] = referer

    try:
        response = requests.get(url, headers=default_headers, timeout=20)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"请求失败: {url}, 错误: {e}")
        return None


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
            # 提取地区和运营商
            parts = re.split(r'\d{2}:\d{2}上线\s*', info_text)
            if len(parts) > 1:
                geo_isp = parts[-1].strip()
                match = re.match(
                    r'(.+?)\s+((?:[\u4e00-\u9fa5]+)?(?:电信|联通|移动|广电|铁通|长宽|教育网|鹏博士))?\s*$',
                    geo_isp
                )
                if match:
                    location = match.group(1).strip()
                    isp = match.group(2).strip() if match.group(2) else '未知运营商'
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
    """解析频道详情页"""
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


def crawl_source(base_url, list_php, total_pages, output_file, max_ip_per_page=3):
    """抓取指定来源 - 每页只取前 max_ip_per_page 个有效IP"""
    list_base = f'{base_url}/{list_php}'
    all_lines = []

    for page in range(1, total_pages + 1):
        if page == 1:
            list_url = list_base
            referer = base_url + '/'
        else:
            list_url = f'{list_base}?page={page}&iphone16=&code='
            referer = f'{list_base}?page={page-1}&iphone16=&code=' if page > 1 else list_base

        print(f"[{list_php}] 正在抓取第 {page} 页: {list_url}")
        list_html = fetch_html(list_url, referer)
        
        if not list_html:
            print(f"[{list_php}] 第 {page} 页请求失败")
            time.sleep(3)
            continue

        entries = parse_ip_list(list_html)
        print(f"[{list_php}] 第 {page} 页提取到 {len(entries)} 个有效条目")

        # === 关键修改：每页只取前3个有效IP ===
        entries_to_crawl = entries[:max_ip_per_page]
        print(f"[{list_php}] 本页计划抓取前 {len(entries_to_crawl)} 个IP")

        # 增加页面间等待时间
        if page > 1:
            wait_time = random.uniform(4, 7)
            print(f"[{list_php}] 页面等待 {wait_time:.1f} 秒...")
            time.sleep(wait_time)

        for i, entry in enumerate(entries_to_crawl, 1):
            ip, tk, p = entry['ip'], entry['tk'], entry['p']
            region_isp = entry['region_isp']
            
            detail_url = f"{base_url}/getall26.php?ip={ip}&c=&tk={tk}&p={p}"
            channel_ref = f"{base_url}/channellist.html?ip={ip}&tk={tk}&p={p}"

            print(f"  [{list_php}] [{i}/{len(entries_to_crawl)}] 抓取 {region_isp} 的频道...")
            
            detail_html = fetch_html(detail_url, channel_ref)
            
            if not detail_html:
                print(f"    → 详情页请求失败")
                time.sleep(2)
                continue

            channels = parse_channel_page(detail_html)
            print(f"    → 获取到 {len(channels)} 个频道")

            if channels:
                all_lines.append(f"{region_isp},#genre#")
                for ch in channels:
                    all_lines.append(f"{ch['channel_name']},{ch['m3u8_url']}")
                print(f"    → 已添加 {len(channels)} 个频道")

            # 每个IP抓取后增加较长等待，避免被封
            wait_time = random.uniform(3.5, 6.5)
            time.sleep(wait_time)

    # 写入文件
    if all_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))
        print(f"\n[{list_php}] 抓取完成！共写入 {len(all_lines)} 行 → {output_file}")
    else:
        print(f"\n[{list_php}] 未获取到有效数据")


def run_crawler(total_pages=1):
    """主函数"""
    base_url = 'https://tonkiang.us'
    
    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}
    ]

    for source in sources:
        print(f"\n{'='*60}")
        print(f"开始抓取: {source['php']}")
        print(f"{'='*60}")
        
        crawl_source(
            base_url=base_url,
            list_php=source['php'],
            total_pages=total_pages,
            output_file=source['output'],
            max_ip_per_page=3          # 每页最多抓取3个IP
        )
        
        # 两个源之间增加较长等待
        if source != sources[-1]:
            print(f"\n两个源之间等待10-15秒...\n")
            time.sleep(random.uniform(10, 15))


if __name__ == '__main__':
    run_crawler(total_pages=1)   # 你可以改成2或3，但建议先用1测试
