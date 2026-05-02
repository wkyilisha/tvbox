import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs
import re
import time
import random

# ================= 配置区 =================
# 填入你刚才部署成功的 Cloudflare Worker 地址
WORKER_URL = 'https://holy-wave-671a.824383214.workers.dev'
# ==========================================

def fetch_html(url, referer, headers=None):
    """通过 Worker 代理发送请求"""
    # 构造请求头，模拟真实浏览器
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache',
    }
    if headers:
        default_headers.update(headers)
    if referer:
        # 注意：这里的 Referer 最好也是经过代理的或者直接设为原站地址
        default_headers['Referer'] = referer.replace(WORKER_URL, 'https://tonkiang.us')

    try:
        # 设置较大的 timeout，因为 Worker 中转可能稍慢
        response = requests.get(url, headers=default_headers, timeout=20)
        response.raise_for_status()
        
        # 调试：如果在 GitHub Actions 运行，打印前 200 字看看是否被拦截
        # print(f"DEBUG: {url} -> {response.text[:200]}")
        
        if "安全验证" in response.text or "Checking your browser" in response.text:
            print(f"⚠️ 警告: {url} 触发了 Cloudflare 防火墙/人机验证，Worker 可能失效。")
            
        response.encoding = response.apparent_encoding
        return response.text
    except Exception as e:
        print(f"❌ 请求失败: {url}, 错误: {e}")
        return None

def parse_ip_list(html):
    """解析列表页"""
    if not html: return []
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
                match = re.match(r'(.+?)\s+((?:[\u4e00-\u9fa5]+)?(?:电信|联通|移动|广电|铁通|长宽|教育网))\s*$', geo_isp)
                if match:
                    location, isp = match.group(1).strip(), match.group(2).strip()
                else:
                    location = geo_isp

        entries.append({
            'ip': ip, 'tk': tk, 'p': p_val,
            'region_isp': f"{location} {isp}"
        })
    return entries

def parse_channel_page(html):
    """解析频道详情页"""
    if not html: return []
    soup = BeautifulSoup(html, 'html.parser')
    channels = []
    result_divs = soup.find_all('div', class_='result')

    for div in result_divs:
        channel_div = div.find('div', class_='channel')
        if not channel_div: continue
        
        tip_div = channel_div.find('div', class_='tip')
        channel_name = tip_div.get_text(strip=True) if tip_div else "未知频道"
        
        m3u8_div = div.find('div', class_='m3u8')
        if not m3u8_div: continue
        
        for td in m3u8_div.find_all('td'):
            text = td.get_text(strip=True)
            if text.startswith('http'):
                channels.append({'channel_name': channel_name, 'm3u8_url': text})
                break
    return channels

def crawl_source(base_url, list_php, total_pages, output_file):
    """抓取核心逻辑"""
    all_lines = []

    for page in range(1, total_pages + 1):
        # 构造 URL (通过 Worker 代理)
        if page == 1:
            list_url = f"{base_url}/{list_php}"
            referer = f"{base_url}/"
        else:
            list_url = f"{base_url}/{list_php}?page={page}&iphone16=&code="
            referer = f"{base_url}/{list_php}"

        print(f"🚀 正在抓取第 {page} 页: {list_url}")
        list_html = fetch_html(list_url, referer)
        entries = parse_ip_list(list_html)
        
        print(f"✅ 第 {page} 页提取到 {len(entries)} 个有效 IP 条目")
        
        # 增加随机延迟，防止 Worker 被目标站封禁
        time.sleep(random.uniform(1.5, 3.0))

        for entry in entries:
            ip, tk, p = entry['ip'], entry['tk'], entry['p']
            region_isp = entry['region_isp']
            
            # 详情页也必须走 Worker
            detail_url = f"{base_url}/getall26.php?ip={ip}&c=&tk={tk}&p={p}"
            channel_ref = f"{base_url}/channellist.html?ip={ip}&tk={tk}&p={p}"

            print(f"  🔍 正在解析: {region_isp} ({ip})")
            detail_html = fetch_html(detail_url, channel_ref)
            channels = parse_channel_page(detail_html)
            
            if channels:
                all_lines.append(f"{region_isp},#genre#")
                for ch in channels:
                    all_lines.append(f"{ch['channel_name']},{ch['m3u8_url']}")
                print(f"  ✨ 成功获取 {len(channels)} 个频道")
            
            time.sleep(random.uniform(0.5, 1.2))

    if all_lines:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(all_lines))
        print(f"🎉 任务完成！写入 {len(all_lines)} 行数据至 {output_file}")
    else:
        print(f"⚠️ 未获取到任何数据，请检查 Worker 是否可用。")

def run_crawler(total_pages=1):
    """入口"""
    # 只要这里填了 Worker 地址，后面所有的请求都会自动走 Worker 转发
    sources = [
        {'php': 'iptvhotelx.php', 'output': 'iptvhote.txt'},
        {'php': 'iptvproxy.php',  'output': 'iptvpmigu.txt'}
    ]

    for source in sources:
        print(f"\n--- 开始任务: {source['php']} ---")
        crawl_source(WORKER_URL, source['php'], total_pages, source['output'])

if __name__ == '__main__':
    # 建议在 GitHub Actions 先跑 1 页测试
    run_crawler(total_pages=1)
