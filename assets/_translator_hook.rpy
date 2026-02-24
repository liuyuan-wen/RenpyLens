# Hook - 自动注入到 Ren'Py 游戏 game/ 目录
# 通过 config.all_character_callbacks 拦截对话并发送到翻译工具

init python:
    import socket as _tsock
    import json as _tjson
    import threading as _tthread
    import os as _tos
    import traceback as _ttb

    _translator_port = 19876
    _translator_log = _tos.path.join(_tos.path.dirname(__file__) if '__file__' in dir() else '.', "_translator_debug.log")

    def _translator_log_msg(msg):
        """写入 debug 日志文件"""
        try:
            with open(_translator_log, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    _translator_log_msg("=== Hook 已加载 ===")
    _translator_log_msg("日志文件: " + _translator_log)
    _translator_log_msg("Socket 端口: " + str(_translator_port))

    def _translator_send(data_dict):
        """异步发送数据到翻译工具，不阻塞游戏"""
        try:
            s = _tsock.socket(_tsock.AF_INET, _tsock.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(("127.0.0.1", _translator_port))
            raw = _tjson.dumps(data_dict, ensure_ascii=False)
            s.sendall(raw.encode("utf-8"))
            s.close()
            _translator_log_msg("[SEND OK] " + raw[:200])
        except Exception as e:
            _translator_log_msg("[SEND FAIL] " + str(e))

    def _translator_callback(event, interact=True, **kwargs):
        _translator_log_msg("[CALLBACK] event=" + str(event) + " kwargs_keys=" + str(list(kwargs.keys())))
        if event == "begin":
            what = kwargs.get("what", "")
            who = kwargs.get("who", "")
            _translator_log_msg("[BEGIN] who=" + str(who) + " what=" + str(what)[:100])
            if not what:
                return
            # 清理 Ren'Py 文本标签 如 {w}, {b}, {/b}, {color=...} 等
            import re as _tre
            clean_what = _tre.sub(r'\{[^}]*\}', '', str(what)).strip()
            # 尝试进行变量替换 (例如 [protagonist] -> Kento)
            try:
                clean_what = renpy.substitute(clean_what)
            except Exception:
                pass
            if not clean_what:
                return

            msg = {
                "type": "current",
                "who": str(who) if who else "",
                "what": clean_what,
            }

            # 尝试预取后续几句台词
            try:
                upcoming = []
                cur = None
                if hasattr(renpy, 'game') and hasattr(renpy.game, 'context'):
                    ctx = renpy.game.context()
                    if hasattr(ctx, 'current'):
                        cur_name = ctx.current
                        if cur_name and hasattr(renpy.game, 'script'):
                            try:
                                cur = renpy.game.script.lookup(cur_name)
                            except Exception:
                                pass

                if cur and hasattr(cur, 'next'):
                    #以此节点为起点，进行广度优先搜索 (BFS) 以支持分支（Menu/Choice）预取
                    to_visit = [cur.next]
                    visited = set()
                    count = 0
                    
                    while to_visit and count < 60:  # 增加上限以覆盖多个分支
                        node = to_visit.pop(0)
                        if not node:
                            continue
                            
                        nid = id(node)
                        if nid in visited:
                            continue
                        visited.add(nid)
                        
                        node_type = node.__class__.__name__
                        
                        # 1. 提取对话 (Say 节点)
                        if hasattr(node, 'what') and hasattr(node, 'who'):
                            w = str(node.what) if node.what else ""
                            # 尝试进行变量替换 (例如 [protagonist] -> Kento)
                            try:
                                w = renpy.substitute(w)
                            except Exception:
                                pass
                                
                            clean_w = _tre.sub(r'\{[^}]*\}', '', w).strip()
                            if clean_w:
                                upcoming.append({
                                    "who": str(node.who) if node.who else "",
                                    "what": clean_w
                                })
                                count += 1
                        
                        # 2. 处理分支 (Menu)
                        if node_type == 'Menu' and hasattr(node, 'items') and node.items:
                            for item in node.items:
                                if len(item) >= 3 and item[2]:
                                    try:
                                        to_visit.append(item[2][0])
                                    except Exception:
                                        pass
                        
                        # 3. 处理 If 分支
                        if node_type == 'If' and hasattr(node, 'entries') and node.entries:
                            for entry in node.entries:
                                if len(entry) >= 2 and entry[1]:
                                    try:
                                        to_visit.append(entry[1][0])
                                    except Exception:
                                        pass
                        
                        # 4. 继续后续 (Linear flow)
                        if hasattr(node, 'next') and node.next:
                            to_visit.append(node.next)
                if upcoming:
                    msg["prefetch"] = upcoming
                    _translator_log_msg("[PREFETCH] " + str(len(upcoming)) + " items")
            except Exception as e:
                _translator_log_msg("[PREFETCH ERROR] " + str(e))

            _tthread.Thread(target=_translator_send, args=(msg,), daemon=True).start()

    # 注册回调
    try:
        config.all_character_callbacks.append(_translator_callback)
        _translator_log_msg("[INIT] 回调已注册到 config.all_character_callbacks")
        _translator_log_msg("[INIT] 当前回调数量: " + str(len(config.all_character_callbacks)))
    except Exception as e:
        _translator_log_msg("[INIT ERROR] " + str(e) + "\n" + _ttb.format_exc())
