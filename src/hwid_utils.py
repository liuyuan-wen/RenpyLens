# -*- coding: utf-8 -*-
import uuid
import urllib.request
import json
import ssl

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
        # Ignore self-signed cert verification
        context = ssl._create_unverified_context()
        
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(trial_key_url, data=data, headers={'Content-Type': 'application/json'})
        
        with urllib.request.urlopen(req, timeout=10.0, context=context) as response:
            if response.status == 200:
                resp_data = json.loads(response.read().decode('utf-8'))
                if resp_data.get('status') == 'success' or 'key' in resp_data:
                    print(f"✅ Server response success! 🔑 Key: {resp_data.get('key')}")
                    return resp_data.get('key')
                else:
                    print(f"❌ Server response error: {resp_data.get('message')}")
            else:
                print(f"❌ Server returned error code: {response.status}, details: {response.read().decode('utf-8')}")

    except Exception as e:
        print(f"🚨 Network request failed: {e}")
    
    return None
