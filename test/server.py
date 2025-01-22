from flask import Flask, request, make_response, jsonify

app = Flask(__name__)

# 预设的cookie键名和期望的值
COOKIE_KEY = 'user_token'
EXPECTED_VALUE = 'secret_value'

@app.route('/')
def index():
    # 获取cookie中的'user_token'值
    user_token = request.cookies.get(COOKIE_KEY)
    print(request.cookies)

    if user_token is None:
        # 如果没有cookie值，设定默认值
        response = make_response("Cookie not found. Setting default value.")
        response.set_cookie(COOKIE_KEY, EXPECTED_VALUE)  # 设置cookie
        return response
    elif user_token == EXPECTED_VALUE:
        # 如果cookie值匹配预期值
        return jsonify({"message": "Cookie is valid!"})
    else:
        # 如果cookie值不匹配
        return jsonify({"message": "Invalid cookie value!"}), 400

if __name__ == '__main__':
    app.run(debug=True,port=5000)
