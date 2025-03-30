from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse,Response
import requests
import os
import urllib.parse
from cachetools import LRUCache, TTLCache
from typing import Any
import httpx
import logging
import sys
import re


# 创建一个LRU缓存实例，例如：缓存100个最近使用的条目，并且每个条目在5分钟内有效
cache = TTLCache(maxsize=100, ttl=5 * 60)
app = FastAPI()


# 设置日志格式，包含文件名、行号和方法名
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s')
# 创建一个stream handler并设置格式
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(formatter)

# 获取logger并设置日志级别和handler
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logger.addHandler(handler)

@app.get('/image')
async def proxy_request(request: Request):
    query_string = request.url.query
    # 解码原始查询字符串以正确处理转义的 &amp;
    query_params = urllib.parse.parse_qs(query_string)

    url_param = query_params.get('url', [''])[0]
    # 尝试从缓存中获取结果
    cached_response = cache.get(url_param)
    if cached_response is not None:
        headers, content = cached_response
        return StreamingResponse(iter([content]), status_code=200, headers=headers)

    referer_env = os.environ.get('DEFAULT_REFERER', '')
    referer_param = query_params.get('referer', [referer_env])[0]

    logger.info(f"Proxying request to URL: {url_param}")
    logger.info(f"Referer: {referer_param}")

    user_agent_env = os.environ.get('USER_AGENT_HEADER')
    if user_agent_env is None:
        user_agent_header = request.headers.get('user-agent', '')
    else:
        user_agent_header = user_agent_env

    proxy_uri = os.environ.get('PROXY_URI', None)

    async with httpx.AsyncClient(proxy=proxy_uri) as client:
        response = await client.get(url_param, headers={'referer': referer_param, 'user-agent': user_agent_header})
        # 确保完整读取response.content
        response_content = response.content
        content_length = len(response_content)
        
        headers = response.headers.copy()
        headers['Content-Length'] = str(content_length)  # 更新Content-Length为实际长度
        # 只缓存HTTP状态码为200的响应
        if response.status_code == 200:
            cache[url_param] = (headers, response_content)
    
        return StreamingResponse(
            iter([response_content]),
            status_code=response.status_code,
            headers=headers,
        )

# 假设你有一系列网站实例
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
                        content = replace_img_with_template(content, f"{request.url.hostname}:{request.url.port}", query_params.get(IMAGE_PROXY_REFERER_KEY, ''))
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


def replace_img_with_template(content: str, host: str, referer) -> bytes:
    pattern = r'<img\s+src="([^"]+)"'
    pattern2 = r'&lt;img\s+src=\&quot;(.+?)\&quot;'
    proxy_img_template = "http://{host}/image?url=\\1&referer={referer}".format(host=host, referer=referer)
    text1 = re.sub(pattern, f'<img src="{proxy_img_template}"', content)
    text2 = re.sub(pattern2, f'&lt;img src=&quot;{proxy_img_template.replace("&", "&amp;")}&quot;', text1)
    return text2.encode('utf-8')


if __name__ == '__main__':
    import uvicorn
    PORT = 8080
    uvicorn.run(app, host='0.0.0.0', port=PORT)