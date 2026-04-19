# Hook injected into a Ren'Py game's game/ directory.
# It forwards runtime text to RenpyLens and accepts local control commands.

init python:
    import json as _tjson
    import socket as _tsock
    import threading as _tthread

    _translator_port = {{SOCKET_PORT}}
    _translator_control_port = {{CONTROL_PORT}}
    _translator_last_menu_signature = None
    _translator_last_current_msg = None
    _translator_scan_running = False
    _translator_scan_cancel_requested = False
    _translator_scan_lock = _tthread.Lock()

    def _translator_start_thread(target, args=()):
        thread = _tthread.Thread(target=target, args=args)
        try:
            thread.daemon = True
        except Exception:
            try:
                thread.setDaemon(True)
            except Exception:
                pass
        thread.start()

    def _translator_send(data_dict):
        try:
            sock = _tsock.socket(_tsock.AF_INET, _tsock.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect(("127.0.0.1", _translator_port))
            raw = _tjson.dumps(data_dict, ensure_ascii=False)
            sock.sendall(raw.encode("utf-8"))
            sock.close()
        except Exception:
            pass

    def _translator_send_type(message_type, **payload):
        msg = {"type": message_type}
        msg.update(payload)
        _translator_start_thread(_translator_send, (msg,))

    def _expand_name_vars(text):
        import re

        if not text or not isinstance(text, str):
            return text

        def replacer(match):
            var_name = match.group(1)
            try:
                import renpy
                value = getattr(renpy.store, var_name, None)
                if value is not None:
                    return str(value)
            except Exception:
                pass
            return match.group(0)

        return re.sub(r"\[([a-zA-Z0-9_]+)\]", replacer, text)

    def _translator_get_current_node(renpy):
        current = None
        if hasattr(renpy, "game") and hasattr(renpy.game, "context"):
            context = renpy.game.context()
            if hasattr(context, "current"):
                current_name = context.current
                if current_name and hasattr(renpy.game, "script"):
                    current = renpy.game.script.lookup(current_name)
        return current

    def _translator_clean_text(renpy, text):
        import re as _tre

        if not text:
            return ""

        cleaned = str(text)
        for _ in range(3):
            new_cleaned = _tre.sub(r"\{[^{}]*\}", "", cleaned)
            if new_cleaned == cleaned:
                break
            cleaned = new_cleaned

        cleaned = _tre.sub(
            r"\{/?(?:color|alpha|font|size|b|i|u|s|a|cps|w|p|nw|fast|k|rt|rb|space|vspace)\b[^}\n]*\}?",
            "",
            cleaned,
            flags=_tre.IGNORECASE,
        ).strip()

        try:
            cleaned = renpy.substitute(cleaned)
        except Exception:
            pass

        cleaned = _expand_name_vars(cleaned)
        return cleaned.strip()

    def _translator_normalize_speaker(value):
        import ast as _tast

        if value is None:
            return ""

        if isinstance(value, (list, tuple, set)):
            parts = []
            seen = set()
            for item in value:
                normalized = _translator_normalize_speaker(item)
                if normalized and normalized not in seen:
                    seen.add(normalized)
                    parts.append(normalized)
            if not parts:
                return ""
            if len(parts) == 1:
                return parts[0]
            return " / ".join(parts)

        try:
            text = str(value).strip()
        except Exception:
            return ""

        if not text or text in ("[]", "()", "{}", "None"):
            return ""

        if len(text) >= 2 and text[0] in "[(" and text[-1] in "])":
            try:
                parsed = _tast.literal_eval(text)
            except Exception:
                parsed = None
            if isinstance(parsed, (list, tuple, set)):
                return _translator_normalize_speaker(parsed)

        return " ".join(text.split())

    def _translator_lookup_name_values(renpy, name):
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
        for screen_name in ("say", "multiple_say", "nvl"):
            try:
                screen_obj = renpy.get_screen(screen_name)
                if screen_obj is not None:
                    scope = getattr(screen_obj, "scope", None)
                    if scope and "who" in scope:
                        visible = _translator_normalize_speaker(
                            _translator_clean_text(renpy, scope.get("who"))
                        )
                        if visible:
                            return visible
            except Exception:
                pass

            try:
                widget = renpy.get_widget(screen_name, "who")
                visible = _translator_normalize_speaker(
                    _translator_extract_widget_text(renpy, widget)
                )
                if visible:
                    return visible
            except Exception:
                pass

        try:
            widget = renpy.get_widget(None, "who")
            visible = _translator_normalize_speaker(
                _translator_extract_widget_text(renpy, widget)
            )
            if visible:
                return visible
        except Exception:
            pass

        return ""

    def _translator_resolve_who(renpy, who_value, cur_node=None):
        candidates = []

        def _push_candidate(value, front=False):
            if value is None:
                return
            text_value = _translator_normalize_speaker(value)
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

        seen = set()
        for cand in candidates:
            if not cand or cand in seen:
                continue
            seen.add(cand)

            if isinstance(cand, str):
                for store_value in _translator_lookup_name_values(renpy, cand):
                    if store_value is who_value:
                        continue
                    try:
                        direct_value = (
                            store_value.name
                            if hasattr(store_value, "name") and store_value.name
                            else store_value
                        )
                        direct_resolved = _translator_normalize_speaker(
                            _translator_clean_text(renpy, direct_value)
                        )
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
            resolved = _translator_normalize_speaker(_translator_clean_text(renpy, resolved))
            if resolved:
                return resolved
        return ""

    def _translator_menu_item_is_visible(renpy, item):
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

    def _translator_extract_menu_choices(renpy, menu_node):
        choices = []
        seen = set()
        if not menu_node or menu_node.__class__.__name__ != "Menu" or not hasattr(menu_node, "items"):
            return choices

        for item in (menu_node.items or []):
            if not item or len(item) < 1:
                continue
            if not _translator_menu_item_is_visible(renpy, item):
                continue
            clean_choice = _translator_clean_text(renpy, item[0])
            if clean_choice and clean_choice not in seen:
                seen.add(clean_choice)
                choices.append(clean_choice)
        return choices

    def _translator_collect_branch_nodes(node, queue):
        next_node = getattr(node, "next", None)
        if next_node:
            queue.append(next_node)

        node_type = node.__class__.__name__
        if node_type == "Menu" and hasattr(node, "items"):
            for item in (node.items or []):
                if len(item) >= 3 and item[2]:
                    try:
                        queue.append(item[2][0])
                    except Exception:
                        pass
        elif node_type == "If" and hasattr(node, "entries"):
            for entry in (node.entries or []):
                if len(entry) >= 2 and entry[1]:
                    try:
                        queue.append(entry[1][0])
                    except Exception:
                        pass

    def _translator_scan_cancel_requested_now():
        with _translator_scan_lock:
            return bool(_translator_scan_cancel_requested)

    def _translator_collect_script_nodes_from_source(source):
        nodes = []
        if source is None:
            return nodes

        try:
            if hasattr(source, "values"):
                iterable = source.values()
            else:
                iterable = source
        except Exception:
            return nodes

        try:
            for node in iterable:
                if node is not None:
                    nodes.append(node)
        except Exception:
            return []
        return nodes

    def _translator_collect_script_nodes(renpy):
        script = getattr(getattr(renpy, "game", None), "script", None)
        if script is None:
            return [], "script"

        for attr_name in ("namemap", "all_stmts"):
            try:
                nodes = _translator_collect_script_nodes_from_source(
                    getattr(script, attr_name, None)
                )
            except Exception:
                nodes = []
            if nodes:
                return nodes, attr_name
        return [], "script map"

    def _translator_wait_for_script_nodes(renpy, timeout_seconds=30.0, poll_interval=0.25):
        import time as _ttime

        deadline = _ttime.time() + float(timeout_seconds)
        last_source_name = "script map"

        while True:
            nodes, source_name = _translator_collect_script_nodes(renpy)
            if nodes:
                return nodes

            last_source_name = source_name or "script map"
            if _translator_scan_cancel_requested_now():
                return None
            if _ttime.time() >= deadline:
                raise RuntimeError(
                    "Ren'Py %s is unavailable after waiting %.1fs."
                    % (last_source_name, float(timeout_seconds))
                )
            _ttime.sleep(float(poll_interval))

    def _translator_flush_scan_batch(job_id, batch):
        if not batch:
            return
        _translator_send({
            "type": "bulk_scan_chunk",
            "job_id": job_id,
            "items": list(batch),
        })
        del batch[:]

    def _translator_scan_all(job_id=""):
        import renpy

        global _translator_scan_running
        global _translator_scan_cancel_requested

        with _translator_scan_lock:
            if _translator_scan_running:
                _translator_send_type(
                    "bulk_scan_error",
                    job_id=job_id,
                    message="A bulk scan is already running.",
                )
                return
            _translator_scan_running = True
            _translator_scan_cancel_requested = False

        try:
            _translator_send_type("bulk_scan_started", job_id=job_id)
            queue = _translator_wait_for_script_nodes(renpy)
            if queue is None:
                _translator_send_type(
                    "bulk_scan_cancelled",
                    job_id=job_id,
                    total=0,
                )
                return
            visited_nodes = set()
            seen_sources = set()
            batch = []

            while queue:
                if _translator_scan_cancel_requested_now():
                    _translator_flush_scan_batch(job_id, batch)
                    _translator_send_type(
                        "bulk_scan_cancelled",
                        job_id=job_id,
                        total=len(seen_sources),
                    )
                    return

                node = queue.pop()
                if node is None:
                    continue

                node_id = id(node)
                if node_id in visited_nodes:
                    continue
                visited_nodes.add(node_id)

                if hasattr(node, "what") and hasattr(node, "who"):
                    clean_what = _translator_clean_text(renpy, getattr(node, "what", ""))
                    if clean_what and clean_what not in seen_sources:
                        speaker = ""
                        try:
                            speaker = _translator_resolve_who(
                                renpy,
                                getattr(node, "who", ""),
                                node,
                            )
                        except Exception:
                            speaker = ""
                        seen_sources.add(clean_what)
                        batch.append(
                            {
                                "source": clean_what,
                                "entry_type": "dialogue",
                                "speaker": _translator_normalize_speaker(speaker),
                            }
                        )

                if node.__class__.__name__ == "Menu" and hasattr(node, "items"):
                    for item in (node.items or []):
                        if not item or len(item) < 1:
                            continue
                        choice_text = _translator_clean_text(renpy, item[0])
                        if choice_text and choice_text not in seen_sources:
                            seen_sources.add(choice_text)
                            batch.append(
                                {
                                    "source": choice_text,
                                    "entry_type": "choice",
                                    "speaker": "",
                                }
                            )

                if len(batch) >= 200:
                    _translator_flush_scan_batch(job_id, batch)

                _translator_collect_branch_nodes(node, queue)

            _translator_flush_scan_batch(job_id, batch)
            _translator_send_type(
                "bulk_scan_finished",
                job_id=job_id,
                total=len(seen_sources),
            )
        except Exception as e:
            _translator_send_type(
                "bulk_scan_error",
                job_id=job_id,
                message=str(e),
            )
        finally:
            with _translator_scan_lock:
                _translator_scan_running = False
                _translator_scan_cancel_requested = False

    def _translator_handle_control_client(client):
        global _translator_scan_cancel_requested

        try:
            client.settimeout(1.0)
            chunks = []
            while True:
                data = client.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if len(data) < 4096:
                    break

            if not chunks:
                return

            message = _tjson.loads(b"".join(chunks).decode("utf-8"))
            command = str(message.get("command", "") or "").strip()
            job_id = str(message.get("job_id", "") or "").strip()

            if command == "scan_all":
                _translator_start_thread(_translator_scan_all, (job_id,))
            elif command == "cancel_scan":
                with _translator_scan_lock:
                    _translator_scan_cancel_requested = True
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _translator_control_server():
        server = None
        try:
            server = _tsock.socket(_tsock.AF_INET, _tsock.SOCK_STREAM)
            server.setsockopt(_tsock.SOL_SOCKET, _tsock.SO_REUSEADDR, 1)
            server.bind(("127.0.0.1", _translator_control_port))
            server.listen(5)
            _translator_send_type("hook_ready", control_port=_translator_control_port)
            while True:
                client, _ = server.accept()
                _translator_start_thread(_translator_handle_control_client, (client,))
        except Exception:
            pass
        finally:
            if server is not None:
                try:
                    server.close()
                except Exception:
                    pass

    def _translator_interact_callback():
        import renpy

        global _translator_last_menu_signature
        try:
            cur = _translator_get_current_node(renpy)
            if cur and cur.__class__.__name__ == "Menu":
                choices = _translator_extract_menu_choices(renpy, cur)
                signature = tuple(choices)
                if choices and signature != _translator_last_menu_signature:
                    _translator_last_menu_signature = signature
                    _translator_start_thread(
                        _translator_send,
                        (
                            {
                                "type": "current",
                                "who": "",
                                "what": "",
                                "italic": False,
                                "choices": choices,
                                "menu_active": True,
                            },
                        ),
                    )
            else:
                _translator_last_menu_signature = None
        except Exception:
            pass

    def _translator_refresh_visible_who(renpy):
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

            if current_who and current_who.upper() not in ("MC", "PLAYER"):
                return

            refreshed = dict(_translator_last_current_msg)
            refreshed["who"] = visible_who
            _translator_last_current_msg = refreshed
            _translator_start_thread(_translator_send, (refreshed,))
        except Exception:
            pass

    def _translator_callback(event, interact=True, **kwargs):
        import renpy

        global _translator_last_current_msg

        if event == "begin":
            what = kwargs.get("what", "")
            raw_who = kwargs.get("who", "")
            if what is None:
                what = ""
            if raw_who is None:
                raw_who = ""
            if not isinstance(what, str):
                what = str(what)

            cur = None
            try:
                cur = _translator_get_current_node(renpy)
            except Exception:
                pass

            if not what:
                try:
                    if cur and hasattr(cur, "what") and cur.what:
                        what = str(cur.what)
                except Exception:
                    pass

            visible_who = _translator_get_visible_who(renpy)
            who = _translator_resolve_who(renpy, raw_who, cur)
            if visible_who and (not who or str(who).strip().upper() in ("MC", "PLAYER")):
                who = visible_who

            is_italic = False
            stripped_what = what.strip()
            if stripped_what.startswith("{i}") and stripped_what.endswith("{/i}"):
                is_italic = True

            clean_what = _translator_clean_text(renpy, what)

            choices = []
            seen_choices = set()

            def _collect_menu_choices(menu_node):
                for clean_choice in _translator_extract_menu_choices(renpy, menu_node):
                    if clean_choice not in seen_choices:
                        seen_choices.add(clean_choice)
                        choices.append(clean_choice)

            _collect_menu_choices(cur)
            if cur and hasattr(cur, "next"):
                _collect_menu_choices(cur.next)

            if not clean_what and not choices:
                return

            is_menu_node = bool(cur and cur.__class__.__name__ == "Menu")
            menu_active = is_menu_node or (not clean_what and bool(choices))

            msg = {
                "type": "current",
                "who": _translator_normalize_speaker(who) if who else "",
                "what": clean_what,
                "italic": is_italic,
                "choices": choices,
                "menu_active": menu_active,
            }
            _translator_last_current_msg = dict(msg)

            try:
                upcoming = []
                prefetch_seen = set()
                node = cur.next if cur and hasattr(cur, "next") else None
                visited = set()
                count = 0

                while node and count < 60:
                    node_id = id(node)
                    if node_id in visited:
                        break
                    visited.add(node_id)

                    node_type = node.__class__.__name__
                    if node_type in ("Menu", "If"):
                        break

                    if hasattr(node, "what") and hasattr(node, "who"):
                        text = str(node.what) if node.what else ""
                        node_italic = False
                        stripped_text = text.strip()
                        if stripped_text.startswith("{i}") and stripped_text.endswith("{/i}"):
                            node_italic = True

                        clean_text = _translator_clean_text(renpy, text)
                        if clean_text and clean_text not in prefetch_seen:
                            who_str = _translator_resolve_who(
                                renpy,
                                node.who if hasattr(node, "who") else "",
                                node,
                            )
                            prefetch_seen.add(clean_text)
                            upcoming.append(
                                {
                                    "who": who_str,
                                    "what": clean_text,
                                    "italic": node_italic,
                                }
                            )
                            count += 1

                    node = getattr(node, "next", None)

                if upcoming:
                    msg["prefetch"] = upcoming
            except Exception:
                pass

            _translator_start_thread(_translator_send, (msg,))
        elif event in ("show", "show_done", "slow_done"):
            _translator_refresh_visible_who(renpy)
        elif event == "end":
            _translator_last_current_msg = None

    try:
        config.all_character_callbacks.append(_translator_callback)

        if hasattr(config, "start_interact_callbacks"):
            config.start_interact_callbacks.append(_translator_interact_callback)
        elif hasattr(config, "interact_callbacks"):
            config.interact_callbacks.append(_translator_interact_callback)
    except Exception:
        pass

    _translator_start_thread(_translator_control_server)
