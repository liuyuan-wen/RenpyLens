# -*- coding: utf-8 -*-
from datetime import datetime
import json
import ssl
import urllib.request
import uuid


def get_hwid():
    """获取机器的唯一物理标识 (UUID/MAC)"""
    node = uuid.getnode()
    hwid = ":".join(["{:02x}".format((node >> i) & 0xFF) for i in range(0, 48, 8)][::-1])
    return hwid


def _derive_trial_expiry_url(trial_key_url: str) -> str:
    if trial_key_url.endswith("/get_trial_key"):
        return trial_key_url[: -len("/get_trial_key")] + "/get_trial_key_expiry"
    return trial_key_url.rstrip("/") + "/get_trial_key_expiry"


def _format_expiry_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).strftime("%Y-%m-%d")
    except ValueError:
        return raw[:10] if len(raw) >= 10 else raw


def _extract_expiry(resp_data: dict) -> str:
    if resp_data.get("has_expiry") is False or resp_data.get("no_expiry") is True:
        return "无到期时间"
    for key in ("expires", "expires_at", "expiry", "expire_at"):
        value = str(resp_data.get(key, "") or "").strip()
        if value:
            return _format_expiry_date(value)
    return ""


def _post_json(url: str, payload: dict) -> dict | None:
    try:
        context = ssl._create_unverified_context()
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10.0, context=context) as response:
            body = response.read().decode("utf-8")
            if response.status != 200:
                print(f"Server returned error code: {response.status}, details: {body}")
                return None
            return json.loads(body)
    except Exception as e:
        print(f"Network request failed: {e}")
        return None


def register_trial_key(hwid, trial_key_url):
    """向服务器申请试用 Key，并返回 key + 到期时间"""
    payload = {"hwid": hwid}
    print(f"Requesting trial Key from {trial_key_url}...")
    resp_data = _post_json(trial_key_url, payload)
    if not resp_data:
        return None

    if resp_data.get("status") == "success" or "key" in resp_data:
        key = str(resp_data.get("key", "") or "").strip()
        expires = _extract_expiry(resp_data)
        print(f"Server response success! Key: {key}")
        if expires:
            print(f"Trial key expires at: {expires}")
        return {
            "key": key,
            "expires": expires,
            "message": resp_data.get("message", ""),
        }

    print(f"Server response error: {resp_data.get('message')}")
    return None


def fetch_trial_key_expiry(hwid: str, api_key: str, trial_key_url: str) -> str:
    """查询试用 Key 的到期时间"""
    expiry_url = _derive_trial_expiry_url(trial_key_url)
    payload = {
        "hwid": hwid,
        "key": api_key,
    }
    print(f"Requesting trial Key expiry from {expiry_url}...")
    resp_data = _post_json(expiry_url, payload)
    if not resp_data:
        return ""

    expires = _extract_expiry(resp_data)
    if expires:
        print(f"Trial key expiry refreshed: {expires}")
    return expires
