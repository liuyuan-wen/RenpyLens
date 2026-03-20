# Hook - 自动注入到 Ren'Py 游戏 game/ 目录
# 通过 config.all_character_callbacks 拦截对话并发送到翻译工具

init python:
    import socket as _tsock
    import json as _tjson
    import threading as _tthread

    _translator_port = 19876

    def _expand_name_vars(text):
        """手动展开类似 [smgn2m] 的变量，防止 renpy.substitute 漏掉"""
        import re
        if not text or not isinstance(text, str):
            return text
        def replacer(match):
            var_name = match.group(1)
            try:
                import renpy
                val = getattr(renpy.store, var_name, None)
                if val is not None:
                    return str(val)
            except Exception:
                pass
            return match.group(0)
        return re.sub(r'\[([a-zA-Z0-9_]+)\]', replacer, text)

    def _translator_send(data_dict):
        """异步发送数据到翻译工具，不阻塞游戏"""
        try:
            s = _tsock.socket(_tsock.AF_INET, _tsock.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(("127.0.0.1", _translator_port))
            raw = _tjson.dumps(data_dict, ensure_ascii=False)
            s.sendall(raw.encode("utf-8"))
            s.close()
        except Exception:
            pass

    _translator_last_menu_signature = None
    _translator_last_current_msg = None

    def _translator_get_current_node(renpy):
        """获取当前执行到的 AST 节点"""
        cur = None
        if hasattr(renpy, 'game') and hasattr(renpy.game, 'context'):
            ctx = renpy.game.context()
            if hasattr(ctx, 'current'):
                cur_name = ctx.current
                if cur_name and hasattr(renpy.game, 'script'):
                    cur = renpy.game.script.lookup(cur_name)
        return cur

    def _translator_clean_text(renpy, text):
        """统一清洗 Ren'Py 文本：去标签、变量替换、去空白"""
        import re as _tre
        if not text:
            return ""
        cleaned = str(text)
        # 先移除完整花括号标签
        for _ in range(3):
            new_cleaned = _tre.sub(r'\{[^{}]*\}', '', cleaned)
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned
        # 再兜底移除不完整或异常的常见 Ren'Py 标签片段
        cleaned = _tre.sub(
            r'\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?',
            '',
            cleaned,
            flags=_tre.IGNORECASE,
        ).strip()
        try:
            cleaned = renpy.substitute(cleaned)
        except Exception:
            pass
        cleaned = _expand_name_vars(cleaned)
        return cleaned.strip()

    def _translator_lookup_name_values(renpy, name):
        """从常见的 Ren'Py 变量作用域里查找同名值"""
        results = []
        seen = set()
        if not name or not isinstance(name, str):
            return results

        def _add(value):
            if value is None:
                return
            marker = id(value)
            if marker in seen:
                return
            seen.add(marker)
            results.append(value)

        try:
            _add(getattr(renpy.store, name, None))
        except Exception:
            pass
        try:
            if hasattr(renpy, "python") and hasattr(renpy.python, "py_eval"):
                _add(renpy.python.py_eval(name))
        except Exception:
            pass
        try:
            persistent_obj = getattr(renpy.store, "persistent", None)
            if persistent_obj is not None:
                _add(getattr(persistent_obj, name, None))
        except Exception:
            pass
        try:
            if hasattr(renpy, "game") and hasattr(renpy.game, "persistent"):
                _add(getattr(renpy.game.persistent, name, None))
        except Exception:
            pass
        return results

    def _translator_extract_widget_text(renpy, widget):
        """从 Text widget 中提取当前实际显示的文本"""
        if widget is None:
            return ""
        try:
            text_value = getattr(widget, "text", "")
            if isinstance(text_value, (list, tuple)):
                parts = []
                for item in text_value:
                    if isinstance(item, str):
                        parts.append(item)
                text_value = "".join(parts)
            return _translator_clean_text(renpy, text_value)
        except Exception:
            return ""

    def _translator_get_visible_who(renpy):
        """优先读取 say screen 上实际显示的名字"""
        for screen_name in ("say", "multiple_say", "nvl"):
            try:
                screen_obj = renpy.get_screen(screen_name)
                if screen_obj is not None:
                    scope = getattr(screen_obj, "scope", None)
                    if scope and "who" in scope:
                        visible = _translator_clean_text(renpy, scope.get("who"))
                        if visible:
                            return visible
            except Exception:
                pass
            try:
                widget = renpy.get_widget(screen_name, "who")
                visible = _translator_extract_widget_text(renpy, widget)
                if visible:
                    return visible
            except Exception:
                pass

        try:
            widget = renpy.get_widget(None, "who")
            visible = _translator_extract_widget_text(renpy, widget)
            if visible:
                return visible
        except Exception:
            pass

        return ""

    def _translator_resolve_who(renpy, who_value, cur_node=None):
        """统一解析说话人显示名，优先解析动态变量后的最终显示文本"""
        candidates = []

        def _push_candidate(value, front=False):
            if value is None:
                return
            try:
                text_value = str(value)
            except Exception:
                return
            if not text_value:
                return
            if front:
                candidates.insert(0, text_value)
            else:
                candidates.append(text_value)

        if who_value is not None:
            try:
                if hasattr(who_value, "name") and who_value.name:
                    _push_candidate(who_value.name)
            except Exception:
                pass
            _push_candidate(who_value)
            # kwargs 里有时直接传角色变量名字符串，如 "MC"
            if isinstance(who_value, str) and who_value:
                for who_obj in _translator_lookup_name_values(renpy, who_value):
                    try:
                        if hasattr(who_obj, "name") and who_obj.name:
                            _push_candidate(who_obj.name, front=True)
                        else:
                            _push_candidate(who_obj, front=True)
                    except Exception:
                        pass

        if cur_node and hasattr(cur_node, "who") and cur_node.who:
            for who_obj in _translator_lookup_name_values(renpy, cur_node.who):
                try:
                    if hasattr(who_obj, "name") and who_obj.name:
                        _push_candidate(who_obj.name, front=True)
                    else:
                        _push_candidate(who_obj, front=True)
                except Exception:
                    pass
            try:
                _push_candidate(cur_node.who)
            except Exception:
                pass

        # 去重并逐个尝试替换
        seen = set()
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)

            # DynamicCharacter("MC") 这类场景需要再从 store.MC 取真实玩家名。
            if isinstance(cand, str):
                for store_value in _translator_lookup_name_values(renpy, cand):
                    if store_value is who_value:
                        continue
                    try:
                        direct_value = store_value.name if hasattr(store_value, "name") and store_value.name else store_value
                        direct_resolved = _translator_clean_text(renpy, direct_value)
                        if direct_resolved and direct_resolved != cand:
                            return direct_resolved
                        if hasattr(store_value, "name") and store_value.name:
                            _push_candidate(store_value.name, front=True)
                        else:
                            _push_candidate(store_value, front=True)
                    except Exception:
                        pass

            resolved = cand
            try:
                resolved = renpy.substitute(resolved)
            except Exception:
                pass
            resolved = _expand_name_vars(resolved)
            resolved = _translator_clean_text(renpy, resolved)
            if resolved:
                return resolved
        return ""

    def _translator_extract_menu_choices(renpy, menu_node):
        choices = []
        seen = set()
        if not menu_node or menu_node.__class__.__name__ != 'Menu' or not hasattr(menu_node, 'items'):
            return choices
        def _menu_item_is_visible(item):
            if not item or len(item) < 2:
                return True
            condition = item[1]
            if condition in (None, True):
                return True
            if condition is False:
                return False
            try:
                if isinstance(condition, str):
                    if hasattr(renpy, "python") and hasattr(renpy.python, "py_eval"):
                        return bool(renpy.python.py_eval(condition))
                return bool(condition)
            except Exception:
                return True
        for item in (menu_node.items or []):
            if not item or len(item) < 1:
                continue
            if not _menu_item_is_visible(item):
                continue
            clean_choice = _translator_clean_text(renpy, item[0])
            if clean_choice and clean_choice not in seen:
                seen.add(clean_choice)
                choices.append(clean_choice)
        return choices

    def _translator_interact_callback():
        """在交互开始时检测菜单是否真正进入可选状态"""
        import renpy
        global _translator_last_menu_signature
        try:
            cur = _translator_get_current_node(renpy)
            if cur and cur.__class__.__name__ == 'Menu':
                choices = _translator_extract_menu_choices(renpy, cur)
                sig = tuple(choices)
                if choices and sig != _translator_last_menu_signature:
                    _translator_last_menu_signature = sig
                    msg = {
                        "type": "current",
                        "who": "",
                        "what": "",
                        "italic": False,
                        "choices": choices,
                        "menu_active": True,
                    }
                    _tthread.Thread(target=_translator_send, args=(msg,), daemon=True).start()
            else:
                _translator_last_menu_signature = None
        except Exception:
            pass

    def _translator_refresh_visible_who(renpy):
        """在 say screen 真正显示后，用屏幕上的 who 文本修正当前消息"""
        global _translator_last_current_msg
        if not _translator_last_current_msg:
            return

        try:
            visible_who = _translator_get_visible_who(renpy)
            if not visible_who:
                return

            current_who = str(_translator_last_current_msg.get("who", "") or "").strip()
            if current_who == visible_who:
                return

            # 只在当前名字明显是占位符时才用屏幕值覆盖，避免误改正常角色名。
            if current_who and current_who.upper() not in ("MC", "PLAYER"):
                return

            refreshed = dict(_translator_last_current_msg)
            refreshed["who"] = visible_who
            _translator_last_current_msg = refreshed
            _tthread.Thread(target=_translator_send, args=(refreshed,), daemon=True).start()
        except Exception:
            pass

    def _translator_callback(event, interact=True, **kwargs):
        import renpy
        global _translator_last_current_msg
        if event == "begin":
            what = kwargs.get("what", "")
            raw_who = kwargs.get("who", "")
            who = raw_who
            if what is None:
                what = ""
            if who is None:
                who = ""
            if not isinstance(what, str):
                what = str(what)
            cur = None

            def _clean_game_text(text):
                return _translator_clean_text(renpy, text)
            
            # 兼容标准 Ren'Py：如果 kwargs 中没有 what，从当前 AST 节点获取
            try:
                cur = _translator_get_current_node(renpy)
            except Exception:
                pass

            if not what:
                try:
                    if cur:
                        if hasattr(cur, 'what') and cur.what:
                            what = str(cur.what)
                except Exception:
                    pass

            visible_who = _translator_get_visible_who(renpy)

            # 无论 what 是否来自 kwargs，都统一解析 who，避免显示变量名（如 "MC"）
            who = _translator_resolve_who(renpy, raw_who, cur)
            if visible_who and (not who or str(who).strip().upper() in ("MC", "PLAYER")):
                who = visible_who

            # 处理斜体：仅当整句话都在 {i}...{/i} 中时才视为全句斜体。局部斜体直接视为无斜体。
            is_italic = False
            w_strip = what.strip()
            if w_strip.startswith("{i}") and w_strip.endswith("{/i}"):
                is_italic = True

            clean_what = _clean_game_text(what)

            # 提取当前菜单选项（当前节点 + 紧邻 next 节点）
            choices = []
            seen_choices = set()

            def _collect_menu_choices(menu_node):
                for clean_choice in _translator_extract_menu_choices(renpy, menu_node):
                    if clean_choice not in seen_choices:
                        choices.append(clean_choice)
                        seen_choices.add(clean_choice)

            _collect_menu_choices(cur)
            if cur and hasattr(cur, 'next'):
                _collect_menu_choices(cur.next)

            if not clean_what and not choices:
                return

            is_menu_node = bool(cur and cur.__class__.__name__ == 'Menu')
            menu_active = is_menu_node or (not clean_what and bool(choices))

            msg = {
                "type": "current",
                "who": str(who) if who else "",
                "what": clean_what,
                "italic": is_italic,
                "choices": choices,
                "menu_active": menu_active,
            }
            _translator_last_current_msg = dict(msg)

            # 尝试预取后续几句台词
            try:
                upcoming = []
                prefetch_seen = set()

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
                            # 处理斜体：仅当整句话都在 {i}...{/i} 中时才视为全句斜体。局部斜体直接视为无斜体。
                            node_is_italic = False
                            w_strip = w.strip()
                            if w_strip.startswith("{i}") and w_strip.endswith("{/i}"):
                                node_is_italic = True
                                
                            clean_w = _clean_game_text(w)
                            if clean_w:
                                who_str = _translator_resolve_who(renpy, node.who if hasattr(node, "who") else "", node)

                                if clean_w not in prefetch_seen:
                                    prefetch_seen.add(clean_w)
                                    upcoming.append({
                                        "who": who_str,
                                        "what": clean_w,
                                        "italic": node_is_italic
                                    })
                                    count += 1
                        
                        # 2. 处理分支 (Menu)
                        if node_type == 'Menu' and hasattr(node, 'items') and node.items:
                            # 2.1 先把菜单选项文本本身加入预取，避免到菜单时还要等待
                            for item in (node.items or []):
                                if not item or len(item) < 1:
                                    continue
                                choice_text = _clean_game_text(item[0])
                                if choice_text and choice_text not in prefetch_seen and count < 60:
                                    prefetch_seen.add(choice_text)
                                    upcoming.append({
                                        "who": "",
                                        "what": choice_text,
                                        "italic": False
                                    })
                                    count += 1
                            # 2.2 再继续遍历各分支节点
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
            except Exception:
                pass

            _tthread.Thread(target=_translator_send, args=(msg,), daemon=True).start()
        elif event in ("show", "show_done", "slow_done"):
            _translator_refresh_visible_who(renpy)
        elif event == "end":
            _translator_last_current_msg = None

    # 注册回调
    try:
        config.all_character_callbacks.append(_translator_callback)

        if hasattr(config, "start_interact_callbacks"):
            config.start_interact_callbacks.append(_translator_interact_callback)
        elif hasattr(config, "interact_callbacks"):
            config.interact_callbacks.append(_translator_interact_callback)
    except Exception:
        pass
