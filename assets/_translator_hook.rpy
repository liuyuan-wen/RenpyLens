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
    _translator_last_visible_signature = None
    _translator_last_resolved_who = ""
    _translator_scan_running = False
    _translator_scan_cancel_requested = False
    _translator_runtime_ready_sent = False
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

    def _translator_schedule_on_main_thread(callback, *args):
        try:
            import renpy
        except Exception as e:
            return False, str(e)

        exports_module = None
        invoke = getattr(renpy, "invoke_in_main_thread", None)

        if invoke is None:
            try:
                import renpy.exports as exports_module
            except Exception:
                exports_module = None

            if exports_module is not None:
                invoke = getattr(exports_module, "invoke_in_main_thread", None)

        if invoke is not None:
            try:
                invoke(callback, *args)
                return True, None
            except Exception as e:
                return False, str(e)

        try:
            interface = getattr(getattr(renpy, "display", None), "interface", None)
            invoke_queue = getattr(interface, "invoke_queue", None)
            if invoke_queue is None:
                raise AttributeError("main-thread invoke queue is unavailable")
            invoke_queue.append((callback, args, {}))
            return True, None
        except Exception as e:
            return False, str(e)

    def _translator_mark_runtime_ready():
        global _translator_runtime_ready_sent

        if _translator_runtime_ready_sent:
            return

        _translator_runtime_ready_sent = True
        _translator_send_type("runtime_ready")

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

    def _translator_debug_value(value, limit=160):
        try:
            text = str(value)
        except Exception:
            text = "<unprintable>"
        text = text.replace("\n", " ").replace("\r", " ")
        return text[:limit]

    def _translator_node_debug(node):
        if node is None:
            return {
                "type": "",
                "name": "",
                "filename": "",
                "line": "",
            }
        return {
            "type": node.__class__.__name__,
            "name": _translator_debug_value(getattr(node, "name", "")),
            "filename": _translator_debug_value(getattr(node, "filename", "")),
            "line": _translator_debug_value(getattr(node, "linenumber", "")),
        }

    def _translator_stack_debug(stack):
        try:
            depth = len(stack or [])
        except Exception:
            return {"depth": -1, "top": "<unavailable>"}
        top = ""
        if depth:
            try:
                top = _translator_debug_value(stack[-1])
            except Exception:
                top = "<unavailable>"
        return {"depth": depth, "top": top}

    def _translator_context_debug(renpy):
        result = {
            "current": "",
            "return_stack": {"depth": -1, "top": "<unavailable>"},
            "call_location_stack": {"depth": -1, "top": "<unavailable>"},
        }
        try:
            context = renpy.game.context()
            result["current"] = _translator_debug_value(getattr(context, "current", ""))
            result["return_stack"] = _translator_stack_debug(
                getattr(context, "return_stack", None)
            )
            result["call_location_stack"] = _translator_stack_debug(
                getattr(context, "call_location_stack", None)
            )
        except Exception as e:
            result["error"] = _translator_debug_value(e)
        return result

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
        import re as _tre

        if value is None:
            return ""

        # Ren'Py may supply a displayable class as `who` (notably Movie).
        # Showing its Python repr leaks "<class 'renpy....'>" into the
        # overlay, so reduce class objects to their human-facing class name.
        try:
            if isinstance(value, type):
                return value.__name__
        except Exception:
            pass

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

        class_match = _tre.match(
            r"^<(?:class|type) ['\"](?:[^'\"]*\.)?([^.'\"]+)['\"]>$",
            text,
        )
        if class_match:
            return ""

        # Do not leak unresolved interpolation or Python/Ren'Py object
        # representations into the user-facing speaker label.
        if _tre.search(r"\[[^\]]+\]", text):
            return ""
        if _tre.search(r"\bobject at 0x[0-9a-f]+\b", text, _tre.IGNORECASE):
            return ""
        if _tre.match(r"^<(?:function|bound method|renpy\.)", text, _tre.IGNORECASE):
            return ""
        if len(text) > 120:
            return ""

        text = " ".join(text.split())
        # Ren'Py 7 and older embed Python 2.7, whose ``re`` module does not
        # provide ``fullmatch``.  ``\Z`` keeps the same whole-string semantics
        # while remaining compatible with both Python 2 and Python 3.
        if _tre.match(r"^[A-Za-z][A-Za-z0-9_]*\Z", text):
            text = _tre.sub(r"_t$", "", text, flags=_tre.IGNORECASE)
            text = text.replace("_", " ")
        return text

    def _translator_get_side_image_speaker(renpy):
        import os as _tos

        try:
            attrs = getattr(renpy.store, "_side_image_attributes", None)
            if not attrs:
                return ""

            prefix = getattr(renpy.config, "side_image_prefix_tag", "side") or "side"
            image_name = renpy.get_side_image(prefix, not_showing=False)
            if not image_name:
                return ""

            if not isinstance(image_name, tuple):
                image_name = tuple(str(image_name).split())

            image_module = getattr(getattr(renpy, "display", None), "image", None)
            image_map = getattr(image_module, "images", None) or {}
            displayable = image_map.get(image_name)
            seen = set()
            while displayable is not None and id(displayable) not in seen:
                seen.add(id(displayable))
                filename = getattr(displayable, "filename", None)
                if filename:
                    basename = _tos.path.basename(str(filename).replace("\\", "/"))
                    stem = _tos.path.splitext(basename)[0]
                    return _translator_normalize_speaker(stem)

                target = getattr(displayable, "target", None)
                if target is None:
                    break
                displayable = target
        except Exception:
            pass
        return ""

    def _translator_choose_visible_speaker(visible, side_speaker):
        visible = _translator_normalize_speaker(visible)
        side_speaker = _translator_normalize_speaker(side_speaker)
        if not visible:
            return side_speaker

        # Generic NPC labels are often paired with a side image whose file
        # contains the actual on-screen role/name (for example npc 6 ->
        # faces/dude.webp). Do not replace normal character names this way.
        if visible.lower() in ("npc", "unknown", "character") and side_speaker:
            return side_speaker
        return visible

    def _translator_apply_speaker_state(value, continuation=False):
        global _translator_last_resolved_who

        normalized = _translator_normalize_speaker(value)
        if normalized.lower() == "extend":
            continuation = True
            normalized = ""

        if normalized:
            _translator_last_resolved_who = normalized
            return normalized
        if continuation:
            return _translator_last_resolved_who
        return ""

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
        side_speaker = _translator_get_side_image_speaker(renpy)

        for screen_name in ("say", "multiple_say", "nvl"):
            # The rendered widget is authoritative. A custom say screen may
            # receive an internal key such as "npc" in its scope, while the
            # Text widget actually shown to the player contains "DUDE".
            try:
                widget = renpy.get_widget(screen_name, "who")
                visible = _translator_normalize_speaker(
                    _translator_extract_widget_text(renpy, widget)
                )
                if visible:
                    return _translator_choose_visible_speaker(visible, side_speaker)
            except Exception:
                pass

            try:
                screen_obj = renpy.get_screen(screen_name)
                if screen_obj is not None:
                    widgets = getattr(screen_obj, "widgets", None) or {}
                    for widget_id, widget in widgets.items():
                        normalized_id = str(widget_id or "").lower().replace("-", "_")
                        if not any(token in normalized_id for token in ("who", "speaker", "name")):
                            continue
                        visible = _translator_normalize_speaker(
                            _translator_extract_widget_text(renpy, widget)
                        )
                        if visible:
                            return _translator_choose_visible_speaker(visible, side_speaker)
            except Exception:
                pass

            try:
                screen_obj = renpy.get_screen(screen_name)
                if screen_obj is not None:
                    scope = getattr(screen_obj, "scope", None) or {}
                    for scope_key, scope_value in scope.items():
                        normalized_key = str(scope_key or "").lower().replace("-", "_")
                        if normalized_key == "who":
                            continue
                        if not any(token in normalized_key for token in ("speaker", "name")):
                            continue
                        visible = _translator_normalize_speaker(
                            _translator_clean_text(renpy, scope_value)
                        )
                        if visible:
                            return _translator_choose_visible_speaker(visible, side_speaker)

                    if "who" in scope:
                        visible = _translator_normalize_speaker(
                            _translator_clean_text(renpy, scope.get("who"))
                        )
                        if visible:
                            return _translator_choose_visible_speaker(visible, side_speaker)
            except Exception:
                pass

        try:
            widget = renpy.get_widget(None, "who")
            visible = _translator_normalize_speaker(
                _translator_extract_widget_text(renpy, widget)
            )
            if visible:
                return _translator_choose_visible_speaker(visible, side_speaker)
        except Exception:
            pass

        return side_speaker

    def _translator_get_visible_what(renpy):
        for screen_name in ("say", "multiple_say", "nvl"):
            try:
                screen_obj = renpy.get_screen(screen_name)
                if screen_obj is not None:
                    scope = getattr(screen_obj, "scope", None)
                    if scope and "what" in scope:
                        visible = _translator_clean_text(renpy, scope.get("what"))
                        if visible:
                            return visible
            except Exception:
                pass

            for widget_id in ("what", "dialogue", "text"):
                try:
                    widget = renpy.get_widget(screen_name, widget_id)
                    visible = _translator_extract_widget_text(renpy, widget)
                    if visible:
                        return visible
                except Exception:
                    pass

        try:
            widget = renpy.get_widget(None, "what")
            visible = _translator_extract_widget_text(renpy, widget)
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

    def _translator_select_if_branch(renpy, if_node):
        entries = getattr(if_node, "entries", None) or []
        for index, entry in enumerate(entries):
            if not entry or len(entry) < 2:
                continue

            condition = entry[0]
            if condition in (None, True):
                matches = True
            elif condition is False:
                matches = False
            elif isinstance(condition, str):
                matches = bool(renpy.python.py_eval(condition))
            else:
                matches = bool(condition)

            if not matches:
                continue

            block = entry[1] or []
            next_node = block[0] if block else getattr(if_node, "next", None)
            return next_node, {
                "index": index,
                "condition": _translator_debug_value(condition),
                "target": _translator_node_debug(next_node),
            }

        next_node = getattr(if_node, "next", None)
        return next_node, {
            "index": -1,
            "condition": "<no-match>",
            "target": _translator_node_debug(next_node),
        }

    def _translator_extract_menu_entries(renpy, menu_node):
        caption = ""
        choices = []
        seen = set()
        if not menu_node or menu_node.__class__.__name__ != "Menu" or not hasattr(menu_node, "items"):
            return caption, choices

        for item in (menu_node.items or []):
            if not item or len(item) < 1:
                continue
            if not _translator_menu_item_is_visible(renpy, item):
                continue
            clean_choice = _translator_clean_text(renpy, item[0])
            if not clean_choice:
                continue

            # Ren'Py stores a menu caption as an item without a branch block.
            # It is explanatory text, not a selectable option.
            branch = item[2] if len(item) >= 3 else None
            if not branch:
                if not caption:
                    caption = clean_choice
                continue

            if clean_choice not in seen:
                seen.add(clean_choice)
                choices.append(clean_choice)
        return caption, choices

    def _translator_extract_menu_choices(renpy, menu_node):
        return _translator_extract_menu_entries(renpy, menu_node)[1]

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
                        menu_text = _translator_clean_text(renpy, item[0])
                        if menu_text and menu_text not in seen_sources:
                            seen_sources.add(menu_text)
                            branch = item[2] if len(item) >= 3 else None
                            batch.append(
                                {
                                    "source": menu_text,
                                    "entry_type": "choice" if branch else "dialogue",
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
                ok, err = _translator_schedule_on_main_thread(_translator_scan_all, job_id)
                if not ok:
                    _translator_send_type(
                        "bulk_scan_error",
                        job_id=job_id,
                        message=str(err or "Failed to schedule bulk scan on the main thread."),
                    )
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
        global _translator_last_current_msg
        global _translator_last_visible_signature
        try:
            _translator_mark_runtime_ready()
            cur = _translator_get_current_node(renpy)
            if cur and cur.__class__.__name__ == "Menu":
                caption, choices = _translator_extract_menu_entries(renpy, cur)
                signature = (caption, tuple(choices))
                if choices and signature != _translator_last_menu_signature:
                    _translator_last_menu_signature = signature
                    _translator_last_visible_signature = None
                    _translator_start_thread(
                        _translator_send,
                        (
                            {
                                "type": "current",
                                "who": "",
                                "what": caption,
                                "italic": False,
                                "choices": choices,
                                "menu_active": True,
                            },
                        ),
                    )
            else:
                _translator_last_menu_signature = None

                visible_what = _translator_get_visible_what(renpy)
                if visible_what:
                    visible_who = _translator_get_visible_who(renpy)
                    visible_who = _translator_apply_speaker_state(
                        visible_who,
                        continuation=str(visible_who or "").strip().lower() == "extend",
                    )
                    signature = (visible_who, visible_what)
                    if signature != _translator_last_visible_signature:
                        _translator_last_visible_signature = signature
                        msg = {
                            "type": "current",
                            "who": visible_who,
                            "what": visible_what,
                            "italic": False,
                            "choices": [],
                            "menu_active": False,
                        }
                        _translator_last_current_msg = dict(msg)
                        _translator_start_thread(_translator_send, (msg,))
        except Exception:
            pass

    def _translator_refresh_visible_who(renpy):
        global _translator_last_current_msg
        if not _translator_last_current_msg:
            return

        try:
            visible_who = _translator_get_visible_who(renpy)
            visible_who = _translator_apply_speaker_state(
                visible_who,
                continuation=str(visible_who or "").strip().lower() == "extend",
            )
            if not visible_who:
                return

            current_who = str(_translator_last_current_msg.get("who", "") or "").strip()
            if current_who == visible_who:
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
        global _translator_last_visible_signature

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
            if visible_who:
                who = visible_who
            continuation = (
                str(raw_who or "").strip().lower() == "extend"
                or str(who or "").strip().lower() == "extend"
            )
            who = _translator_apply_speaker_state(who, continuation=continuation)

            is_italic = False
            stripped_what = what.strip()
            if stripped_what.startswith("{i}") and stripped_what.endswith("{/i}"):
                is_italic = True

            clean_what = _translator_clean_text(renpy, what)

            choices = []
            seen_choices = set()
            menu_caption = ""

            def _collect_menu_choices(menu_node):
                global_caption, menu_choices = _translator_extract_menu_entries(renpy, menu_node)
                for clean_choice in menu_choices:
                    if clean_choice not in seen_choices:
                        seen_choices.add(clean_choice)
                        choices.append(clean_choice)
                return global_caption

            menu_caption = _collect_menu_choices(cur)
            if cur and hasattr(cur, "next"):
                next_caption = _collect_menu_choices(cur.next)
                if not menu_caption:
                    menu_caption = next_caption

            is_menu_node = bool(cur and cur.__class__.__name__ == "Menu")
            if is_menu_node and menu_caption:
                clean_what = menu_caption

            if not clean_what and not choices:
                return

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
            if clean_what:
                _translator_last_visible_signature = (msg["who"], clean_what)

            prefetch_debug = {
                "current_node": _translator_node_debug(cur),
                "start_node": _translator_node_debug(
                    cur.next if cur and hasattr(cur, "next") else None
                ),
                "stop_reason": "not-started",
                "stop_node": _translator_node_debug(None),
                "visited_nodes": 0,
                "prefetch_items": 0,
                "if_branches": [],
                "context": _translator_context_debug(renpy),
            }
            try:
                upcoming = []
                prefetch_seen = set()
                node = cur.next if cur and hasattr(cur, "next") else None
                visited = set()
                count = 0
                last_node = None

                if cur is None:
                    prefetch_debug["stop_reason"] = "current-node-missing"
                elif node is None:
                    prefetch_debug["stop_reason"] = "current-next-none"

                while node and count < 60:
                    node_id = id(node)
                    if node_id in visited:
                        prefetch_debug["stop_reason"] = "cycle"
                        prefetch_debug["stop_node"] = _translator_node_debug(node)
                        break
                    visited.add(node_id)
                    last_node = node

                    node_type = node.__class__.__name__
                    if node_type == "Menu":
                        prefetch_debug["stop_reason"] = "menu-boundary"
                        prefetch_debug["stop_node"] = _translator_node_debug(node)
                        break
                    if node_type == "If":
                        try:
                            node, branch_debug = _translator_select_if_branch(renpy, node)
                            prefetch_debug["if_branches"].append(branch_debug)
                            continue
                        except Exception as e:
                            prefetch_debug["stop_reason"] = "if-eval-error"
                            prefetch_debug["stop_node"] = _translator_node_debug(node)
                            prefetch_debug["error"] = _translator_debug_value(e)
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

                if prefetch_debug["stop_reason"] == "not-started":
                    if node is not None and count >= 60:
                        prefetch_debug["stop_reason"] = "prefetch-limit"
                        prefetch_debug["stop_node"] = _translator_node_debug(node)
                    else:
                        prefetch_debug["stop_reason"] = "next-none"
                        prefetch_debug["stop_node"] = _translator_node_debug(last_node)

                prefetch_debug["visited_nodes"] = len(visited)
                prefetch_debug["prefetch_items"] = len(upcoming)

                if upcoming:
                    msg["prefetch"] = upcoming
            except Exception as e:
                prefetch_debug["stop_reason"] = "exception"
                prefetch_debug["error"] = _translator_debug_value(e)
            msg["prefetch_debug"] = prefetch_debug

            _translator_start_thread(_translator_send, (msg,))
        elif event in ("show", "show_done", "slow_done"):
            _translator_refresh_visible_who(renpy)
        elif event == "end":
            _translator_last_current_msg = None
            _translator_last_visible_signature = None

    try:
        config.all_character_callbacks.append(_translator_callback)

        if hasattr(config, "start_interact_callbacks"):
            config.start_interact_callbacks.append(_translator_interact_callback)
        elif hasattr(config, "interact_callbacks"):
            config.interact_callbacks.append(_translator_interact_callback)
    except Exception:
        pass

    _translator_start_thread(_translator_control_server)
