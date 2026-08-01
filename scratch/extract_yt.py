import requests
import re
import html

url = 'https://www.youtube.com/watch?v=-TqA864nVHA'
headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9'
}

print('Fetching watch page...')
response = requests.get(url, headers=headers)
content = response.text

# Try to find video title in various places
title_match = re.search(r'<title>(.*?)</title>', content)
title_text = html.unescape(title_match.group(1)) if title_match else 'Not found'
print('Title:', title_text)

# Try og:description or meta description
og_desc = re.search(r'<meta name=\"description\" content=\"(.*?)\"', content)
if og_desc:
    print('Meta Description:', html.unescape(og_desc.group(1)))

# Try shortDescription
desc_match = re.search(r'\"shortDescription\":\"(.*?)\"', content)
if desc_match:
    desc_text = desc_match.group(1)
    # Print first 1000 characters of raw description to avoid unicode decode errors
    print('Raw Description:', desc_text[:1000])
