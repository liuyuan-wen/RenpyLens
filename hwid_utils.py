# -*- coding: utf-8 -*-
import uuid
import requests
import urllib3

# 禁用 urllib3 的证书警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def get_hwid():
    """获取机器的唯一物理标识 (UUID/MAC)"""
    # 使用 uuid.getnode() 获取 MAC 地址的 48 位整数形式，并转为十六进制字符串
    node = uuid.getnode()
    hwid = ':'.join(['{:02x}'.format((node >> i) & 0xff) for i in range(0, 48, 8)][::-1])
    return hwid

def register_trial_key(hwid, trial_key_url):
    """向服务器申请试用 Key"""
    payload = {
        "hwid": hwid
    }
    
    print(f"Requesting trial Key from {trial_key_url}...")
    
    try:
        # 忽略自签名证书校验
        response = requests.post(trial_key_url, json=payload, timeout=10, verify=False)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('status') == 'success' or 'key' in data:
                print(f"✅ Server response success! 🔑 Key: {data.get('key')}")
                return data.get('key')
            else:
                print(f"❌ Server response error: {data.get('message')}")
        else:
            print(f"❌ Server returned error code: {response.status_code}, details: {response.text}")

    except Exception as e:
        print(f"🚨 Network request failed: {e}")
    
    return None
