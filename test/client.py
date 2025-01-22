import requests
from client.session_requester import Client as t

# Flask 后端的 URL
url = 'http://127.0.0.1:5000/'

# 创建一个 Session 对象以保持持久化的 cookie
client = t()

# 访问 Flask 后端并打印返回的内容
def check_cookie():
    response = client.get(url)
    
    # 打印响应的内容和状态码
    print(response.content)
    print(response.cookies.items())
    print(f"Response: {response.json()}")
    print(f"Status Code: {response.status_code}")
    
    # 打印 session 中的 cookie，验证持久化
    cookies = client.session.cookies.get_dict()
    print(f"Cookies: {cookies}")

if __name__ == "__main__":
    check_cookie()
