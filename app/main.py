from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, Response
import requests
import os
import urllib.parse
from cachetools import LRUCache, TTLCache
from typing import Any
import httpx
import logging
import sys
import re

cache = TTLCache(maxsize=100, ttl=5 * 60)
app = FastAPI()

# 日志设置保持不变
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# 添加反向代理相关的头处理
def get_base_url(request: Request) -> str:
    # 检查 X-Forwarded-* 头，这是反向代理常用的
    forwarded_proto = request.headers.get('X-Forwarded-Proto', request.url.scheme)
    forwarded_host = request.headers.get('X-Forwarded-Host', request.url.hostname)
    forwarded_port = request.headers.get('X-Forwarded-Port', request.url.port)
    
    # 如果没有指定端口，使用协议默认端口
    port_str = f":{forwarded_port}" if forwarded_port and forwarded_port not in ['80', '443'] else ""
    return f"{forwarded_proto}://{forwarded_host}{port_str}"

@app.get('/image')
async def proxy_request(request: Request):
    query_string = request.url.query
    query_params = urllib.parse.parse_qs(query_string)
    url_param = query_params.get('url', [''])[0]
    
    cached_response = cache.get(url_param)
    if cached_response is not None:
        headers, content = cached_response
        return StreamingResponse(iter([content]), status_code=200, headers=headers)

    referer_env = os.environ.get('DEFAULT_REFERER', '')
    referer_param = query_params.get('referer', [referer_env])[0]

    logger.info(f"Proxying request to URL: {url_param}")
    logger.info(f"Referer: {referer_param}")

    user_agent_env = os.environ.get('USER_AGENT_HEADER')
    user_agent_header = user_agent_env if user_agent_env else request.headers.get('user-agent', '')
    
    proxy_uri = os.environ.get('PROXY_URI', None)

    async with httpx.AsyncClient(proxy=proxy_uri) as client:
        response = await client.get(url_param, headers={'referer': referer_param, 'user-agent': user_agent_header})
        response_content = response.content
        content_length = len(response_content)
        
        headers = response.headers.copy()
        headers['Content-Length'] = str(content_length)
        if response.status_code == 200:
            cache[url_param] = (headers, response_content)
    
        return StreamingResponse(
            iter([response_content]),
            status_code=response.status_code,
            headers=headers,
        )

websiteInstances = [
    "http://rsshub:1200"
]

IMAGE_PROXY_KEY = "_image_proxy"
IMAGE_PROXY_REFERER_KEY = "_image_proxy_referer"

async def forward_request(request: Request, path: str):
    proxy_uri = os.environ.get('PROXY_URI', None)
    if not path.startswith('/'):
        path = f"/{path}"
    query_params = request.query_params
    base_url = get_base_url(request)
    
    async with httpx.AsyncClient(proxy=proxy_uri) as client:
        for website in websiteInstances:
            full_url = f"{website}{path}"
            if query_params:
                full_url += '?' + '&'.join([f"{k}={v}" for k, v in query_params.items()])
            try:
                response = await client.get(full_url)
                if response.status_code == 200:
                    content = str(response.content.decode('utf-8'))
                    if IMAGE_PROXY_KEY in query_params.keys():
                        content = replace_img_with_template(content, base_url, query_params.get(IMAGE_PROXY_REFERER_KEY, ''))
                    return Response(
                        content=content,
                        media_type=response.headers["content-type"],
                    )
            except Exception as e:
                logger.error(f"Error occurred while requesting {full_url}, err: {str(e)}")
            else:
                if response.status_code != 200:
                    logger.info(f"Non-200 status code ({response.status_code}) from {full_url}")

        return None

@app.get("/rsshub/{path:path}")
async def rsshub_handler(request: Request, path: str):
    result = await forward_request(request, path)
    if result is not None:
        return result
    else:
        return {"error": "No website instance returned a 200 status code"}

def replace_img_with_template(content: str, base_url: str, referer: str) -> bytes:
    pattern = r'<img\s+src="([^"]+)"'
    pattern2 = r'&lt;img\s+src=\&quot;(.+?)\&quot;'
    proxy_img_template = f"{base_url}/image?url=\\1&referer={referer}"
    text1 = re.sub(pattern, f'<img src="{proxy_img_template}"', content)
    text2 = re.sub(pattern2, f'&lt;img src=&quot;{proxy_img_template.replace("&", "&amp;")}&quot;', text1)
    return text2.encode('utf-8')

if __name__ == '__main__':
    import uvicorn
    PORT = int(os.environ.get('PORT', 8080))  # 添加环境变量支持
    uvicorn.run(app, host='0.0.0.0', port=PORT)