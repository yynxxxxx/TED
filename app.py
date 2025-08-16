from flask import Flask, render_template, request, jsonify, session, send_from_directory
import requests
import hashlib
import re
import json
import random
import string
import time
import warnings
import os
import asyncio
import aiohttp
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import calendar
import math

# 禁用SSL警告
warnings.filterwarnings("ignore", category=UserWarning)

app = Flask(__name__)
app.secret_key = 'iyuba_vip_helper_secret_key_2024'

# 全局任务进度存储，避免在后台线程写 session
# 结构: { task_id: { total_requests, successful_requests, failed_requests,
#                    completed_requests, latest_vip_end_time, retry_count,
#                    completed, started, progress_percent } }
progress_store = {}
progress_store_lock = threading.Lock()

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

async def send_vip_request_async(session, uid, request_id):
    """异步发送VIP充值请求，3秒超时"""
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
        # 设置3秒超时
        timeout = aiohttp.ClientTimeout(total=3)
        async with session.get(url, params=params, headers=headers, ssl=False, timeout=timeout) as response:
            response_text = await response.text()
            return {
                'request_id': request_id,
                'status_code': response.status,
                'response_text': response_text,
                'success': response.status == 200
            }
    except asyncio.TimeoutError:
        return {
            'request_id': request_id,
            'status_code': None,
            'response_text': None,
            'success': False,
            'error': '请求超时(3秒)'
        }
    except Exception as e:
        return {
            'request_id': request_id,
            'status_code': None,
            'response_text': None,
            'success': False,
            'error': str(e)
        }

def add_months(base_dt: datetime, months: int) -> datetime:
    """在日历意义上为日期增加月份，保持日对齐；若目标月无此日，则取该月最后一天。"""
    year = base_dt.year + (base_dt.month - 1 + months) // 12
    month = (base_dt.month - 1 + months) % 12 + 1
    day = base_dt.day
    last_day = calendar.monthrange(year, month)[1]
    day = min(day, last_day)
    return base_dt.replace(year=year, month=month, day=day)

async def batch_vip_requests(uid, total_requests, batch_size=10, max_retries=3, progress_callback=None):
    """批量异步发送VIP充值请求，每批10个，等待本批完全处理完（包括重试）后再进行下一批"""
    successful_requests = 0
    latest_vip_end_time = None
    processed_batches = 0  # 已处理完成的批次数
    retry_count = 0
    
    def update_progress():
        """更新进度"""
        if progress_callback:
            # completed_requests = 已完成批次数 * 10 + 当前批次的进度
            completed_requests = processed_batches * batch_size + min(batch_size, total_requests - processed_batches * batch_size) if processed_batches < (total_requests + batch_size - 1) // batch_size else total_requests
            progress_callback(completed_requests, successful_requests, total_requests, latest_vip_end_time)
    
    # 创建SSL上下文，忽略证书验证
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=30)
    
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # 按批次处理，每批10个请求
        batch_size = 10  # 固定并发为10
        
        for batch_start in range(0, total_requests, batch_size):
            batch_end = min(batch_start + batch_size, total_requests)
            current_batch_size = batch_end - batch_start
            
            print(f"开始处理第 {batch_start//batch_size + 1} 批，共 {current_batch_size} 个请求")
            
            # 当前批次需要成功的请求数
            batch_successful = 0
            batch_attempts = 0
            
            # 对当前批次进行重试，直到全部成功或达到最大重试次数
            while batch_successful < current_batch_size and batch_attempts <= max_retries:
                batch_attempts += 1
                if batch_attempts > 1:
                    retry_count += 1
                    print(f"第 {batch_start//batch_size + 1} 批进行第 {batch_attempts-1} 次重试")
                
                # 需要发送的请求数 = 当前批次大小 - 已成功数
                need_requests = current_batch_size - batch_successful
                batch_tasks = []
                
                # 创建当前需要的任务
                for j in range(need_requests):
                    task = send_vip_request_async(session, uid, batch_start + j + 1)
                    batch_tasks.append(task)
                
                # 并发执行当前批次
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                # 处理批次结果
                batch_current_success = 0
                for result in batch_results:
                    if isinstance(result, dict):
                        if result.get('success') and result.get('status_code') == 200:
                            try:
                                response_data = json.loads(result.get('response_text', '{}'))
                                if response_data.get('result') == 200:
                                    batch_current_success += 1
                                    successful_requests += 1
                                    # 记录最新的VIP到期时间
                                    if 'vipEndTime' in response_data:
                                        latest_vip_end_time = response_data['vipEndTime']
                            except json.JSONDecodeError:
                                pass
                
                batch_successful += batch_current_success
                print(f"第 {batch_start//batch_size + 1} 批第 {batch_attempts} 次尝试：成功 {batch_current_success}/{need_requests}，累计成功 {batch_successful}/{current_batch_size}")
                
                # 如果本批次未全部成功且还可以重试，稍作延迟
                if batch_successful < current_batch_size and batch_attempts <= max_retries:
                    await asyncio.sleep(0.5)
            
            print(f"第 {batch_start//batch_size + 1} 批处理完成：最终成功 {batch_successful}/{current_batch_size}")
            
            # 批次完成，更新已处理批次数
            processed_batches += 1
            update_progress()
            
            # 批次间稍作延迟
            if batch_end < total_requests:
                await asyncio.sleep(0.3)
    
    failed_requests = total_requests - successful_requests
    
    return {
        'successful_requests': successful_requests,
        'failed_requests': failed_requests,
        'total_requests': total_requests,
        'latest_vip_end_time': latest_vip_end_time,
        'retry_count': retry_count
    }

