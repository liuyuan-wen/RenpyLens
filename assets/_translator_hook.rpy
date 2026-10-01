# Hook injected into a Ren'Py game's game/ directory.
# It forwards runtime text to RenpyLens and accepts local control commands.

init python:
    import json as _tjson
    import os as _tos
    import socket as _tsock
    import sys as _tsys
    import threading as _tthread
    import uuid as _tuuid
    import renpy as _translator_runtime
    _translator_kernel_archive = _tos.path.join(
        getattr(_translator_runtime.config, "gamedir", ""),
        "_renpylens_semantics.zip",
    )
    if _translator_kernel_archive and _translator_kernel_archive not in _tsys.path:
        _tsys.path.insert(0, _translator_kernel_archive)
    from renpylens_semantics.protocol import EventSequencer as _translator_event_sequencer
    from renpylens_semantics.protocol import PROTOCOL_VERSION as _translator_protocol_version
    from renpylens_semantics.renpy_adapter import IdentityIndex as _translator_identity_class
    from renpylens_semantics.renpy_adapter import all_nodes as _translator_all_nodes
    from renpylens_semantics.renpy_adapter import graph_id_for as _translator_graph_id_for
    from renpylens_semantics.graph_machine import predict as _translator_kernel_predict

    _translator_port = {{SOCKET_PORT}}
    _translator_control_port = {{CONTROL_PORT}}
    _translator_last_menu_signature = None
    _translator_last_current_msg = None
    _translator_last_visible_signature = None
    _translator_last_resolved_who = ""
    _translator_scan_running = False
    _translator_scan_cancel_requested = False
    _translator_runtime_ready_sent = False
    _translator_session_id = getattr(
        _translator_runtime, "_renpylens_session_id", None
    ) or _tuuid.uuid4().hex
    _translator_runtime._renpylens_session_id = _translator_session_id
    _translator_state_version = int(getattr(
        _translator_runtime, "_renpylens_state_version", 0
    ) or 0)
    _translator_runtime._renpylens_state_version = _translator_state_version
    _translator_prefetch_count = 10
    _translator_menu_versions = {}
    _translator_original_menu = None
    _translator_was_rollback = False
    _translator_scan_lock = _tthread.Lock()
    _translator_protocol_lock = _tthread.Lock()
    _translator_event_counter = _translator_event_sequencer()
    _translator_graph_id = "{{GRAPH_ID}}"
    _translator_identity = None

    def _translator_prepare_identity(renpy):
        global _translator_graph_id
        global _translator_identity
        if _translator_identity is not None:
            return
        try:
            gamedir = getattr(getattr(renpy, "config", None), "gamedir", "")
            if not _translator_graph_id:
                _translator_graph_id = _translator_graph_id_for(gamedir)
            _translator_identity = _translator_identity_class(
                _translator_graph_id, _translator_all_nodes(renpy), gamedir
            )
        except Exception:
            _translator_graph_id = ""
            _translator_identity = None

    def _translator_node_ref(node):
        if _translator_identity is None:
            return None
        try:
            return _translator_identity.ref_for(node)
        except Exception:
            return None

    def _translator_edge_id(source, target, kind, slot=""):
        if _translator_identity is None:
            return ""
        try:
            return _translator_identity.edge_id(source, target, kind, slot)
        except Exception:
            return ""

    def _translator_protocolize(data_dict):
        msg = dict(data_dict or {})
        with _translator_protocol_lock:
            if "event_seq" not in msg:
                msg["event_seq"] = _translator_event_counter.next()
        msg.setdefault("protocol_version", _translator_protocol_version)
        msg.setdefault("graph_id", _translator_graph_id)
        msg.setdefault("session_id", _translator_session_id)
        msg.setdefault("node_id", "")
        if "state_version" not in msg and msg.get("type") in (
                "current", "branch_prefetch", "menu_selected", "route_invalidated"):
            msg["state_version"] = _translator_state_version
        return msg

    def _translator_menu_id(node):
        if node is None:
            return ""
        ref = _translator_node_ref(node)
        if ref is not None and getattr(ref, "node_id", ""):
            return ref.node_id
        name = getattr(node, "name", None)
        if name:
            return _translator_debug_value(name)
        filename = _translator_debug_value(getattr(node, "filename", ""))
        line = _translator_debug_value(getattr(node, "linenumber", ""))
        if not filename and not line:
            return ""
        return "%s:%s" % (filename, line)

    def _translator_next_state_version():
        global _translator_state_version
        runtime_version = int(getattr(
            _translator_runtime, "_renpylens_state_version", 0
        ) or 0)
        _translator_state_version = max(_translator_state_version, runtime_version) + 1
        _translator_runtime._renpylens_state_version = _translator_state_version
        return _translator_state_version

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
            data_dict = _translator_protocolize(data_dict)
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
        msg = _translator_protocolize(msg)
        _translator_start_thread(_translator_send, (msg,))

    def _translator_send_current_and_branches(current, branch_message):
        _translator_send(_translator_protocolize(current))
        if branch_message:
            _translator_send(_translator_protocolize(branch_message))

    def _translator_resume_branch_prefetch(task, state_version, branch_message):
        if state_version != _translator_state_version:
            return
        needs_more = task()
        if state_version != _translator_state_version:
            return
        # The sender runs on another thread, so freeze this revision before the
        # next continuation mutates the shared branch records.
        payload = _tjson.loads(_tjson.dumps(branch_message, ensure_ascii=False))
        # A continuation is a new transport event, even for the same state
        # revision.  Allocate a fresh sequence instead of reusing the packet
        # sent when the branch was first scheduled.
        payload.pop("event_seq", None)
        payload = _translator_protocolize(payload)
        _translator_start_thread(_translator_send, (payload,))
        if needs_more:
            _translator_schedule_on_main_thread(
                _translator_resume_branch_prefetch,
                task,
                state_version,
                branch_message,
            )

    def _translator_schedule_branch_continuations(tasks, state_version, branch_message):
        for task in tasks:
            _translator_schedule_on_main_thread(
                _translator_resume_branch_prefetch,
                task,
                state_version,
                branch_message,
            )

    def _translator_invalidate_route(reason):
        global _translator_menu_versions
        global _translator_session_id
        _translator_menu_versions = {}
        _translator_session_id = getattr(
            _translator_runtime, "_renpylens_session_id", _translator_session_id
        )
        state_version = _translator_next_state_version()
        _translator_send_type(
            "route_invalidated",
            session_id=_translator_session_id,
            state_version=state_version,
            reason=reason,
        )

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

        _translator_prepare_identity(_translator_runtime)
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

    def _translator_format_text(renpy, source, resolve):
        if "[" not in source:
            return source
        substitutions = getattr(renpy, "substitutions", None)
        parser = getattr(substitutions, "parse", None)
        legacy = parser is None
        if legacy:
            parser = getattr(getattr(substitutions, "formatter", None), "parse", None)
        if parser is None:
            raise ValueError("substitution-parser-unavailable")
        parts = []
        for literal, code, conversion, spec in parser(source):
            if legacy:
                conversion, spec = spec, conversion
            parts.append(literal)
            if code is not None:
                parts.append(resolve(code, conversion, spec))
        return "".join(parts)

    def _translator_strip_text_tags(cleaned):
        import re as _tre
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
        )
        return cleaned

    def _translator_clean_text(renpy, text):
        if not text:
            return ""
        try:
            string_types = (basestring,)
        except NameError:
            string_types = (str,)
        cleaned = text if isinstance(text, string_types) else str(text)
        translator = getattr(getattr(renpy, "translation", None), "translate_string", None)
        if translator is not None:
            cleaned = translator(cleaned)

        def resolve(code, conversion, spec):
            expression = "[" + code
            if conversion is not None:
                expression += "!" + conversion
            if spec is not None:
                expression += ":" + spec
            expression += "]"
            try:
                return renpy.substitute(expression)
            except Exception:
                return _expand_name_vars(expression)

        try:
            cleaned = _translator_format_text(renpy, cleaned, resolve)
        except Exception:
            # Retain support for engines without an exposed bracket parser.
            try:
                cleaned = renpy.substitute(cleaned)
            except Exception:
                cleaned = _expand_name_vars(cleaned)
        return _translator_strip_text_tags(cleaned).strip()

    def _translator_normalize_speaker(value):
        import re as _tre

        if value is None:
            return ""

        try:
            string_types = (basestring,)
        except NameError:
            string_types = (str,)

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

        if not isinstance(value, string_types):
            return ""

        try:
            text = value.strip()
        except Exception:
            return ""

        if not text or text in ("[]", "()", "{}", "None"):
            return ""

        class_match = _tre.match(
            r"^<(?:class|type) ['\"](?:[^'\"]*\.)?([^.'\"]+)['\"]>$",
            text,
        )
        if class_match:
            return ""

        # Do not leak Python/Ren'Py object
        # representations into the user-facing speaker label.
        if _tre.search(r"\bobject at 0x[0-9a-f]+\b", text, _tre.IGNORECASE):
            return ""
        if _tre.match(r"^<(?:function|bound method|renpy\.)", text, _tre.IGNORECASE):
            return ""
        if len(text) > 120:
            return ""

        text = " ".join(text.split())
        return text

    def _translator_character_id(expression, previous=""):
        """Preserve expressions, but keep literal speakers in the name namespace."""
        if not expression or expression == "narrator":
            return ""
        if expression == "extend":
            return previous
        import ast
        try:
            ast.literal_eval(expression)
        except (ValueError, SyntaxError):
            return expression
        return ""

    def _translator_apply_speaker_state(value, continuation=False):
        global _translator_last_resolved_who

        normalized = _translator_normalize_speaker(value)
        if normalized:
            _translator_last_resolved_who = normalized
            return normalized
        if continuation:
            return _translator_last_resolved_who
        return ""

    def _translator_lookup_name_values(renpy, name):
        results = []
        seen = set()
        try:
            string_types = (basestring,)
        except NameError:
            string_types = (str,)
        if not name or not isinstance(name, string_types):
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

    def _translator_extract_widget_text(renpy, widget, resolved=False):
        if widget is None:
            return ""
        try:
            try:
                string_types = (basestring,)
            except NameError:
                string_types = (str,)
            text_value = getattr(widget, "text", "")
            parts = []

            def _append_text(value):
                if isinstance(value, string_types):
                    parts.append(value)
                    return
                try:
                    iterator = iter(value)
                except TypeError:
                    return
                for nested_value in iterator:
                    _append_text(nested_value)

            _append_text(text_value)
            if not parts:
                return ""
            text_value = u"".join(parts)
            if resolved:
                import re
                return re.sub(r"\{[^{}]*\}", "", text_value).strip()
            return _translator_clean_text(renpy, text_value)
        except Exception:
            return ""

    def _translator_get_visible_who(renpy):
        # Read Text objects, never pixels or scope metadata. None means unavailable;
        # an empty string means an existing name widget is intentionally empty.
        screens = []
        context = getattr(renpy, "_renpylens_display_context", None)
        if context and context[2]:
            screens.append(context[2])
        try:
            node = _translator_get_current_node(renpy)
            variable = getattr(node, "who", None)
            character = getattr(renpy.store, variable, None) if variable else None
            custom_screen = getattr(character, "screen", None)
            if custom_screen:
                screens.append(custom_screen)
        except Exception:
            pass
        screens.extend(("say", "multiple_say", "nvl"))
        for screen_name in screens:
            try:
                widget = renpy.get_widget(screen_name, "who")
                if widget is not None:
                    return _translator_normalize_speaker(
                        _translator_extract_widget_text(renpy, widget, resolved=True))
                screen = renpy.get_screen(screen_name)
                widgets = getattr(screen, "widgets", None) or {}
                for widget_id, widget in widgets.items():
                    # Match name labels, not fields such as name_font or filename.
                    if str(widget_id).lower() not in (
                        "speaker", "name", "speaker_name", "character_name", "who_name"):
                        continue
                    if hasattr(widget, "text"):
                        return _translator_normalize_speaker(
                            _translator_extract_widget_text(renpy, widget, resolved=True))
            except Exception:
                pass
        try:
            widget = renpy.get_widget(None, "who")
            if widget is not None:
                return _translator_normalize_speaker(
                    _translator_extract_widget_text(renpy, widget, resolved=True))
        except Exception:
            pass
        return None

    def _translator_get_visible_what(renpy):
        # Input interactions may leave the previous say screen visible.
        # Let screen capture report the input prompt instead of stale dialogue.
        get_mode = getattr(renpy, "get_mode", None)
        if get_mode is not None and get_mode() == "input":
            return ""
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

    def _translator_collect_displayable_text(renpy, displayable):
        body = []
        choices = []
        seen_displayables = set()
        seen_body = set()
        seen_choices = set()
        context = getattr(renpy, "context", None)
        in_game_menu = bool(context and getattr(context(), "_menu", False))

        def _children(node):
            # Container.visit() changed from render order in Ren'Py 6 to
            # reverse/event order in Ren'Py 7. The children attribute keeps
            # declaration/render order on both versions.
            children = getattr(node, "children", None)
            if children is not None:
                return children
            try:
                return node.visit() or []
            except Exception:
                return []

        def _button_text(node):
            parts = []
            visited = set()

            def _collect(current, root=False):
                if current is None or id(current) in visited:
                    return
                visited.add(id(current))

                class_name = current.__class__.__name__
                if not root and class_name in (
                    "Button",
                    "ImageButton",
                    "TextButton",
                ):
                    return
                if class_name == "Text":
                    clean_text = _translator_extract_widget_text(renpy, current)
                    if clean_text:
                        parts.append(clean_text)
                    return
                for child in _children(current):
                    _collect(child)

            _collect(node, root=True)
            return " ".join(parts)

        def _button_text_role(node):
            role = "body" if in_game_menu else "choice"
            pending = [
                getattr(node, "action", None),
                getattr(node, "clicked", None),
            ]
            visited = set()
            while pending:
                action = pending.pop()
                if action is None or id(action) in visited:
                    continue
                visited.add(id(action))
                class_name = action.__class__.__name__
                # FileAction resolves to FileSave/FileLoad in the engine.
                # Skip the whole slot, including its timestamp/save name.
                if class_name in (
                    "FileSave", "FileLoad", "FileDelete", "FileAction",
                    "FilePage", "FilePageNext", "FilePagePrevious",
                ):
                    return "skip"
                if class_name.startswith("Toggle") or class_name in (
                    "ShowMenu", "MainMenu", "Start", "OpenURL",
                ):
                    role = "body"
                if class_name in ("list", "tuple", "RevertableList"):
                    pending.extend(action)
            return role

        def _button_is_interactive(node):
            # Ren'Py screens commonly use a bare ``button`` as a styled
            # container. It looks like a Button in the display tree but has
            # no input action, so its text is ordinary screen copy rather
            # than a selectable choice.
            for attribute in ("action", "clicked", "alternate"):
                if getattr(node, attribute, None) is not None:
                    return True
            return bool(getattr(node, "keymap", None))

        def _walk(node, inside_button=False):
            if node is None:
                return

            marker = id(node)
            if marker in seen_displayables:
                return
            seen_displayables.add(marker)

            class_name = node.__class__.__name__
            is_button = class_name in (
                "Button",
                "ImageButton",
                "TextButton",
            )

            if is_button:
                interactive = _button_is_interactive(node)
                role = _button_text_role(node) if interactive else "body"
                if role == "skip":
                    return
                clean_text = _button_text(node) if interactive else ""
                if interactive and role == "body":
                    if clean_text and clean_text not in seen_body:
                        seen_body.add(clean_text)
                        body.append(clean_text)
                elif interactive and clean_text and clean_text not in seen_choices:
                    seen_choices.add(clean_text)
                    choices.append(clean_text)
            elif class_name == "Text" and not inside_button:
                clean_text = _translator_extract_widget_text(renpy, node)
                if clean_text and clean_text not in seen_body:
                    seen_body.add(clean_text)
                    body.append(clean_text)

            for child in _children(node):
                _walk(
                    child,
                    inside_button or (is_button and _button_is_interactive(node)),
                )

        _walk(displayable)
        return body, choices

    def _translator_get_custom_screen_payload(renpy):
        try:
            scene_lists = renpy.game.context().scene_lists
            # Ren'Py stores (layer, tag) in older engines and
            # (layer, tag, displayable) in newer ones.
            transient_screens = set(
                tuple(entry[:2]) for entry in
                (getattr(scene_lists, "additional_transient", None) or [])
            )
            layers = getattr(scene_lists, "layers", None) or {}
        except Exception:
            return None

        candidates = []
        for layer, entries in layers.items():
            for entry in (entries or []):
                screen = getattr(entry, "displayable", None)
                if screen is None:
                    continue

                tag = getattr(entry, "tag", None)
                if "$" in str(tag or ""):
                    continue

                # `show text` puts Text (often wrapped in a Transform) on
                # a scene layer, without creating a ScreenDisplayable.
                if screen.__class__.__name__ != "ScreenDisplayable":
                    body, _choices = _translator_collect_displayable_text(renpy, screen)
                    if body:
                        candidates.append({
                            "screen": "scene:%s:%s" % (layer, tag),
                            "what": "\n".join(body),
                            "choices": [],
                            "zorder": getattr(entry, "zorder", 0),
                        })
                    continue

                screen_name = getattr(screen, "screen_name", ()) or ()
                if isinstance(screen_name, str):
                    screen_name = (screen_name,)
                primary_name = str(screen_name[0] if screen_name else "")
                if primary_name in ("say", "multiple_say", "nvl"):
                    continue

                is_transient = (layer, tag) in transient_screens
                if not is_transient and not bool(getattr(screen, "modal", False)):
                    continue

                body, choices = _translator_collect_displayable_text(renpy, screen)
                if not body and not choices:
                    continue

                candidates.append(
                    {
                        "screen": primary_name,
                        "what": "\n".join(body),
                        "choices": choices,
                        "zorder": getattr(entry, "zorder", 0),
                    }
                )

        if not candidates:
            return None

        # Scene-list order resolves equal zorders; the final item is the
        # screen closest to the player.
        candidates.sort(key=lambda item: item["zorder"])
        return candidates[-1]

    def _translator_custom_screen_interact_callback():
        import renpy

        global _translator_last_current_msg
        global _translator_last_visible_signature

        try:
            if _translator_get_visible_what(renpy):
                return False

            cur = _translator_get_current_node(renpy)
            if cur and cur.__class__.__name__ == "Menu":
                return False

            payload = _translator_get_custom_screen_payload(renpy)
            if not payload:
                _translator_last_visible_signature = None
                return False

            what = payload.get("what", "")
            choices = payload.get("choices", [])
            signature = (payload.get("screen", ""), what, tuple(choices))
            if signature == _translator_last_visible_signature:
                return False

            _translator_last_visible_signature = signature
            # Keep the callback independently loadable for older injected
            # hooks and small unit-test harnesses that do not provide the
            # optional canonical menu segment helper.
            choice_segments_builder = globals().get(
                "_translator_menu_choice_segments")
            choice_segments = (choice_segments_builder(renpy, cur, choices)
                               if callable(choice_segments_builder) else [])
            msg = {
                "type": "current",
                "who": "",
                "what": what,
                "italic": False,
                "choices": choices,
                "menu_active": bool(choices),
                "screen_text": True,
            }
            if payload.get("screen", "").startswith("scene:") and cur is not None:
                upcoming, _branches, debug, _continuations = _translator_predict(renpy, cur)
                msg["prefetch"] = upcoming
                msg["prefetch_debug"] = debug
            _translator_last_current_msg = dict(msg)
            _translator_start_thread(_translator_send, (msg,))
        except Exception:
            pass
        return False

    def _translator_install_display_capture(renpy):
        original = renpy.character.display_say
        if getattr(original, "_renpylens_wrapper", False):
            return

        def display_say(who, what, *args, **kwargs):
            # A game can replace config.all_character_callbacks after this
            # hook initializes. Restore our callback at the last possible
            # point before Ren'Py snapshots that list for this dialogue.
            callbacks = renpy.config.all_character_callbacks
            if _translator_callback not in callbacks:
                callbacks.append(_translator_callback)
            previous = getattr(renpy, "_renpylens_display_context", None)
            show = args[0] if args else kwargs.get("show_function")
            character = getattr(show, "__self__", getattr(show, "im_self", None))
            renpy._renpylens_display_context = (who, what, getattr(character, "screen", None))
            try:
                return original(who, what, *args, **kwargs)
            finally:
                renpy._renpylens_display_context = previous

        display_say._renpylens_wrapper = True
        renpy.character.display_say = display_say

    def _translator_resolve_who(renpy, who_value, cur_node=None, callback=False):
        try:
            string_types = (basestring,)
        except NameError:
            string_types = (str,)

        def substitute(value):
            if not isinstance(value, string_types):
                return ""
            # Failure is not a display name. Do not return the source expression.
            formatter = getattr(renpy, "substitute", None)
            if formatter is None:
                formatter = renpy.exports.substitute
            return formatter(value)

        def display_name(value):
            try:
                if isinstance(value, type):
                    return ""
                if hasattr(value, "name"):
                    character = value
                    value = character.name
                    if getattr(character, "dynamic", False):
                        value = value() if callable(value) else renpy.python.py_eval(value)
                    if value is None:
                        return ""
                    prefix = getattr(character, "who_prefix", "")
                    suffix = getattr(character, "who_suffix", "")
                    formatter = getattr(character, "prefix_suffix", None)
                    if formatter is not None:
                        value = formatter("who", prefix, value, suffix)
                    else:
                        value = substitute(prefix) + substitute(value) + substitute(suffix)
                elif callback:
                    # The engine's callback value has already passed prefix_suffix.
                    pass
                else:
                    value = substitute(value)
                import re
                if not isinstance(value, string_types):
                    return ""
                return _translator_normalize_speaker(re.sub(r"\{[^{}]*\}", "", value))
            except Exception:
                return ""

        if callback:
            return display_name(who_value)
        node_who = getattr(cur_node, "who", None)
        if who_value is not None and who_value != node_who:
            resolved = display_name(who_value)
            if resolved:
                return resolved
        expression = node_who if node_who else who_value
        if not isinstance(expression, string_types):
            return display_name(expression)
        for value in _translator_lookup_name_values(renpy, expression):
            resolved = display_name(value)
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

    def _translator_predict(renpy, current, limit=60, branch_limit=None, max_steps=1500, time_budget=0.05, choice_selector=None, input_provider=None, screen_selector=None, random_provider=None, stop_selector=None, state_names=None, label_observer=None):
        _translator_prepare_identity(renpy)
        return _translator_kernel_predict(
            renpy, current, limit=limit, branch_limit=branch_limit,
            max_steps=max_steps, time_budget=time_budget,
            choice_selector=choice_selector, input_provider=input_provider,
            screen_selector=screen_selector, random_provider=random_provider,
            stop_selector=stop_selector, state_names=state_names,
            label_observer=label_observer,
            services={
                'node_debug': _translator_node_debug,
                'menu_id': _translator_menu_id,
                'node_ref': _translator_node_ref,
                'edge_id': _translator_edge_id,
                'format_text': _translator_format_text,
                'strip_text_tags': _translator_strip_text_tags,
                'character_id': _translator_character_id,
                'prefetch_count': _translator_prefetch_count,
                'last_current_msg': _translator_last_current_msg,
            },
        )

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

    def _translator_menu_choice_segments(renpy, menu_node, choices):
        """Return stable menu option records while retaining legacy text list."""
        result = []
        wanted = set(choices or [])
        for index, item in enumerate(getattr(menu_node, "items", None) or []):
            if not item or len(item) < 3 or not item[2]:
                continue
            if not _translator_menu_item_is_visible(renpy, item):
                continue
            text = _translator_clean_text(renpy, item[0])
            if not text or text not in wanted:
                continue
            target = item[2][0]
            result.append({
                "source": text,
                "display_text": text,
                "node_id": _translator_menu_id(menu_node),
                "edge_id": _translator_edge_id(menu_node, target, "MENU_CHOICE", index),
                "choice_index": index,
            })
        return result

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
                                "character_id": _translator_character_id(getattr(node, "who", None)),
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
        global _translator_prefetch_count

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
            elif command == "set_prefetch_count":
                _translator_prefetch_count = max(3, min(20, int(message.get("count", 10))))
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
            _translator_send_type(
                "hook_ready",
                control_port=_translator_control_port,
                session_id=_translator_session_id,
                branch_prefetch=True,
            )
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
        global _translator_last_current_msg
        try:
            _translator_mark_runtime_ready()
            cur = _translator_get_current_node(renpy)
            if cur and cur.__class__.__name__ == "Menu":
                caption, choices = _translator_extract_menu_entries(renpy, cur)
                choice_segments = _translator_menu_choice_segments(renpy, cur, choices)
                signature = (caption, tuple(choices))
                if choices and signature != _translator_last_menu_signature:
                    _translator_last_menu_signature = signature
                    _translator_last_visible_signature = None
                    state_version = _translator_next_state_version()
                    upcoming, branches, prediction_debug, continuations = _translator_predict(renpy, cur)
                    menu_id = _translator_menu_id(cur)
                    _translator_menu_versions[menu_id] = state_version
                    current_msg = {
                        "type": "current",
                        "session_id": _translator_session_id,
                        "state_version": state_version,
                        "node_id": menu_id,
                        "menu_id": menu_id,
                        "prefetch_menu_id": menu_id,
                        "who": "",
                        "what": caption,
                        "italic": False,
                        "choices": choices,
                        "choice_segments": choice_segments,
                        "menu_active": True,
                        "prefetch": upcoming,
                        "prefetch_debug": prediction_debug,
                    }
                    _translator_last_current_msg = dict(current_msg)
                    branch_message = None
                    if branches:
                        branch_message = {
                            "type": "branch_prefetch",
                            "session_id": _translator_session_id,
                            "state_version": state_version,
                            "menu_id": menu_id,
                            "node_id": menu_id,
                            "linear": upcoming,
                            "branches": branches,
                        }
                    _translator_start_thread(
                        _translator_send_current_and_branches,
                        (current_msg, branch_message),
                    )
                    if branch_message and continuations:
                        _translator_schedule_branch_continuations(
                            continuations,
                            state_version,
                            branch_message,
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
                            "session_id": _translator_session_id,
                            "state_version": _translator_next_state_version(),
                            "node_id": _translator_menu_id(cur) if cur is not None else "",
                            "who": visible_who,
                            "character_id": _translator_character_id(getattr(cur, "who", None)),
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
            if visible_who is None:
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

    def _translator_menu_wrapper(*menu_args, **menu_kwargs):
        """Report the real menu result without changing Ren'Py menu semantics."""
        import renpy
        current = _translator_get_current_node(renpy)
        menu_id = _translator_menu_id(current) if type(current).__name__ == "Menu" else ""
        result = _translator_original_menu(*menu_args, **menu_kwargs)
        if menu_id and result is not None:
            edge_id = ""
            try:
                item = current.items[result]
                if item and len(item) >= 3 and item[2]:
                    edge_id = _translator_edge_id(
                        current, item[2][0], "MENU_CHOICE", result
                    )
            except Exception:
                pass
            _translator_send_type(
                "menu_selected",
                session_id=_translator_session_id,
                state_version=_translator_menu_versions.get(menu_id, _translator_state_version),
                menu_id=menu_id,
                node_id=menu_id,
                edge_id=edge_id,
                choice_index=result,
            )
        return result

    def _translator_callback(event, interact=True, **kwargs):
        import renpy

        global _translator_last_current_msg
        global _translator_last_visible_signature
        global _translator_was_rollback

        if event == "begin":
            try:
                rolling_back = bool(renpy.in_rollback())
            except Exception:
                rolling_back = False
            if rolling_back and not _translator_was_rollback:
                _translator_invalidate_route("rollback")
            _translator_was_rollback = rolling_back
            what = kwargs.get("what", "")
            raw_who = kwargs.get("who", "")
            display_context = getattr(renpy, "_renpylens_display_context", None)
            if display_context is not None:
                raw_who, captured_what = display_context[:2]
                if not what:
                    what = captured_what
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

            # At begin, the visible widget can still belong to the previous line.
            who = _translator_resolve_who(renpy, raw_who, cur,
                                          callback=display_context is not None or "who" in kwargs)
            continuation = getattr(cur, "who", None) == "extend"
            who = _translator_apply_speaker_state(who, continuation=continuation)
            character_id = _translator_character_id(
                getattr(cur, "who", None),
                (_translator_last_current_msg or {}).get("character_id", ""))

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

            # Keep option identity alongside the legacy text list.  A say
            # callback can expose a following menu, so inspect that menu when
            # the current node itself is not the menu container.
            choice_menu = cur if is_menu_node else (
                cur.next if cur and getattr(cur.next, "__class__", None).__name__ == "Menu"
                else None)
            choice_segments_builder = globals().get(
                "_translator_menu_choice_segments")
            choice_segments = (choice_segments_builder(
                renpy, choice_menu, choices)
                if callable(choice_segments_builder) else [])

            if not clean_what and not choices:
                return

            menu_active = is_menu_node or (not clean_what and bool(choices))

            msg = {
                "type": "current",
                "session_id": _translator_session_id,
                "state_version": _translator_next_state_version(),
                "node_id": _translator_menu_id(cur) if cur is not None else "",
                "who": _translator_normalize_speaker(who) if who else "",
                "character_id": character_id if who else "",
                "what": clean_what,
                "italic": is_italic,
                "choices": choices,
                "menu_active": menu_active,
            }
            if choice_segments:
                msg["choice_segments"] = choice_segments
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
            upcoming, branches, prediction_debug, continuations = _translator_predict(renpy, cur)
            prefetch_debug.update(prediction_debug)
            if upcoming:
                msg["prefetch"] = upcoming
            msg["prefetch_debug"] = prefetch_debug

            branch_message = None
            if branches:
                menu_id = branches[0].get("menu_id", "")
                msg["prefetch_menu_id"] = menu_id
                _translator_menu_versions[menu_id] = msg["state_version"]
                branch_message = {
                    "type": "branch_prefetch",
                    "session_id": _translator_session_id,
                    "state_version": msg["state_version"],
                    "menu_id": menu_id,
                    "node_id": menu_id,
                    "linear": upcoming,
                    "branches": branches,
                }
            _translator_start_thread(
                _translator_send_current_and_branches,
                (msg, branch_message),
            )
            if branch_message and continuations:
                _translator_schedule_branch_continuations(
                    continuations,
                    msg["state_version"],
                    branch_message,
                )
        elif event in ("show_done", "slow_done"):
            _translator_refresh_visible_who(renpy)
        elif event == "end":
            _translator_last_current_msg = None
            _translator_last_visible_signature = None

    try:
        config.all_character_callbacks.append(_translator_callback)

        import renpy as _translator_renpy
        _translator_install_display_capture(_translator_renpy)
        if not getattr(_translator_renpy.exports.menu, "_renpylens_wrapper", False):
            _translator_original_menu = _translator_renpy.exports.menu
            _translator_menu_wrapper._renpylens_wrapper = True
            _translator_renpy.exports.menu = _translator_menu_wrapper

        if hasattr(config, "start_interact_callbacks"):
            config.start_interact_callbacks.append(_translator_interact_callback)
        elif hasattr(config, "interact_callbacks"):
            config.interact_callbacks.append(_translator_interact_callback)

        if hasattr(config, "needs_redraw_callbacks"):
            config.needs_redraw_callbacks.append(
                _translator_custom_screen_interact_callback
            )
        elif hasattr(config, "interact_callbacks"):
            config.interact_callbacks.append(_translator_custom_screen_interact_callback)
        if hasattr(config, "after_load_callbacks"):
            config.after_load_callbacks.append(
                lambda: _translator_invalidate_route("load")
            )
    except Exception:
        pass

    _translator_start_thread(_translator_control_server)
