# Hook injected into a Ren'Py game's game/ directory.
# It forwards runtime text to RenpyLens and accepts local control commands.

init python:
    import json as _tjson
    import socket as _tsock
    import threading as _tthread
    import uuid as _tuuid
    import renpy as _translator_runtime

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
    _translator_prefetch_count = 5
    _translator_menu_versions = {}
    _translator_original_menu = None
    _translator_was_rollback = False
    _translator_scan_lock = _tthread.Lock()

    def _translator_menu_id(node):
        if node is None:
            return ""
        name = getattr(node, "name", None)
        if name:
            return _translator_debug_value(name)
        return "%s:%s" % (
            _translator_debug_value(getattr(node, "filename", "")),
            _translator_debug_value(getattr(node, "linenumber", "")),
        )

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

    def _translator_send_current_and_branches(current, branch_message):
        _translator_send(current)
        if branch_message:
            _translator_send(branch_message)

    def _translator_resume_branch_prefetch(task, state_version, branch_message):
        if state_version != _translator_state_version:
            return
        needs_more = task()
        if state_version != _translator_state_version:
            return
        # The sender runs on another thread, so freeze this revision before the
        # next continuation mutates the shared branch records.
        payload = _tjson.loads(_tjson.dumps(branch_message, ensure_ascii=False))
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
            transient_screens = set(
                getattr(scene_lists, "additional_transient", None) or []
            )
            layers = getattr(scene_lists, "layers", None) or {}
        except Exception:
            return None

        candidates = []
        for layer, entries in layers.items():
            for entry in (entries or []):
                screen = getattr(entry, "displayable", None)
                if screen is None or screen.__class__.__name__ != "ScreenDisplayable":
                    continue

                tag = getattr(entry, "tag", None)
                if "$" in str(tag or ""):
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
            msg = {
                "type": "current",
                "who": "",
                "what": what,
                "italic": False,
                "choices": choices,
                "menu_active": bool(choices),
                "screen_text": True,
            }
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
            return renpy.substitute(value)

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

    def _translator_predict(renpy, current, limit=60, branch_limit=None, max_steps=1500, time_budget=0.05, choice_selector=None, input_provider=None, screen_selector=None, random_provider=None):
        """Interpret a bounded script prefix without executing game Python.

        Only plain data and explicitly supported syntax enter the shadow store.
        Unknown operations end the prefix; they must never be skipped while
        continuing to report subsequent dialogue as belonging to this route.
        """
        import ast
        import operator
        import re
        import time
        try:
            import builtins as builtin
        except ImportError:
            import __builtin__ as builtin
        # Ren'Py replaces store.list/dict/set with revertable subclasses. Type
        # checks must also recognize ordinary objects from engine modules.
        list, tuple, dict, set = builtin.list, builtin.tuple, builtin.dict, builtin.set
        str, int, float, bool, object = builtin.str, builtin.int, builtin.float, builtin.bool, builtin.object
        len, abs, min, max, round, any, all = builtin.len, builtin.abs, builtin.min, builtin.max, builtin.round, builtin.any, builtin.all

        clock = getattr(time, "perf_counter", time.time)
        started = clock()
        slice_started = [started]
        slice_steps = [0]
        steps = [0]
        memo = {}
        shadow = {}
        audio_scope = [False]
        missing = object()
        upcoming = []
        branches = []
        continuations = []
        node = current if type(current).__name__ == "Menu" else getattr(current, "next", None)
        debug = {
            "stop_reason": "next-none",
            "visited_nodes": 0,
            "prefetch_items": 0,
            "route_choices": [],
            "route_inputs": [],
            "route_screens": [],
            "route_random": [],
        }
        if branch_limit is None:
            branch_limit = _translator_prefetch_count
        branch_limit = max(0, int(branch_limit))
        try:
            string_types = (str, unicode)
            number_types = (int, long, float, bool)
        except NameError:
            string_types = (str,)
            number_types = (int, float, bool)
        scalar_types = string_types + number_types + (type(None),)
        containers = {list: list, tuple: tuple, dict: dict, set: set}
        for module_name in ("revertable", "python"):
            module = getattr(renpy, module_name, None)
            for name, base in (("RevertableList", list), ("RevertableDict", dict), ("RevertableSet", set)):
                kind = getattr(module, name, None)
                if kind is not None:
                    containers[kind] = base

        class Stop(Exception):
            pass

        def tick():
            steps[0] += 1
            slice_steps[0] += 1
            if slice_steps[0] > max_steps or clock() - slice_started[0] > time_budget:
                raise Stop("work-budget")

        def reset_slice():
            slice_steps[0] = 0
            slice_started[0] = clock()

        def clone(value):
            tick()
            kind = type(value)
            if kind in scalar_types:
                return value
            if id(value) in memo:
                return memo[id(value)]
            base = containers.get(kind)
            if base is None:
                raise Stop("unsupported-value")
            if len(value) > 4096:
                raise Stop("value-budget")
            result = {} if base is dict else [] if base in (list, tuple) else set()
            memo[id(value)] = result
            if base is dict:
                for key, item in value.items():
                    result[clone(key)] = clone(item)
            else:
                for item in value:
                    if base is set:
                        result.add(clone(item))
                    else:
                        result.append(clone(item))
            if base is tuple:
                result = tuple(result)
                memo[id(value)] = result
            memo[id(result)] = result
            return result

        def fork_value(value, copies=None):
            """Clone prediction-owned data while retaining immutable AST nodes."""
            if copies is None:
                copies = {}
            if value is missing or type(value) in scalar_types:
                return value
            value_id = id(value)
            if value_id in copies:
                return copies[value_id]
            base = containers.get(type(value))
            if base is None:
                return value
            result = {} if base is dict else [] if base in (list, tuple) else set()
            copies[value_id] = result
            if base is dict:
                for key, item in value.items():
                    result[fork_value(key, copies)] = fork_value(item, copies)
            else:
                for item in value:
                    if base is set:
                        result.add(fork_value(item, copies))
                    else:
                        result.append(fork_value(item, copies))
            if base is tuple:
                result = tuple(result)
                copies[value_id] = result
            return result

        def read(name):
            if name not in shadow:
                if name.startswith("persistent."):
                    shadow[name] = clone(renpy.store.persistent.__dict__.get(name[11:], None))
                else:
                    shadow[name] = clone(renpy.store.__dict__[name])
            if shadow[name] is missing:
                raise Stop("missing-variable")
            return shadow[name]

        binary = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                  ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
                  ast.Div: operator.truediv, ast.BitAnd: operator.and_, ast.BitOr: operator.or_}
        comparisons = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
                       ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
                       ast.Is: operator.is_, ast.IsNot: operator.is_not,
                       ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}

        def operation(op, left, right):
            if isinstance(op, ast.Mult):
                sequence, count = (left, right) if isinstance(right, number_types) else (right, left)
                if isinstance(sequence, (list, tuple) + string_types) and len(sequence) * abs(count) > 4096:
                    raise Stop("value-budget")
            if isinstance(op, ast.Mod) and isinstance(left, string_types):
                raise Stop("unsupported-format-operation")
            if type(op) not in binary:
                raise Stop("unsupported-operator")
            value = binary[type(op)](left, right)
            if isinstance(value, (list, tuple) + string_types) and len(value) > 4096:
                raise Stop("value-budget")
            return value

        def expression(tree):
            tick()
            kind = type(tree).__name__
            if kind in ("Constant", "Num", "Str", "NameConstant"):
                return clone(getattr(tree, "value", getattr(tree, "n", getattr(tree, "s", None))))
            if isinstance(tree, ast.Name):
                if tree.id in ("True", "False", "None"):
                    return {"True": True, "False": False, "None": None}[tree.id]
                if audio_scope[0]:
                    audio = getattr(renpy.store, "audio", None)
                    if audio is not None and tree.id in audio.__dict__:
                        return clone(audio.__dict__[tree.id])
                return read(tree.id)
            if isinstance(tree, (ast.List, ast.Tuple, ast.Set)):
                values = [expression(x) for x in tree.elts]
                return tuple(values) if isinstance(tree, ast.Tuple) else set(values) if isinstance(tree, ast.Set) else values
            if isinstance(tree, ast.Dict):
                return dict((expression(k), expression(v)) for k, v in zip(tree.keys, tree.values))
            if isinstance(tree, ast.BinOp):
                return operation(tree.op, expression(tree.left), expression(tree.right))
            if isinstance(tree, ast.UnaryOp):
                ops = {ast.Not: operator.not_, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Invert: operator.invert}
                return ops[type(tree.op)](expression(tree.operand))
            if isinstance(tree, ast.BoolOp):
                for part in tree.values:
                    value = expression(part)
                    if isinstance(tree.op, ast.And) and not value or isinstance(tree.op, ast.Or) and value:
                        return value
                return value
            if isinstance(tree, ast.Compare):
                left = expression(tree.left)
                for op, part in zip(tree.ops, tree.comparators):
                    right = expression(part)
                    if not comparisons[type(op)](left, right):
                        return False
                    left = right
                return True
            if isinstance(tree, ast.IfExp):
                return expression(tree.body if expression(tree.test) else tree.orelse)
            if isinstance(tree, ast.Subscript):
                return expression(tree.value)[expression(tree.slice)]
            if isinstance(tree, ast.Attribute) and isinstance(tree.value, ast.Name) and tree.value.id == "persistent" and not tree.attr.startswith("_"):
                return read("persistent." + tree.attr)
            if (isinstance(tree, ast.Attribute) and isinstance(tree.value, ast.Name)
                    and tree.value.id in ("preferences", "config")
                    and not tree.attr.startswith("_")):
                root_name = tree.value.id
                runtime = renpy.store.__dict__.get(root_name)
                native = (getattr(renpy.game, "preferences", None) if root_name == "preferences"
                          else getattr(renpy, "config", None))
                values = getattr(runtime, "__dict__", {})
                if runtime is not native or tree.attr not in values:
                    raise Stop("custom-engine-object")
                return clone(values[tree.attr])
            if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name):
                pure = {"len": len, "abs": abs, "min": min, "max": max, "int": int,
                        "float": float, "str": str, "bool": bool, "round": round,
                        "any": any, "all": all}
                function = pure.get(tree.func.id)
                if function is not None and not tree.keywords and not getattr(tree, "starargs", None) and not getattr(tree, "kwargs", None):
                    if tree.func.id in shadow or (tree.func.id in renpy.store.__dict__ and renpy.store.__dict__[tree.func.id] is not function):
                        raise Stop("overridden-builtin")
                    return function(*[expression(arg) for arg in tree.args])
                if tree.func.id == "clamp":
                    candidate = renpy.store.__dict__.get("clamp")
                    code = getattr(candidate, "__code__", getattr(candidate, "func_code", None))
                    names = set(getattr(code, "co_names", ())) if code else set()
                    globals_dict = getattr(candidate, "__globals__", getattr(candidate, "func_globals", {}))
                    if (candidate is None or code is None
                            or int(getattr(code, "co_argcount", -1)) != 3
                            or names - set(("min", "max"))
                            or globals_dict.get("min", min) is not min
                            or globals_dict.get("max", max) is not max
                            or tree.keywords
                            or getattr(tree, "starargs", None)
                            or getattr(tree, "kwargs", None)
                            or len(tree.args) != 3):
                        raise Stop("unsupported-clamp")
                    value, lower, upper = [expression(arg) for arg in tree.args]
                    return max(lower, min(value, upper))
            if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Attribute):
                random_namespace = tree.func.value
                if (isinstance(random_namespace, ast.Attribute)
                        and isinstance(random_namespace.value, ast.Name)
                        and random_namespace.value.id == "renpy"
                        and random_namespace.attr == "random"
                        and tree.func.attr == "randint"):
                    if random_provider is None:
                        raise Stop("random-boundary")
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    runtime_rng = getattr(store_renpy, "random", None)
                    rollback = getattr(renpy, "rollback", None)
                    native_rng = getattr(rollback, "rng", None)
                    if (runtime_rng is None or runtime_rng is not native_rng
                            or "renpy" in shadow
                            or len(tree.args) != 2 or tree.keywords
                            or getattr(tree, "starargs", None)
                            or getattr(tree, "kwargs", None)):
                        raise Stop("custom-random-handler")
                    lower, upper = [expression(arg) for arg in tree.args]
                    if not isinstance(lower, int) or not isinstance(upper, int):
                        raise Stop("unsupported-random-arguments")
                    value = random_provider({
                        "method": "randint", "args": [lower, upper],
                    })
                    if not isinstance(value, int) or value < lower or value > upper:
                        raise Stop("unsupported-random-value")
                    debug["route_random"].append({
                        "method": "randint", "args": [lower, upper], "value": value,
                    })
                    return value
                if (isinstance(tree.func.value, ast.Name)
                        and tree.func.value.id == "renpy"
                        and tree.func.attr == "input"):
                    if input_provider is None:
                        raise Stop("interactive-input")
                    exports_module = getattr(renpy, "exports", None)
                    native = getattr(exports_module, "input", None)
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    runtime_input = getattr(store_renpy, "input", None)
                    code = getattr(runtime_input, "__code__", getattr(runtime_input, "func_code", None))
                    filename = code.co_filename.replace("\\", "/") if code else ""
                    if (runtime_input is None or runtime_input is not native
                            or not filename.endswith("renpy/exports/inputexports.py")
                            or "renpy" in shadow):
                        raise Stop("custom-input-handler")
                    if (getattr(tree, "starargs", None)
                            or getattr(tree, "kwargs", None)
                            or any(keyword.arg is None for keyword in tree.keywords)):
                        raise Stop("unsupported-input-arguments")
                    args = [expression(arg) for arg in tree.args]
                    kwargs = dict((keyword.arg, expression(keyword.value)) for keyword in tree.keywords)
                    if not args and "prompt" not in kwargs:
                        raise Stop("unsupported-input-arguments")
                    prompt = args[0] if args else kwargs.get("prompt", "")
                    default = args[1] if len(args) > 1 else kwargs.get("default", "")
                    value = input_provider({"prompt": str(prompt), "default": str(default or "")})
                    if not isinstance(value, string_types):
                        raise Stop("unsupported-input-value")
                    debug["route_inputs"].append({
                        "prompt": str(prompt),
                        "default": str(default or ""),
                        "value": value,
                    })
                    return value
                if tree.func.attr in ("strip", "lstrip", "rstrip"):
                    if (tree.keywords or getattr(tree, "starargs", None)
                            or getattr(tree, "kwargs", None) or len(tree.args) > 1):
                        raise Stop("unsupported-string-method")
                    value = expression(tree.func.value)
                    if not isinstance(value, string_types):
                        raise Stop("unsupported-string-method")
                    args = [expression(arg) for arg in tree.args]
                    return getattr(value, tree.func.attr)(*args)
                if tree.func.attr in ("lower", "upper"):
                    if (tree.args or tree.keywords or getattr(tree, "starargs", None)
                            or getattr(tree, "kwargs", None)):
                        raise Stop("unsupported-string-method")
                    value = expression(tree.func.value)
                    if not isinstance(value, string_types):
                        raise Stop("unsupported-string-method")
                    return getattr(value, tree.func.attr)()
            if kind == "Index":
                return expression(tree.value)
            if isinstance(tree, ast.Slice):
                return slice(*[expression(x) if x is not None else None for x in (tree.lower, tree.upper, tree.step)])
            # Never invoke even a seemingly harmless game function: its globals
            # and closures still refer to the real store.
            raise Stop("unsupported-expression:" + kind)

        def evaluate(source):
            return expression(ast.parse(source, mode="eval").body)

        def assign(target, value):
            if isinstance(target, ast.Name):
                shadow[target.id] = value
            elif isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "persistent" and not target.attr.startswith("_"):
                shadow["persistent." + target.attr] = value
            elif isinstance(target, (ast.Tuple, ast.List)):
                if len(target.elts) != len(value):
                    raise Stop("unpack-mismatch")
                for item, part in zip(target.elts, value):
                    assign(item, part)
            elif isinstance(target, ast.Subscript):
                expression(target.value)[expression(target.slice)] = value
            else:
                raise Stop("unsupported-assignment")

        def statements(body):
            for statement in body:
                tick()
                if isinstance(statement, ast.Assign):
                    value = expression(statement.value)
                    for target in statement.targets:
                        assign(target, value)
                elif isinstance(statement, ast.AugAssign):
                    old = expression(statement.target)
                    value = operation(statement.op, old, expression(statement.value))
                    if isinstance(old, list) and isinstance(statement.op, (ast.Add, ast.Mult)):
                        old[:] = value
                        value = old
                    assign(statement.target, value)
                elif isinstance(statement, ast.If):
                    statements(statement.body if expression(statement.test) else statement.orelse)
                elif isinstance(statement, ast.Pass):
                    pass
                elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
                    call = statement.value
                    function = call.func
                    namespace = function.value if isinstance(function, ast.Attribute) else None
                    if (isinstance(namespace, ast.Name)
                            and namespace.id == "renpy"
                            and function.attr == "end_replay"):
                        exports_module = getattr(renpy, "exports", None)
                        native = getattr(exports_module, "end_replay", None)
                        store_renpy = renpy.store.__dict__.get("renpy", None)
                        runtime = getattr(store_renpy, "end_replay", None)
                        code = getattr(runtime, "__code__", getattr(runtime, "func_code", None))
                        filename = code.co_filename.replace("\\", "/") if code else ""
                        if (runtime is None or runtime is not native
                                or not filename.endswith("renpy/exports/contextexports.py")
                                or call.args or call.keywords
                                or getattr(call, "starargs", None)
                                or getattr(call, "kwargs", None)
                                or "renpy" in shadow):
                            raise Stop("custom-end-replay-handler")
                        if read("_in_replay"):
                            raise Stop("replay-boundary")
                        continue
                    if (isinstance(namespace, ast.Name)
                            and namespace.id == "renpy"
                            and function.attr == "pause"):
                        exports_module = getattr(renpy, "exports", None)
                        native = getattr(exports_module, "pause", None)
                        store_renpy = renpy.store.__dict__.get("renpy", None)
                        pause = getattr(store_renpy, "pause", None)
                        code = getattr(pause, "__code__", getattr(pause, "func_code", None))
                        filename = code.co_filename.replace("\\", "/") if code else ""
                        if (pause is None or pause is not native
                                or not filename.endswith("renpy/exports/statementexports.py")
                                or "renpy" in shadow):
                            raise Stop("custom-pause-handler")
                        if (getattr(call, "starargs", None)
                                or getattr(call, "kwargs", None)
                                or any(keyword.arg is None for keyword in call.keywords)):
                            raise Stop("unsupported-pause-arguments")
                        import inspect
                        try:
                            inspect.getcallargs(
                                pause,
                                *([None] * len(call.args)),
                                **dict((keyword.arg, None) for keyword in call.keywords)
                            )
                        except TypeError:
                            raise Stop("unsupported-pause-arguments")
                        for argument in call.args:
                            expression(argument)
                        for keyword in call.keywords:
                            expression(keyword.value)
                        continue
                    elif (not isinstance(namespace, ast.Attribute)
                            or not isinstance(namespace.value, ast.Name)
                            or namespace.value.id != "renpy"
                            or namespace.attr not in ("sound", "music")
                            or function.attr not in ("play", "queue", "stop", "set_volume", "set_pan", "set_pause")):
                        raise Stop("unsupported-python:Expr")
                    store_renpy = renpy.store.__dict__.get("renpy", None)
                    module = getattr(store_renpy, namespace.attr, None)
                    if module is None:
                        module = getattr(renpy, namespace.attr, None)
                    native = getattr(getattr(renpy, "audio", None), namespace.attr, None)
                    stop = getattr(module, function.attr, None)
                    code = getattr(stop, "__code__", getattr(stop, "func_code", None))
                    filename = code.co_filename.replace("\\", "/") if code else ""
                    if (stop is None or stop is not getattr(native, function.attr, None)
                            or not filename.endswith(("renpy/audio/sound.py", "renpy/audio/music.py"))
                            or "renpy" in shadow):
                        raise Stop("custom-audio-handler")
                    if (getattr(call, "starargs", None)
                            or getattr(call, "kwargs", None)
                            or any(keyword.arg is None for keyword in call.keywords)):
                        raise Stop("unsupported-audio-arguments")
                    import inspect
                    try:
                        inspect.getcallargs(stop, *([None] * len(call.args)),
                                            **dict((keyword.arg, None) for keyword in call.keywords))
                    except TypeError:
                        raise Stop("unsupported-audio-arguments")
                    # Validate only the data arguments. Never stop real audio
                    # while predicting the dialogue after this statement.
                    for argument in call.args:
                        expression(argument)
                    for keyword in call.keywords:
                        expression(keyword.value)
                elif isinstance(statement, ast.Expr):
                    expression(statement.value)
                else:
                    raise Stop("unsupported-python:" + type(statement).__name__)

        def text_value(source, strip=True, translate=False):
            if translate:
                translator = getattr(getattr(renpy, "translation", None), "translate_string", None)
                if translator is not None:
                    source = translator(source)
            def resolve(code, conversion, spec):
                if spec or conversion and conversion != "s":
                    raise Stop("unsupported-substitution-format")
                return str(evaluate(code))
            # Share syntax handling, while evaluating only in the shadow store.
            try:
                source = _translator_format_text(renpy, source, resolve)
            except ValueError as error:
                raise Stop(str(error))
            source = _translator_strip_text_tags(source)
            return source.strip() if strip else source

        def unwrap_curried(candidate):
            curry_module = getattr(renpy, "curry", None)
            partial_type = getattr(curry_module, "Partial", None)
            curry_type = getattr(curry_module, "Curry", None)
            if partial_type is not None and type(candidate) is partial_type:
                if (candidate.func is partial_type and len(candidate.args) == 1
                        and not candidate.keywords):
                    return candidate.args[0]
            elif curry_type is not None and type(candidate) is curry_type:
                if (candidate.callable is curry_type and len(candidate.args) == 1
                        and not candidate.kwargs):
                    return candidate.args[0]
            return candidate

        def validate_transition(source):
            def transition_value(part):
                if isinstance(part, (ast.Tuple, ast.List)):
                    for item in part.elts:
                        transition_value(item)
                    return
                if not isinstance(part, ast.Call):
                    expression(part)
                    return
                # Native transition constructors affect presentation only.
                # Check identity and arguments without constructing them.
                name = part.func.id if isinstance(part.func, ast.Name) else None
                native = getattr(getattr(getattr(renpy, "display", None), "transition", None), name or "", None)
                constructor = renpy.store.__dict__.get(name)
                constructor = unwrap_curried(constructor)
                if (name not in ("Dissolve", "Fade", "Pixellate", "ImageDissolve", "CropMove", "MultipleTransition",
                                 "AlphaDissolve", "PushMove", "ComposeTransition", "SubTransition")
                        or native is None or constructor is not native
                        or name in shadow):
                    raise Stop("dynamic-transition")
                for argument in part.args:
                    transition_value(argument)
                for keyword in part.keywords:
                    transition_value(keyword.value)

            tree = ast.parse(source, mode="eval").body
            if any(isinstance(part, ast.Call) for part in ast.walk(tree)):
                transition_value(tree)

        def validate_image_transform(source):
            def transform_value(part):
                if isinstance(part, (ast.Tuple, ast.List)):
                    for item in part.elts:
                        transform_value(item)
                    return
                if not isinstance(part, ast.Call):
                    expression(part)
                    return

                name = part.func.id if isinstance(part.func, ast.Name) else None
                native_paths = {
                    "Alpha": ("layout", "Alpha"),
                    "Position": ("layout", "Position"),
                    "Pan": ("motion", "Pan"),
                    "Move": ("motion", "Move"),
                    "Motion": ("motion", "Motion"),
                    "Revolve": ("motion", "Revolve"),
                    "Zoom": ("motion", "Zoom"),
                    "RotoZoom": ("motion", "RotoZoom"),
                    "FactorZoom": ("motion", "FactorZoom"),
                    "SizeZoom": ("motion", "SizeZoom"),
                    "Transform": ("transform", "Transform"),
                    "Camera": ("transform", "Camera"),
                }
                path = native_paths.get(name)
                display = getattr(renpy, "display", None)
                module = getattr(display, path[0], None) if path else None
                native = getattr(module, path[1], None) if path else None
                constructor = unwrap_curried(renpy.store.__dict__.get(name))
                if (native is None or constructor is not native or name in shadow
                        or getattr(part, "starargs", None)
                        or getattr(part, "kwargs", None)
                        or any(keyword.arg is None for keyword in part.keywords)):
                    raise Stop("dynamic-image-transform")
                for argument in part.args:
                    transform_value(argument)
                for keyword in part.keywords:
                    transform_value(keyword.value)

            tree = ast.parse(source, mode="eval").body
            if any(isinstance(part, ast.Call) for part in ast.walk(tree)):
                transform_value(tree)

        def passive_statement(statement):
            parsed = getattr(statement, "parsed", None)
            if not parsed:
                raise Stop("unparsed-user-statement")
            name = tuple(parsed[0])
            if name in (("show", "screen"), ("hide", "screen"), ("call", "screen")):
                execute = renpy.statements.get("execute", parsed)
                code = getattr(execute, "__code__", getattr(execute, "func_code", None))
                filename = code.co_filename.replace("\\", "/") if code else ""
                if not filename.endswith("renpy/common/000statements.rpy"):
                    raise Stop("custom-statement-handler")
                data = parsed[1]
                if not isinstance(data, dict):
                    raise Stop("unsupported-statement-data")

                screen_name = data.get("name")
                if data.get("expression", False):
                    screen_name = evaluate(screen_name)
                if not isinstance(screen_name, string_types):
                    raise Stop("unsupported-screen-name")

                arguments = data.get("arguments")
                positional, keywords = [], {}
                if arguments is not None:
                    if (getattr(arguments, "starred_indexes", None)
                            or getattr(arguments, "doublestarred_indexes", None)
                            or getattr(arguments, "extrapos", None)
                            or getattr(arguments, "extrakw", None)):
                        raise Stop("unsupported-argument-unpacking")
                    for key, source in arguments.arguments:
                        value = evaluate(source)
                        if key is None:
                            positional.append(value)
                        else:
                            keywords[key] = value
                if data.get("zorder") is not None:
                    evaluate(data["zorder"])
                if data.get("transition_expr") is not None:
                    validate_transition(data["transition_expr"])
                if name == ("call", "screen"):
                    if screen_selector is None:
                        raise Stop("interactive-screen")
                    selection = screen_selector({
                        "name": screen_name,
                        "args": positional,
                        "kwargs": keywords,
                        "evaluate": evaluate,
                    })
                    if not isinstance(selection, dict):
                        raise Stop("unsupported-screen-selection")
                    record = {"name": screen_name}
                    if "jump" in selection:
                        target = selection["jump"]
                        if not isinstance(target, string_types):
                            raise Stop("unsupported-screen-selection")
                        try:
                            label = renpy.game.script.lookup(target)
                        except Exception:
                            raise Stop("unsupported-screen-selection")
                        record["jump"] = target
                        debug["route_screens"].append(record)
                        return label
                    if "value" in selection:
                        shadow["_return"] = clone(selection["value"])
                        record["value"] = selection["value"]
                        debug["route_screens"].append(record)
                        return
                    if selection.get("end") is True:
                        record["end"] = True
                        debug["route_screens"].append(record)
                        raise Stop("screen-end")
                    raise Stop("unsupported-screen-selection")
                return
            if name in (("window", "show"), ("window", "hide"), ("window", "auto")):
                execute = renpy.statements.get("execute", parsed)
                code = getattr(execute, "__code__", getattr(execute, "func_code", None))
                filename = code.co_filename.replace("\\", "/") if code else ""
                if not filename.endswith("renpy/common/000window.rpy"):
                    raise Stop("custom-statement-handler")
                data = parsed[1]
                if name in (("window", "show"), ("window", "hide")):
                    if data is not None:
                        validate_transition(data)
                else:
                    if not isinstance(data, dict):
                        raise Stop("unsupported-statement-data")
                    for key, source in data.items():
                        if key in ("show", "hide"):
                            validate_transition(source)
                        elif key == "auto":
                            evaluate(source)
                        else:
                            raise Stop("unsupported-statement-data")
                return
            if name not in (("pause",), ("play",), ("queue",), ("stop",),
                            ("play", "music"), ("play", "sound"), ("queue", "music"),
                            ("queue", "sound"), ("stop", "music"), ("stop", "sound"), ("voice",)):
                raise Stop("unsupported-user-statement:" + " ".join(name))
            execute = renpy.statements.get("execute", parsed)
            code = getattr(execute, "__code__", getattr(execute, "func_code", None))
            filename = code.co_filename.replace("\\", "/") if code else ""
            if not filename.endswith(("renpy/common/000statements.rpy", "renpy/common/00voice.rpy")):
                raise Stop("custom-statement-handler")
            data = parsed[1]
            if not isinstance(data, dict):
                raise Stop("unsupported-statement-data")
            for key in ("file", "delay", "fadeout", "fadein", "channel", "volume"):
                if data.get(key) is not None:
                    audio_scope[0] = key == "file"
                    try:
                        evaluate(data[key])
                    finally:
                        audio_scope[0] = False

        def remember(frame, name):
            if name in frame:
                return
            if name in shadow:
                frame[name] = shadow[name]
            elif name in renpy.store.__dict__:
                frame[name] = read(name)
            else:
                frame[name] = missing

        def bind(label, args, kwargs, frame):
            parameters = getattr(label, "parameters", None)
            if parameters is None:
                if args or kwargs:
                    raise Stop("unexpected-arguments")
                return
            params = parameters.parameters
            if isinstance(params, dict):
                entries = []
                for param in params.values():
                    if int(param.kind) not in (0, 1):
                        raise Stop("unsupported-parameter-kind")
                    default = param.default
                    entries.append((param.name, None if default is param.empty else default))
            else:
                if getattr(parameters, "extrapos", None) or getattr(parameters, "extrakw", None):
                    raise Stop("unsupported-parameter-kind")
                entries = list(params)
            if len(args) > len(entries):
                raise Stop("unexpected-arguments")
            values = {}
            for index, (name, default) in enumerate(entries):
                if index < len(args):
                    if name in kwargs:
                        raise Stop("duplicate-argument")
                    value = args[index]
                elif name in kwargs:
                    value = kwargs.pop(name)
                elif default is not None:
                    value = evaluate(default)
                else:
                    raise Stop("missing-argument")
                values[name] = value
            if kwargs:
                raise Stop("unexpected-arguments")
            for name, value in values.items():
                remember(frame, name)
                shadow[name] = value

        def resolve_say(statement):
            if getattr(statement, "arguments", None):
                raise Stop("say-arguments")
            clean = text_value(statement.what or "")
            raw_who = getattr(statement, "who", None)
            who = raw_who or ""
            if raw_who in shadow:
                who = read(raw_who)
            elif raw_who and raw_who in renpy.store.__dict__:
                character = renpy.store.__dict__[raw_who]
                if type(character).__module__ == "renpy.character":
                    who = character.__dict__.get("name")
                    if character.__dict__.get("dynamic", False):
                        who = evaluate(who)
                    if who is not None:
                        who = (text_value(character.__dict__.get("who_prefix", ""), strip=False, translate=True)
                               + text_value(who, strip=False, translate=True)
                               + text_value(character.__dict__.get("who_suffix", ""), strip=False, translate=True)).strip()
                    return {
                        "who": who or "",
                        "what": clean,
                        "italic": bool(statement.what.strip().startswith("{i}")
                                       and statement.what.strip().endswith("{/i}")),
                    }
                elif type(character) in string_types:
                    who = read(raw_who)
                else:
                    raise Stop("unsupported-speaker")
            elif raw_who:
                who = evaluate(raw_who)
            return {
                "who": text_value(who or ""),
                "what": clean,
                "italic": bool(
                    statement.what.strip().startswith("{i}")
                    and statement.what.strip().endswith("{/i}")
                ),
            }

        def advance(statement, active_frame_box):
            kind = type(statement).__name__
            next_node = getattr(statement, "next", None)
            if kind in ("Translate", "TranslateSay") and getattr(statement, "language", None) is None:
                language = getattr(getattr(renpy.game, "preferences", None), "language", None)
                if language is not None:
                    translated = statement.lookup()
                    if translated is not None and translated is not statement:
                        return translated
            if kind == "If":
                for condition, block in statement.entries:
                    if condition is None or condition is True or evaluate(condition):
                        return block[0] if block else next_node
            elif kind == "While":
                if evaluate(statement.condition):
                    return statement.block[0]
            elif kind in ("Jump", "Call"):
                target = statement.target if kind == "Jump" else statement.label
                if statement.expression:
                    target = evaluate(target)
                    if target.startswith("."):
                        target = getattr(statement, "global_label", "") + target
                label = renpy.game.script.lookup(target)
                args, kwargs = [], {}
                if kind == "Call":
                    arguments = getattr(statement, "arguments", None)
                    if arguments:
                        if (getattr(arguments, "starred_indexes", None)
                                or getattr(arguments, "doublestarred_indexes", None)
                                or getattr(arguments, "extrapos", None)
                                or getattr(arguments, "extrakw", None)):
                            raise Stop("unsupported-argument-unpacking")
                        for key, source in arguments.arguments:
                            value = evaluate(source)
                            if key is None:
                                args.append(value)
                            else:
                                kwargs[key] = value
                    active_frame = {}
                    frames.append((next_node, active_frame))
                    active_frame_box[0] = active_frame
                    for name in ("_args", "_kwargs"):
                        remember(active_frame, name)
                        shadow[name] = None
                bind(label, args, kwargs, active_frame_box[0])
                return label.next
            elif kind == "Return":
                shadow["_return"] = evaluate(statement.expression) if statement.expression else None
                if not frames:
                    raise Stop("return-boundary")
                site, frame = frames.pop()
                for name, value in frame.items():
                    if "." in name:
                        raise Stop("unsupported-dynamic-store")
                    shadow[name] = missing if type(value).__name__ == "Delete" or value is missing else clone(value)
                active_frame_box[0] = dict(frames[-1][1]) if frames else {}
                if frames:
                    frames[-1] = (frames[-1][0], active_frame_box[0])
                return renpy.game.script.lookup(site) if isinstance(site, string_types + (tuple,)) else site
            elif kind == "Python":
                if getattr(statement, "hide", False) or getattr(statement, "store", "store") != "store":
                    raise Stop("unsupported-python-store")
                # As with label callbacks, prediction does not run runtime
                # notifications. Their registration alone must not block
                # supported assignments in the isolated shadow store.
                statements(ast.parse(statement.code.source).body)
            elif kind == "UserStatement":
                destination = passive_statement(statement)
                if destination is not None:
                    return destination
            elif kind == "Label":
                bind(statement, [], {}, active_frame_box[0])
            elif kind in ("Scene", "Show", "Hide"):
                imspec = getattr(statement, "imspec", None)
                if imspec and imspec[1] is not None:
                    evaluate(imspec[1])
                if imspec:
                    for source in imspec[3]:
                        validate_image_transform(source)
            elif kind == "With":
                validate_transition(getattr(statement, "expr", "None"))
            elif kind not in ("Say", "TranslateSay", "Pass", "Init", "Translate", "EndTranslate"):
                raise Stop("unsupported-node:" + kind)
            return next_node

        def resume_branch(task, active_frame_box):
            task["slices"] += 1
            restored = fork_value(task["state"])
            shadow.clear()
            shadow.update(restored[0])
            frames[:] = restored[1]
            active_frame_box[0] = frames[-1][1] if frames else {}
            reset_slice()
            branch_record = task["record"]
            branch_node = task["node"]
            try:
                while branch_node is not None and len(branch_record["items"]) < branch_limit:
                    checkpoint = fork_value((shadow, frames))
                    tick()
                    if type(branch_node).__name__ in ("Translate", "TranslateSay") and getattr(branch_node, "language", None) is None:
                        language = getattr(getattr(renpy.game, "preferences", None), "language", None)
                        if language is not None:
                            translated = branch_node.lookup()
                            if translated is not None and translated is not branch_node:
                                branch_node = translated
                                task["node"] = branch_node
                                task["state"] = fork_value((shadow, frames))
                                continue
                    if type(branch_node).__name__ == "Menu":
                        raise Stop("menu-boundary")
                    if type(branch_node).__name__ in ("Say", "TranslateSay"):
                        say_item = resolve_say(branch_node)
                        if say_item["what"]:
                            branch_record["items"].append(say_item)
                    branch_node = advance(branch_node, active_frame_box)
                    task["node"] = branch_node
                    task["state"] = fork_value((shadow, frames))
                branch_record["complete"] = True
                branch_record["stop_reason"] = (
                    "prefetch-limit"
                    if branch_node is not None and len(branch_record["items"]) >= branch_limit
                    else "next-none"
                )
                return False
            except Stop as error:
                reason = str(error)
                branch_record["stop_reason"] = reason
                branch_record["stop_node"] = _translator_node_debug(branch_node)
                if reason == "work-budget":
                    shadow.clear()
                    shadow.update(checkpoint[0])
                    frames[:] = checkpoint[1]
                    task["node"] = branch_node
                    task["state"] = fork_value((shadow, frames))
                    branch_record["complete"] = False
                    return task["slices"] < 16
                branch_record["complete"] = True
                return False
            except Exception as error:
                branch_record["complete"] = True
                branch_record["stop_reason"] = "prediction-error"
                branch_record["stop_node"] = _translator_node_debug(branch_node)
                branch_record["error"] = type(error).__name__ + ": " + str(error)[:160]
                return False

        def collect_menu(menu_node, active_frame_box):
            menu_id = _translator_menu_id(menu_node)
            base = fork_value((shadow, frames))
            reset_slice()
            set_value = None
            set_expression = getattr(menu_node, "set", None)
            if set_expression:
                set_value = evaluate(set_expression)
                if not isinstance(set_value, (list, set)):
                    raise Stop("unsupported-menu-set")
            for item_index, item in enumerate(getattr(menu_node, "items", None) or []):
                reset_slice()
                restored = fork_value(base)
                shadow.clear()
                shadow.update(restored[0])
                frames[:] = restored[1]
                active_frame_box[0] = frames[-1][1] if frames else {}
                if not item or len(item) < 3 or not item[2]:
                    continue
                condition = item[1]
                if condition not in (None, True):
                    if condition is False or not isinstance(condition, string_types) or not evaluate(condition):
                        continue
                raw_choice = item[0] or ""
                if set_value is not None and raw_choice in evaluate(set_expression):
                    continue
                choice_text = text_value(raw_choice)
                branch_record = {
                    "menu_id": menu_id,
                    "choice_index": item_index,
                    "choice": choice_text,
                    "items": [],
                    "complete": True,
                    "stop_reason": "next-none",
                }
                if set_expression:
                    selected_set = evaluate(set_expression)
                    if isinstance(selected_set, list):
                        selected_set.append(raw_choice)
                    else:
                        selected_set.add(raw_choice)
                branches.append(branch_record)
                task = {
                    "node": item[2][0],
                    "state": fork_value((shadow, frames)),
                    "record": branch_record,
                    "slices": 0,
                }
                if resume_branch(task, active_frame_box):
                    continuations.append(
                        lambda task=task: resume_branch(task, active_frame_box)
                    )
            restored = fork_value(base)
            shadow.clear()
            shadow.update(restored[0])
            frames[:] = restored[1]
            active_frame_box[0] = frames[-1][1] if frames else {}

        def select_menu(menu_node):
            """Select one visible branch while retaining the current shadow state."""
            set_value = None
            set_expression = getattr(menu_node, "set", None)
            if set_expression:
                set_value = evaluate(set_expression)
                if not isinstance(set_value, (list, set)):
                    raise Stop("unsupported-menu-set")

            choices = []
            for item_index, item in enumerate(getattr(menu_node, "items", None) or []):
                if not item or len(item) < 3 or not item[2]:
                    continue
                condition = item[1]
                if condition not in (None, True):
                    if condition is False or not isinstance(condition, string_types) or not evaluate(condition):
                        continue
                raw_choice = item[0] or ""
                if set_value is not None and raw_choice in set_value:
                    continue
                choices.append({
                    "choice_index": item_index,
                    "choice": text_value(raw_choice),
                    "raw_choice": raw_choice,
                })

            if not choices:
                return getattr(menu_node, "next", None)
            selected_index = choice_selector([
                {"choice_index": item["choice_index"], "choice": item["choice"]}
                for item in choices
            ])
            selected = next(
                (item for item in choices if item["choice_index"] == selected_index),
                None,
            )
            if selected is None:
                raise Stop("invalid-choice-selection")
            if set_value is not None:
                if isinstance(set_value, list):
                    set_value.append(selected["raw_choice"])
                else:
                    set_value.add(selected["raw_choice"])
            debug["route_choices"].append({
                "menu_id": _translator_menu_id(menu_node),
                "choice_index": selected["choice_index"],
                "choice": selected["choice"],
            })
            return menu_node.items[selected["choice_index"]][2][0]

        try:
            if type(current).__name__ not in ("Say", "TranslateSay", "Menu"):
                raise Stop("unsupported-current-node:" + type(current).__name__)
            context = renpy.game.context()
            returns = list(getattr(context, "return_stack", []))
            dynamics = list(getattr(context, "dynamic_stack", []))
            # Dynamic dictionaries remain read-only until a return is reached.
            frames = [(site, dynamics[len(dynamics) - len(returns) + i] if len(dynamics) >= len(returns) else {})
                      for i, site in enumerate(returns)]
            active_frame = dict(frames[-1][1]) if frames else {}
            active_frame_box = [active_frame]
            if frames:
                frames[-1] = (frames[-1][0], active_frame)
            while node is not None and len(upcoming) < limit:
                tick()
                debug["visited_nodes"] += 1
                kind = type(node).__name__
                if kind in ("Translate", "TranslateSay") and getattr(node, "language", None) is None:
                    language = getattr(getattr(renpy.game, "preferences", None), "language", None)
                    if language is not None:
                        translated = node.lookup()
                        if translated is not None and translated is not node:
                            node = translated
                            continue
                if kind == "Menu":
                    if choice_selector is not None:
                        node = select_menu(node)
                        continue
                    collect_menu(node, active_frame_box)
                    raise Stop("menu-boundary")
                if kind in ("Say", "TranslateSay"):
                    say_item = resolve_say(node)
                    if say_item["what"]:
                        upcoming.append(say_item)
                node = advance(node, active_frame_box)
            debug["stop_reason"] = "prefetch-limit" if node is not None else "next-none"
        except Stop as error:
            debug["stop_reason"] = str(error)
        except Exception as error:
            debug["stop_reason"] = "prediction-error"
            debug["error"] = type(error).__name__ + ": " + str(error)[:160]
        debug["stop_node"] = _translator_node_debug(node)
        debug["prefetch_items"] = len(upcoming)
        debug["steps"] = steps[0]
        debug["elapsed_ms"] = round((clock() - started) * 1000, 3)
        debug["branch_count"] = len(branches)
        return upcoming, branches, debug, continuations

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
                _translator_prefetch_count = max(1, min(20, int(message.get("count", 5))))
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
                        "who": "",
                        "what": caption,
                        "italic": False,
                        "choices": choices,
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
            _translator_send_type(
                "menu_selected",
                session_id=_translator_session_id,
                state_version=_translator_menu_versions.get(menu_id, _translator_state_version),
                menu_id=menu_id,
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
                "session_id": _translator_session_id,
                "state_version": _translator_next_state_version(),
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
            upcoming, branches, prediction_debug, continuations = _translator_predict(renpy, cur)
            prefetch_debug.update(prediction_debug)
            if upcoming:
                msg["prefetch"] = upcoming
            msg["prefetch_debug"] = prefetch_debug

            branch_message = None
            if branches:
                menu_id = branches[0].get("menu_id", "")
                _translator_menu_versions[menu_id] = msg["state_version"]
                branch_message = {
                    "type": "branch_prefetch",
                    "session_id": _translator_session_id,
                    "state_version": msg["state_version"],
                    "menu_id": menu_id,
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
