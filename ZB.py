import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time

def fetch_html(url, referer, headers=None):
    """通用请求函数"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.8,en-US;q=0.5,en;q=0.3',
    }
    if headers:
        default_headers.update(headers)
    if referer:
        default_headers['Referer'] = referer

    try:
        response = requests.get(url, headers=default_headers, timeout=15)
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
        time.sleep(1)

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
            time.sleep(0.5)

    if all_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))
        print(f"[{list_php}] 完成！共写入 {len(all_lines)} 行至 {output_file}")
    else:
        print(f"[{list_php}] 未获取到有效数据，{output_file} 留空")

def run_crawler(total_pages=3):
    """主函数，依次爬取两个源，生成两个文件"""
    base_url = 'https://tonkiang.us'
    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},   # 您指定的文件名
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}   # 您指定的文件名
    ]

    for source in sources:
        print(f"\n开始抓取 {source['php']} ...")
        crawl_source(base_url, source['php'], total_pages, source['output'])
        print(f"{source['php']} 抓取结束\n")

if __name__ == '__main__':
    run_crawler(total_pages=3)  # 修改此处数字可同时调整两个源的抓取页数
