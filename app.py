from flask import Flask, render_template, request, jsonify, session
import requests
import hashlib
import re
import json
import random
import string
import time
import warnings
import os

# 禁用SSL警告
warnings.filterwarnings("ignore", category=UserWarning)

app = Flask(__name__)
app.secret_key = 'iyuba_vip_helper_secret_key_2024'

def get_md5(text):
    """将输入转换为MD5"""
    return hashlib.md5(text.encode('utf-8')).hexdigest()

def generate_random_wxid():
    """生成随机wxid，长度为22位"""
    chars = string.ascii_letters + string.digits
    return ''.join(random.choice(chars) for _ in range(22))

def extract_sign(response_text):
    """从响应文本中提取sign"""
    match = re.search(r'sign result:([a-f0-9]{32})', response_text)
    if match:
        return match.group(1)
    return None

def extract_uid(response_text):
    """从JSON响应中提取UID"""
    try:
        response_json = json.loads(response_text)
        uid = response_json.get('uid')
        return uid
    except json.JSONDecodeError:
        return None

def send_iyuba_request(username, password_md5, sign=""):
    """发送爱语吧登录请求"""
    url = "http://api.iyuba.com.cn/v2/api.iyuba"
    
    params = {
        "protocol": "11001",
        "username": username,
        "password": password_md5,
        "x": "0",
        "y": "0",
        "appid": "240",
        "sign": sign,
        "format": "json"
    }
    
    headers = {
        "Host": "api.iyuba.com.cn",
        "Connection": "Keep-Alive",
        "Accept-Encoding": "gzip",
        "User-Agent": "okhttp/3.14.9"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers)
        return response
    except requests.exceptions.RequestException:
        return None

def send_vip_request_simple(uid):
    """发送VIP充值请求"""
    url = "https://apps.iyuba.cn/iyuba/openVipApplet.jsp"
    random_wxid = generate_random_wxid()
    
    params = {
        "uid": uid,
        "appid": "240",
        "wxid": random_wxid
    }
    
    headers = {
        "Host": "apps.iyuba.cn",
        "Connection": "keep-alive",
        "content-type": "application/json",
        "charset": "utf-8",
        "Referer": "https://servicewechat.com/wxe79692fc76516ce2/85/page-frame.html",
        "User-Agent": "Mozilla/5.0 (Linux; Android 14; 22081212C Build/UKQ1.230917.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/130.0.6723.103 Mobile Safari/537.36 XWEB/1300409 MMWEBSDK/20241202 MMWEBID/353 MicroMessenger/8.0.56.2800(0x28003833) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android",
        "Accept-Encoding": "gzip, deflate, br"
    }
    
    try:
        response = requests.get(url, params=params, headers=headers, verify=False)
        return response
    except requests.exceptions.RequestException:
        return None

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/login', methods=['POST'])
def login():
    """处理登录请求"""
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '').strip()
    
    if not username or not password:
        return jsonify({'success': False, 'message': '请输入手机号和密码'})
    
    try:
        # 第一次请求获取sign
        first_response = send_iyuba_request(username, get_md5(password))
        if not first_response or first_response.status_code != 200:
            return jsonify({'success': False, 'message': '第一次请求失败'})
            
        sign = extract_sign(first_response.text)
        if not sign:
            return jsonify({'success': False, 'message': '获取sign失败'})
        
        # 第二次请求获取UID
        second_response = send_iyuba_request(username, get_md5(password), sign)
        if not second_response or second_response.status_code != 200:
            return jsonify({'success': False, 'message': '第二次请求失败'})
            
        uid = extract_uid(second_response.text)
        if not uid:
            return jsonify({'success': False, 'message': '获取UID失败'})
        
        # 保存到session
        session['uid'] = uid
        session['username'] = username
        
        return jsonify({
            'success': True, 
            'message': f'登录成功！UID: {uid}',
            'uid': uid
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'登录异常: {str(e)}'})

@app.route('/start_recharge', methods=['POST'])
def start_recharge():
    """开始充值，返回总数"""
    if 'uid' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.get_json()
    try:
        months = int(data.get('months', 0))
        if months <= 0:
            return jsonify({'success': False, 'message': '充值月数必须大于0'})
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': '请输入有效的数字'})
    
    total_requests = months * 10
    
    # 保存到session
    session['total_requests'] = total_requests
    session['current_request'] = 0
    session['successful_requests'] = 0
    session['latest_vip_end_time'] = None
    
    return jsonify({
        'success': True,
        'total_requests': total_requests,
        'months': months
    })

@app.route('/recharge_single', methods=['POST'])
def recharge_single():
    """执行单次充值请求"""
    if 'uid' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    
    if 'total_requests' not in session:
        return jsonify({'success': False, 'message': '请先开始充值'})
    
    uid = session['uid']
    current_request = session.get('current_request', 0)
    total_requests = session.get('total_requests', 0)
    successful_requests = session.get('successful_requests', 0)
    
    if current_request >= total_requests:
        return jsonify({'success': False, 'message': '充值已完成'})
    
    try:
        response = send_vip_request_simple(uid)
        current_request += 1
        session['current_request'] = current_request
        
        success = False
        vip_end_time = None
        message = ""
        
        if response and response.status_code == 200:
            try:
                response_data = response.json()
                if response_data.get('result') == 200:
                    success = True
                    successful_requests += 1
                    session['successful_requests'] = successful_requests
                    
                    # 记录最新的VIP到期时间
                    if 'vipEndTime' in response_data:
                        vip_end_time = response_data['vipEndTime']
                        session['latest_vip_end_time'] = vip_end_time
                    
                    message = f"充值成功 ({successful_requests}/{total_requests})"
                    if vip_end_time:
                        message += f" | 到期时间: {vip_end_time}"
                else:
                    message = f"充值失败 ({current_request}/{total_requests}) | {response_data.get('msg', '未知错误')}"
            except json.JSONDecodeError:
                message = f"响应解析错误 ({current_request}/{total_requests})"
        else:
            message = f"请求失败 ({current_request}/{total_requests}) | 状态码: {response.status_code if response else 'None'}"
        
        is_completed = current_request >= total_requests
        
        return jsonify({
            'success': True,
            'request_success': success,
            'message': message,
            'current': current_request,
            'total': total_requests,
            'successful': successful_requests,
            'is_completed': is_completed,
            'vip_end_time': vip_end_time or session.get('latest_vip_end_time')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'请求异常: {str(e)}'})

@app.route('/logout', methods=['POST'])
def logout():
    """退出登录"""
    session.clear()
    return jsonify({'success': True, 'message': '已退出登录'})

@app.route('/random_bg')
def random_bg():
    """获取随机背景图片"""
    try:
        # 获取public文件夹中的图片文件
        public_folder = os.path.join(os.path.dirname(__file__), 'public')
        
        if not os.path.exists(public_folder):
            return jsonify({'success': False, 'message': 'public文件夹不存在'})
        
        # 支持的图片格式
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        image_files = []
        
        # 遍历public文件夹找图片文件
        for filename in os.listdir(public_folder):
            if any(filename.lower().endswith(ext) for ext in image_extensions):
                image_files.append(filename)
        
        if not image_files:
            return jsonify({'success': False, 'message': 'public文件夹中没有找到图片文件'})
        
        # 随机选择一张图片
        selected_image = random.choice(image_files)
        image_url = f'/public/{selected_image}'
        
        return jsonify({'success': True, 'image_url': image_url})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'获取背景图片异常: {str(e)}'})

# Vercel需要这个变量
app_instance = app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)