@app.route('/')
def index():
    """主页"""
    return render_template('index.html')

@app.route('/public/<filename>')
def serve_public_file(filename):
    """提供public文件夹中的静态文件"""
    return send_from_directory('public', filename)

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

@app.route('/recharge_async', methods=['POST'])
def recharge_async():
    """异步批量充值"""
    if 'uid' not in session:
        return jsonify({'success': False, 'message': '请先登录'})
    
    data = request.get_json()
    try:
        months = int(data.get('months', 0))
        if months <= 0:
            return jsonify({'success': False, 'message': '充值月数必须大于0'})
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': '请输入有效的数字'})
    
    uid = session['uid']
    # 计算按自然月份精确的目标到期日
    now_dt = datetime.now()
    target_dt = add_months(now_dt, months)

    # 目标到期日为目标日期当天的00:00之后。为了确保至少覆盖到目标当天，把目标对齐到目标日的23:59:59
    target_dt = target_dt.replace(hour=23, minute=59, second=59, microsecond=0)

    # 每个请求充值3天 => 按天数精确换算需要的请求次数
    delta_days = (target_dt.date() - now_dt.date()).days
    # 确保非负
    delta_days = max(delta_days, 0)
    total_requests = math.ceil(delta_days / 3) if delta_days > 0 else 0
    # 至少也要发一次请，否则0个月会有0；但这里months>=1才会进来
    total_requests = max(total_requests, months * 10)  # 与旧规则兼容的下限

    batch_size = 10  # 固定并发为10

    # 为本次任务生成 task_id，前端用它轮询状态
    task_id = ''.join(random.choice(string.ascii_letters + string.digits) for _ in range(16))
    
    def run_async_recharge():
        """在新线程中运行异步充值"""
        try:
            # 创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # 进度回调函数（写入全局进度存储）
            def progress_callback(completed, successful, total, vip_time):
                with progress_store_lock:
                    task = progress_store.get(task_id)
                    if task is None:
                        return
                    task['completed_requests'] = completed
                    task['successful_requests'] = successful
                    task['latest_vip_end_time'] = vip_time
                    task['progress_percent'] = (completed / total * 100) if total > 0 else 0
            
            # 执行异步充值
            result = loop.run_until_complete(batch_vip_requests(uid, total_requests, batch_size, max_retries=3, progress_callback=progress_callback))
            
            # 如果失败请求过多，尝试额外补充请求以达到目标月数
            target_successful = total_requests
            if result['failed_requests'] > 0 and result['successful_requests'] < target_successful:
                # 计算需要补充的请求数
                additional_needed = target_successful - result['successful_requests']
                if additional_needed > 0:
                    print(f"需要补充{additional_needed}个请求以达到目标")
                    
                    # 执行补充请求
                    additional_result = loop.run_until_complete(
                        batch_vip_requests(uid, additional_needed, min(batch_size, 5), max_retries=2)
                    )
                    
                    # 合并结果
                    result['successful_requests'] += additional_result['successful_requests']
                    result['failed_requests'] += additional_result['failed_requests'] 
                    result['total_requests'] += additional_result['total_requests']
                    result['retry_count'] += additional_result['retry_count']
                    if additional_result['latest_vip_end_time']:
                        result['latest_vip_end_time'] = additional_result['latest_vip_end_time']
            
            # 保存结果到全局任务存储
            with progress_store_lock:
                progress_store[task_id].update({
                    'total_requests': result['total_requests'],
                    'successful_requests': result['successful_requests'],
                    'failed_requests': result['failed_requests'],
                    'latest_vip_end_time': result['latest_vip_end_time'],
                    'retry_count': result['retry_count'],
                    'completed': True,
                    'target_requests': target_successful,
                    'target_datetime': target_dt.strftime('%Y-%m-%d %H:%M:%S')
                })
            
            loop.close()
            
        except Exception as e:
            with progress_store_lock:
                progress_store[task_id].update({
                    'error': str(e),
                    'completed': True
                })
    
    # 初始化任务进度（存入全局存储）
    with progress_store_lock:
        progress_store[task_id] = {
            'total_requests': total_requests,
            'successful_requests': 0,
            'failed_requests': 0,
            'completed_requests': 0,
            'progress_percent': 0,
            'latest_vip_end_time': None,
            'retry_count': 0,
            'completed': False,
            'started': True
        }
    
    # 在后台线程中执行异步充值
    thread = threading.Thread(target=run_async_recharge)
    thread.daemon = True
    thread.start()
    
    return jsonify({
        'success': True,
        'message': f'开始异步充值 {months} 个月，共 {total_requests} 次请求',
        'total_requests': total_requests,
        'batch_size': batch_size,
        'task_id': task_id
    })

@app.route('/recharge_status', methods=['GET'])
def recharge_status():
    """获取异步充值状态（通过task_id读取全局存储）"""
    if 'uid' not in session:
        return jsonify({'success': False, 'message': '请先登录'})

    task_id = request.args.get('task_id', '').strip()
    if not task_id:
        return jsonify({'success': False, 'message': '缺少task_id'})

    with progress_store_lock:
        result = progress_store.get(task_id)

    if not result:
        return jsonify({'success': False, 'message': '无此任务或任务已过期'})

    return jsonify({
        'success': True,
        'result': result
    })

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