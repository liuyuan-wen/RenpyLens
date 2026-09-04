/*:
 * @plugindesc RenpyLens runtime text bridge for RPG Maker MV/MZ.
 * @author RenpyLens
 * @param socketPort
 * @type number
 * @default 19876
 * @param sessionId
 * @type string
 * @default
 * @param qolEnabled
 * @type boolean
 * @default false
 * @param qolLocale
 * @type string
 * @default en_US
 * @param qolFeatures
 * @type string
 * @default {}
 * @help This file is installed and removed automatically by RenpyLens.
 */

(function() {
    "use strict";

    var PLUGIN_NAME = "RenpyLensBridge";
    var parameters = PluginManager.parameters(PLUGIN_NAME) || {};
    var socketPort = Number(parameters.socketPort || 19876);
    var sessionId = String(parameters.sessionId || "");
    var qolEnabled = String(parameters.qolEnabled || "false") === "true";
    var qolLocale = String(parameters.qolLocale || "en_US");
    var qolFeatures = {
        textSpeed: true,
        moveSpeed: true,
        through: true,
        encounters: true,
        battleVictory: true,
        messageOpacity: true,
        autoAdvance: true
    };
    try {
        if (parameters.qolFeatures) {
            var configuredFeatures = JSON.parse(String(parameters.qolFeatures));
            if (configuredFeatures && typeof configuredFeatures === "object") {
                Object.keys(qolFeatures).forEach(function(key) {
                    qolFeatures[key] = configuredFeatures[key] === true;
                });
            }
        }
    } catch (error) {}
    var engine = typeof Utils !== "undefined" && Utils.RPGMAKER_NAME === "MZ"
        ? "rpgmaker_mz" : "rpgmaker_mv";
    var lastMessageSegment = null;
    var lastInterpreter = null;
    var lastSignature = "";

    function send(payload) {
        try {
            var net = require("net");
            payload.protocol_version = 2;
            payload.engine = engine;
            payload.engine_version = String(Utils.RPGMAKER_VERSION || "");
            payload.session_id = sessionId;
            var socket = net.createConnection({ host: "127.0.0.1", port: socketPort }, function() {
                socket.end(JSON.stringify(payload));
            });
            socket.setTimeout(1000, function() { socket.destroy(); });
            socket.on("error", function() {});
        } catch (error) {
            // The game must keep running when RenpyLens is not listening.
        }
    }

    function actorName(id) {
        try {
            var actor = $gameActors.actor(Number(id));
            return actor ? String(actor.name() || "") : "";
        } catch (error) {
            return "";
        }
    }

    function partyName(index) {
        try {
            var member = $gameParty.members()[Number(index) - 1];
            return member ? String(member.name() || "") : "";
        } catch (error) {
            return "";
        }
    }

    function tokenData(raw) {
        var values = {};
        var source = String(raw || "").replace(/\r\n?/g, "\n");
        source = source.replace(/\\(V|N|P)\[(\d+)\]/gi, function(match, kind, id) {
            kind = String(kind).toUpperCase();
            id = String(Number(id));
            var token = "⟦RL_" + kind + "_" + id + "⟧";
            try {
                if (kind === "V") values[token] = String($gameVariables.value(Number(id)));
                if (kind === "N") values[token] = actorName(id);
                if (kind === "P") values[token] = partyName(id);
            } catch (error) {
                values[token] = "";
            }
            return token;
        });
        source = source.replace(/\\G\b/gi, function() {
            var token = "⟦RL_G⟧";
            values[token] = typeof TextManager !== "undefined" ? String(TextManager.currencyUnit || "") : "";
            return token;
        });
        // Variable/name/currency escapes above carry translatable values. Any
        // remaining letter command is presentation or flow control (colour,
        // icon, wait, word wrap, name-box layout, plugin-specific effects...).
        // Strip it so it cannot make a variable-backed message look like it
        // contains literal text. Match command structure, not a game-specific
        // list such as only `\\w[5]`.
        source = source.replace(/\\[A-Z]+(?:\[[^\]]*\]|<[^>]*>)?/gi, "");
        source = source.replace(/\\[.\\|!><^{}]/g, "");
        source = source.replace(/<\/?(?:WordWrap|CENTER|LEFT|RIGHT|TOP|MIDDLE|BOTTOM)>/gi, "");
        source = source.replace(/[ \t]+\n/g, "\n").trim();
        return { source: source, token_values: values };
    }

    function stripRendered(text) {
        var value = String(text || "").replace(/\x1b[A-Z]+(?:\[[^\]]*\]|<[^>]*>)?/gi, "");
        value = value.replace(/\x1b[.\\|!><^{}]/g, "");
        value = value.replace(/<\/?(?:WordWrap|CENTER|LEFT|RIGHT|TOP|MIDDLE|BOTTOM)>/gi, "");
        return value.replace(/[ \t]+\n/g, "\n").trim();
    }

    function resolveRaw(windowObject, raw) {
        try {
            return stripRendered(windowObject.convertEscapeCharacters(String(raw || "")));
        } catch (error) {
            return stripRendered(raw);
        }
    }

    function isVariableOnlySource(source) {
        var value = String(source || "");
        if (!/⟦RL_V_\d+⟧/.test(value)) return false;
        value = value.replace(/⟦RL_V_\d+⟧/g, "");
        // Be defensive if this helper receives text which has not gone
        // through tokenData yet.
        value = value.replace(/\\[A-Z]+(?:\[[^\]]*\]|<[^>]*>)?/gi, "");
        value = value.replace(/\\[.\\|!><^{}]/g, "");
        return value.trim() === "";
    }

    function extractSpeaker(raw, windowObject) {
        var text = String(raw || "");
        var speaker = "";
        try {
            if ($gameMessage.speakerName) speaker = String($gameMessage.speakerName() || "");
        } catch (error) {}

        if (!speaker && windowObject && windowObject._nameWindow) {
            var nameWindow = windowObject._nameWindow;
            speaker = String(nameWindow._lastNameText || nameWindow._text || "");
            speaker = resolveRaw(windowObject, speaker);
        }

        var nameBox = text.match(/^\s*\\(?:N|N[1-5]|NC|NR)<([^>]+)>\s*/i);
        if (!speaker && nameBox) speaker = resolveRaw(windowObject, nameBox[1]);
        if (nameBox) text = text.slice(nameBox[0].length);

        var actorPrefix = text.match(/^\s*\\N\[(\d+)\]\s*[:：]\s*/i);
        if (!speaker && actorPrefix) speaker = actorName(actorPrefix[1]);
        if (actorPrefix) text = text.slice(actorPrefix[0].length);

        var bracket = text.match(/^\s*【([^】\r\n]{1,80})】\s*/);
        if (!speaker && bracket) speaker = bracket[1].trim();
        if (bracket) text = text.slice(bracket[0].length);

        return { speaker: stripRendered(speaker), text: text };
    }

    function makeSegment(raw, windowObject, speakerHint, italic, resolveVariableOnly) {
        var extracted = extractSpeaker(raw, windowObject);
        if (speakerHint) extracted.speaker = String(speakerHint);
        var tokens = tokenData(extracted.text);
        var displayText = resolveRaw(windowObject, extracted.text);
        var variableOnly = isVariableOnlySource(tokens.source);

        // Some RPG Maker games (including NLT titles) put the complete current
        // line in a game variable and make the message command just `\V[n]`.
        // Sending only our protected token asks the model to preserve it, so
        // the untranslated variable value is restored afterwards. Translate
        // the resolved visible line instead. Future values cannot be known
        // during interpreter prefetch, so callers can disable this expansion.
        if (variableOnly && resolveVariableOnly !== false && displayText) {
            tokens.source = displayText;
            tokens.token_values = {};
        }
        return {
            source: tokens.source,
            display_text: displayText,
            token_values: tokens.token_values,
            who: stripRendered(extracted.speaker),
            italic: !!italic
        };
    }

    function commandScript(interpreter) {
        if (!interpreter || !interpreter._list) return "";
        var index = Number(interpreter._index || 0);
        var command = interpreter._list[index] || {};
        if (Number(command.code || 0) !== 355) return "";
        var params = command.parameters || [];
        var parts = [String(params[0] || "")];
        var cursor = index + 1;
        while (cursor < interpreter._list.length &&
                Number((interpreter._list[cursor] || {}).code) === 655) {
            var continuation = (interpreter._list[cursor] || {}).parameters || [];
            parts.push(String(continuation[0] || ""));
            cursor += 1;
        }
        return parts.join("\n");
    }

    function textReplacementRules(script) {
        var rules = [];
        var pattern = /\.replace\(\s*("(?:\\.|[^"\\])*")\s*,\s*("(?:\\.|[^"\\])*")\s*\)/g;
        var match;
        while ((match = pattern.exec(String(script || ""))) !== null) {
            try {
                rules.push([JSON.parse(match[1]), JSON.parse(match[2]), false]);
            } catch (error) {}
        }

        // A common localization pipeline also expands symbolic names with an
        // actor name or a game variable. Read only simple, unconditional,
        // literal-token regex replacements from scripts which the interpreter
        // actually executed; never eval a future event script.
        var code = String(script || "");
        if (!/\bif\s*\(/.test(code)) {
            var dynamic = /\.replace\(\s*\/([A-Za-z0-9_']+)\/g\s*,\s*(\$gameActors\.actor\(\s*(\d+)\s*\)\.name\(\)|\$gameVariables\.value\(\s*(\d+)\s*\))\s*\)/g;
            while ((match = dynamic.exec(code)) !== null) {
                try {
                    var replacement = match[3] !== undefined
                        ? actorName(match[3])
                        : String($gameVariables.value(Number(match[4])) || "");
                    if (replacement) rules.push([match[1], replacement, true]);
                } catch (error) {}
            }
        }
        return rules;
    }

    function applyLiteralReplacements(value, rules) {
        var result = String(value || "");
        (rules || []).forEach(function(rule) {
            var source = String(rule[0] || "");
            if (!source) return;
            var replacement = String(rule[1] || "");
            if (rule[2]) {
                result = result.split(source).join(replacement);
            } else {
                var index = result.indexOf(source);
                if (index >= 0) {
                    result = result.slice(0, index) + replacement +
                        result.slice(index + source.length);
                }
            }
        });
        return result;
    }

    function scriptedDialogue(command, replacementRules) {
        if (!command || Number(command.code || 0) !== 355) return "";
        var params = command.parameters || [];
        var script = String(params[0] || "");
        var assignment = script.match(
            /\$gameVariables\.setValue\(\s*\d+\s*,\s*("(?:\\.|[^"\\])*")\s*\)/
        );
        if (!assignment) return "";
        try {
            var value = applyLiteralReplacements(
                JSON.parse(assignment[1]), replacementRules
            );
            // Some event systems encode compact speaker/portrait/expression
            // metadata before the first dot. Its alphabet and separators vary
            // by game (for example `Br,fr,op.` or `HeSaOp#>.`), so recognize
            // the structural envelope instead of a particular game's codes.
            var encoded = value.match(
                /^[^\s.]{2,32}\.(.+)$/
            );
            return encoded ? encoded[1].trim() : "";
        } catch (error) {
            return "";
        }
    }

    function interpreterPrefetch(windowObject) {
        var items = [];
        var seen = {};
        var interpreter = lastInterpreter;
        if (!interpreter || !interpreter._list) return items;
        var replacementRules = interpreter._renpyLensTextReplacements || [];
        var boundaries = { 102:1, 111:1, 112:1, 113:1, 115:1, 119:1, 201:1, 301:1 };
        while (interpreter && interpreter._list && items.length < 60) {
            var list = interpreter._list;
            // The active interpreter is still on command101 while its
            // startMessage runs. A parent paused for its child has already
            // advanced to the next command, so do not skip that command.
            var index = Number(interpreter._index || 0) + (interpreter === lastInterpreter ? 1 : 0);
            while (index < list.length && items.length < 60) {
                var command = list[index] || {};
                var code = Number(command.code || 0);
                if (boundaries[code]) break;
                if (code === 101) {
                    var params = command.parameters || [];
                    var lines = [];
                    var cursor = index + 1;
                    while (cursor < list.length && Number((list[cursor] || {}).code) === 401) {
                        var lineParams = (list[cursor] || {}).parameters || [];
                        lines.push(String(lineParams[0] || ""));
                        cursor += 1;
                    }
                    var nativeSpeaker = params.length >= 5 ? String(params[4] || "") : "";
                    var segment = makeSegment(lines.join("\n"), windowObject, nativeSpeaker, false, false);
                    // A future `\V[n]` line would resolve to the variable's current
                    // value, not the value it will have when that event executes.
                    if (!isVariableOnlySource(segment.source) && segment.source && !seen[segment.source]) {
                        seen[segment.source] = true;
                        items.push(segment);
                    }
                    index = cursor;
                    continue;
                }

                // NLT games keep following lines as literal assignments in
                // the parent map interpreter and render them through a child
                // common event. Never execute future scripts to discover text.
                var futureDialogue = scriptedDialogue(command, replacementRules);
                if (futureDialogue && !seen[futureDialogue]) {
                    seen[futureDialogue] = true;
                    items.push(makeSegment(futureDialogue, windowObject, "", false));
                }
                index += 1;
            }
            interpreter = interpreter._renpyLensParentInterpreter || null;
        }
        return items;
    }

    function emitCurrent(segment, choices, menuActive, prefetch) {
        choices = choices || [];
        var payload = {
            type: "current",
            who: segment ? segment.who : "",
            what: segment ? segment.display_text : "",
            italic: segment ? segment.italic : false,
            choices: choices.map(function(item) { return item.display_text; }),
            menu_active: !!menuActive,
            current_segment: segment,
            choice_segments: choices,
            prefetch: prefetch || []
        };
        var signature = JSON.stringify([payload.who, payload.what, payload.choices, payload.menu_active]);
        if (signature === lastSignature) return;
        lastSignature = signature;
        send(payload);
    }

    function setupQoL() {
        var dictionaries = {
            zh_CN: {
                textSpeed: "文字速度", moveSpeed: "高速", normal: "1×", fast: "2×", instant: "即时",
                opacity: "对话框", auto: "自动",
                speed: "高速", through: "穿墙", encounters: "随机遇敌", victory: "战胜", defeat: "战败",
                on: "开", off: "关", noBattle: "当前不在战斗中",
                winning: "敌人已全部击倒，正在结算胜利", losing: "队伍已被击倒，正在结算战败"
            },
            zh_TW: {
                textSpeed: "文字速度", moveSpeed: "高速移動", normal: "1×", fast: "2×", instant: "即時",
                opacity: "對話框", auto: "自動",
                speed: "高速", through: "穿牆", encounters: "隨機遇敵", victory: "戰勝", defeat: "戰敗",
                on: "開", off: "關", noBattle: "目前不在戰鬥中",
                winning: "敵人已全部擊倒，正在結算勝利", losing: "隊伍已被擊倒，正在結算戰敗"
            },
            en_US: {
                textSpeed: "Text speed", moveSpeed: "High speed", normal: "1×", fast: "2×", instant: "Instant",
                opacity: "Dialogue", auto: "Auto",
                speed: "Speed", through: "No clip", encounters: "Encounters", victory: "Win", defeat: "Lose",
                on: "On", off: "Off", noBattle: "Not currently in battle",
                winning: "Enemies defeated; resolving victory", losing: "Party defeated; resolving defeat"
            },
            ja_JP: {
                textSpeed: "文字速度", moveSpeed: "高速移動", normal: "1×", fast: "2×", instant: "即時",
                opacity: "会話枠", auto: "自動",
                speed: "高速", through: "壁抜け", encounters: "ランダム遭遇", victory: "勝利", defeat: "敗北",
                on: "オン", off: "オフ", noBattle: "戦闘中ではありません",
                winning: "敵を倒しました。勝利処理中です", losing: "味方が倒れました。敗北処理中です"
            },
            ko_KR: {
                textSpeed: "텍스트 속도", moveSpeed: "고속 이동", normal: "1×", fast: "2×", instant: "즉시",
                opacity: "대화창", auto: "자동",
                speed: "고속", through: "벽 통과", encounters: "랜덤 전투", victory: "승리", defeat: "패배",
                on: "켜짐", off: "꺼짐", noBattle: "현재 전투 중이 아닙니다",
                winning: "적을 모두 쓰러뜨렸습니다. 승리를 처리합니다", losing: "파티가 쓰러졌습니다. 패배를 처리합니다"
            },
            ru_RU: {
                textSpeed: "Скорость текста", moveSpeed: "Быстрое движение", normal: "1×", fast: "2×", instant: "Мгновенно",
                opacity: "Диалог", auto: "Авто",
                speed: "Скорость", through: "Сквозь стены", encounters: "Случайные бои", victory: "Победа", defeat: "Поражение",
                on: "Вкл", off: "Выкл", noBattle: "Сейчас нет боя",
                winning: "Враги побеждены; завершается бой", losing: "Отряд побеждён; завершается поражение"
            }
        };
        var text = dictionaries[qolLocale] || dictionaries.en_US;
        var controls = null;
        var buttons = {};
        var toastTimer = 0;
        var autoPanelOpen = false;

        function featureEnabled(key) {
            return qolFeatures[key] === true;
        }

        function state() {
            if (typeof $gameSystem === "undefined" || !$gameSystem) return null;
            if (!$gameSystem._rpgMakerQoL) {
                $gameSystem._rpgMakerQoL = {
                    textSpeed: "normal", speed: false, through: false, noEncounters: false,
                    messageOpacity: 100, autoAdvance: false, autoAdvanceSpeed: 50
                };
            }
            var data = $gameSystem._rpgMakerQoL;
            if (typeof data.speed !== "boolean") data.speed = !!data.exploration;
            if (typeof data.through !== "boolean") data.through = !!data.exploration;
            if (typeof data.noEncounters !== "boolean") data.noEncounters = false;
            if (["normal", "fast", "instant"].indexOf(data.textSpeed) < 0) {
                data.textSpeed = "normal";
            }
            if ([100, 70, 40, 0].indexOf(Number(data.messageOpacity)) < 0) {
                data.messageOpacity = 100;
            }
            if (typeof data.autoAdvance !== "boolean") data.autoAdvance = false;
            var autoSpeed = Number(data.autoAdvanceSpeed);
            if (!isFinite(autoSpeed)) autoSpeed = 50;
            data.autoAdvanceSpeed = Math.max(0, Math.min(100, Math.round(autoSpeed)));
            if (Object.prototype.hasOwnProperty.call(data, "exploration")) delete data.exploration;
            return data;
        }

        function ensureToast() {
            if (typeof document === "undefined" || !document.body) return null;
            var element = document.getElementById("renpylens-qol-toast");
            if (!element) {
                element = document.createElement("div");
                element.id = "renpylens-qol-toast";
                element.style.cssText = [
                    "position:fixed", "top:56px", "right:10px", "z-index:2147483647",
                    "padding:8px 12px", "border-radius:5px", "background:rgba(0,0,0,.8)",
                    "color:#fff", "font:16px sans-serif", "pointer-events:none", "opacity:0",
                    "transition:opacity .15s ease"
                ].join(";");
                document.body.appendChild(element);
            }
            return element;
        }

        function toast(message) {
            var element = ensureToast();
            if (!element) return;
            element.textContent = message;
            element.style.opacity = "1";
            window.clearTimeout(toastTimer);
            toastTimer = window.setTimeout(function() { element.style.opacity = "0"; }, 1600);
        }

        function refreshControls() {
            ensureControls();
            if (!controls) return;
            var data = state();
            var scene = typeof SceneManager !== "undefined" ? SceneManager._scene : null;
            var title = typeof Scene_Title !== "undefined" && scene instanceof Scene_Title;
            var boot = typeof Scene_Boot !== "undefined" && scene instanceof Scene_Boot;
            var visible = !!data && !!scene && !title && !boot;
            controls.style.display = visible ? "flex" : "none";
            if (!visible) {
                autoPanelOpen = false;
                return;
            }
            if (buttons.textSpeed) {
                buttons.textSpeed.textContent = text.textSpeed + ": " + text[data.textSpeed];
                buttons.textSpeed.style.background = data.textSpeed === "normal" ?
                    "rgba(55,55,60,.86)" : "rgba(34,139,86,.92)";
            }
            if (buttons.opacity) {
                buttons.opacity.textContent = text.opacity + ": " + data.messageOpacity + "%";
                buttons.opacity.style.background = data.messageOpacity === 100 ?
                    "rgba(55,55,60,.86)" : "rgba(34,139,86,.92)";
            }
            if (buttons.autoAdvance) {
                buttons.autoAdvance.button.style.background = data.autoAdvance ?
                    "rgba(34,139,86,.92)" : "rgba(55,55,60,.86)";
                buttons.autoAdvance.panel.style.display = autoPanelOpen ? "block" : "none";
                buttons.autoAdvance.range.value = String(data.autoAdvanceSpeed);
                buttons.autoAdvance.button.setAttribute(
                    "aria-pressed", data.autoAdvance ? "true" : "false"
                );
                buttons.autoAdvance.range.setAttribute(
                    "aria-valuenow", String(data.autoAdvanceSpeed)
                );
            }
            paintButton(buttons.speed, text.moveSpeed || text.speed, data.speed);
            paintButton(buttons.through, text.through, data.through);
            paintButton(buttons.encounters, text.encounters, !data.noEncounters);
            var inBattle = typeof $gameParty !== "undefined" && $gameParty && $gameParty.inBattle();
            if (buttons.battleOutcome) {
                buttons.battleOutcome.container.style.display = inBattle ? "flex" : "none";
                buttons.battleOutcome.victory.textContent = text.victory;
                buttons.battleOutcome.defeat.textContent = text.defeat;
            }
        }

        function cycleTextSpeed() {
            var data = state();
            if (!data || !featureEnabled("textSpeed")) return;
            data.textSpeed = data.textSpeed === "normal" ? "fast" :
                (data.textSpeed === "fast" ? "instant" : "normal");
            refreshControls();
            toast(text.textSpeed + ": " + text[data.textSpeed]);
        }

        function currentMessageWindow() {
            var scene = typeof SceneManager !== "undefined" ? SceneManager._scene : null;
            if (!scene) return null;
            if (scene._messageWindow) return scene._messageWindow;
            var children = scene._windowLayer && scene._windowLayer.children || [];
            for (var index = 0; index < children.length; index += 1) {
                if (children[index] instanceof Window_Message) return children[index];
            }
            return null;
        }

        function applyMessageOpacity(windowObject) {
            if (!windowObject || !featureEnabled("messageOpacity")) return;
            var data = state();
            if (!data || data.messageOpacity === 100) return;
            var alpha = Math.round(255 * data.messageOpacity / 100);
            if (windowObject._background === 1 && windowObject._dimmerSprite) {
                windowObject._dimmerSprite.opacity = Math.round(
                    Number(windowObject._dimmerSprite.opacity || 255) * data.messageOpacity / 100
                );
            } else if (windowObject._background !== 2) {
                windowObject.opacity = alpha;
            }
        }

        function cycleMessageOpacity() {
            var data = state();
            if (!data || !featureEnabled("messageOpacity")) return;
            var levels = [100, 70, 40, 0];
            var index = levels.indexOf(data.messageOpacity);
            data.messageOpacity = levels[(index + 1) % levels.length];
            var messageWindow = currentMessageWindow();
            if (messageWindow && typeof messageWindow.updateBackground === "function") {
                messageWindow.updateBackground();
            }
            refreshControls();
            toast(text.opacity + ": " + data.messageOpacity + "%");
        }

        function toggleAutoAdvance() {
            var data = state();
            if (!data || !featureEnabled("autoAdvance")) return;
            if (data.autoAdvance && !autoPanelOpen) {
                autoPanelOpen = true;
                refreshControls();
                return;
            }
            data.autoAdvance = !data.autoAdvance;
            autoPanelOpen = data.autoAdvance;
            refreshControls();
            toast(text.auto + ": " + text[data.autoAdvance ? "on" : "off"]);
        }

        function closeAutoPanel() {
            if (!autoPanelOpen) return;
            autoPanelOpen = false;
            refreshControls();
        }

        function toggleSpeed() {
            var data = state();
            if (!data || !featureEnabled("moveSpeed")) return;
            data.speed = !data.speed;
            refreshControls();
            toast((text.moveSpeed || text.speed) + ": " + text[data.speed ? "on" : "off"]);
        }

        function toggleThrough() {
            var data = state();
            if (!data || !featureEnabled("through")) return;
            data.through = !data.through;
            refreshControls();
            toast(text.through + ": " + text[data.through ? "on" : "off"]);
        }

        function toggleEncounters() {
            var data = state();
            if (!data || !featureEnabled("encounters")) return;
            data.noEncounters = !data.noEncounters;
            refreshControls();
            toast(text.encounters + ": " + text[data.noEncounters ? "off" : "on"]);
        }

        function winBattle() {
            if (!featureEnabled("battleVictory")) return;
            if (typeof $gameParty === "undefined" || !$gameParty || !$gameParty.inBattle() ||
                    typeof $gameTroop === "undefined" || !$gameTroop) {
                toast(text.noBattle);
                return;
            }
            $gameTroop.members().forEach(function(enemy) {
                if (!enemy || !enemy.isAlive()) return;
                var deathStateId = enemy.deathStateId();
                enemy.setHp(0);
                enemy.addState(deathStateId);
                if (!enemy.isStateAffected(deathStateId)) {
                    enemy._states.push(deathStateId);
                    enemy.refresh();
                }
            });
            // Merely changing battler HP is not enough for several battle
            // systems (notably YEP's action-sequence and CTB/ATB plugins):
            // their normal end check may be suspended until the current
            // sequence finishes. Enter the engine's victory flow explicitly
            // so rewards, callbacks and victory-after-battle plugins still run.
            if (typeof BattleManager !== "undefined" && BattleManager &&
                    typeof BattleManager.processVictory === "function") {
                BattleManager.processVictory();
            }
            toast(text.winning);
        }

        function loseBattle() {
            if (!featureEnabled("battleVictory")) return;
            if (typeof $gameParty === "undefined" || !$gameParty || !$gameParty.inBattle()) {
                toast(text.noBattle);
                return;
            }
            var members = typeof $gameParty.battleMembers === "function" ?
                $gameParty.battleMembers() : $gameParty.members();
            (members || []).forEach(function(actor) {
                if (!actor || (typeof actor.isAlive === "function" && !actor.isAlive())) return;
                var deathStateId = typeof actor.deathStateId === "function" ? actor.deathStateId() : 1;
                if (typeof actor.setHp === "function") actor.setHp(0);
                if (typeof actor.addState === "function") actor.addState(deathStateId);
                if (typeof actor.isStateAffected === "function" &&
                        !actor.isStateAffected(deathStateId) && actor._states) {
                    actor._states.push(deathStateId);
                    if (typeof actor.refresh === "function") actor.refresh();
                }
            });
            if (typeof BattleManager !== "undefined" && BattleManager &&
                    typeof BattleManager.processDefeat === "function") {
                BattleManager.processDefeat();
            }
            toast(text.losing);
        }

        function makeButton(key, handler) {
            var button = document.createElement("button");
            button.type = "button";
            button.style.cssText = [
                "min-width:0", "width:auto", "height:32px", "padding:0 9px",
                "border:1px solid rgba(255,255,255,.4)", "border-radius:5px",
                "color:#fff", "font:14px sans-serif", "font-weight:bold",
                "text-shadow:0 1px 1px #000", "cursor:pointer",
                "box-shadow:0 1px 4px rgba(0,0,0,.4)", "white-space:nowrap",
                "box-sizing:border-box", "flex:0 0 auto"
            ].join(";");
            button.addEventListener("click", function(event) {
                event.preventDefault();
                event.stopPropagation();
                handler();
            });
            buttons[key] = button;
            controls.appendChild(button);
        }

        function makeAutoControl() {
            var container = document.createElement("div");
            container.style.cssText = [
                "position:relative", "height:32px", "display:block", "overflow:visible"
            ].join(";");

            var button = document.createElement("button");
            button.type = "button";
            button.textContent = text.auto;
            button.style.cssText = [
                "min-width:0", "width:auto", "height:32px", "padding:0 9px", "margin:0",
                "border:1px solid rgba(255,255,255,.4)", "border-radius:5px",
                "background:rgba(55,55,60,.86)", "appearance:none", "-webkit-appearance:none",
                "color:#fff", "font:14px sans-serif", "font-weight:bold",
                "text-shadow:0 1px 1px #000", "cursor:pointer",
                "box-shadow:0 1px 4px rgba(0,0,0,.4)", "box-sizing:border-box",
                "white-space:nowrap", "flex:0 0 auto"
            ].join(";");
            button.addEventListener("click", function(event) {
                event.preventDefault();
                event.stopPropagation();
                toggleAutoAdvance();
            });

            var panel = document.createElement("div");
            panel.style.cssText = [
                "position:absolute", "display:none", "top:38px", "left:50%",
                "transform:translateX(-50%)", "width:166px", "height:34px",
                "padding:6px 9px", "border:1px solid rgba(255,255,255,.35)",
                "border-radius:5px", "background:rgba(38,38,44,.96)",
                "box-shadow:0 2px 7px rgba(0,0,0,.45)", "box-sizing:border-box",
                "z-index:2147483647"
            ].join(";");
            var range = document.createElement("input");
            range.type = "range";
            range.min = "0";
            range.max = "100";
            range.step = "5";
            range.value = "50";
            range.setAttribute("aria-label", text.auto);
            range.style.cssText = [
                "display:block", "width:146px", "height:20px", "margin:0",
                "padding:0", "cursor:pointer", "accent-color:#2aad70"
            ].join(";");
            range.addEventListener("input", function(event) {
                var data = state();
                if (!data) return;
                var value = Number(event && event.target ? event.target.value : range.value);
                data.autoAdvanceSpeed = Math.max(0, Math.min(100, Math.round(value)));
                range.setAttribute("aria-valuenow", String(data.autoAdvanceSpeed));
            });
            range.addEventListener("blur", closeAutoPanel);
            panel.appendChild(range);
            container.appendChild(button);
            container.appendChild(panel);
            buttons.autoAdvance = {
                container: container, button: button, panel: panel, range: range
            };
            controls.appendChild(container);
        }

        function makeExplorationControl() {
            var container = document.createElement("div");
            container.style.cssText = [
                "height:32px", "display:flex", "align-items:center",
                "border:1px solid rgba(255,255,255,.4)", "border-radius:5px",
                "background:rgba(55,55,60,.86)", "overflow:hidden",
                "box-shadow:none", "box-sizing:border-box"
            ].join(";");

            function makeSegment(key, handler) {
                var button = document.createElement("button");
                button.type = "button";
                button.style.cssText = [
                    "min-width:0", "width:auto", "height:100%", "padding:0 9px", "margin:0",
                    "border:0", "border-radius:0", "background:transparent",
                    "appearance:none", "-webkit-appearance:none", "box-shadow:none",
                    "outline:none", "box-sizing:border-box", "display:block",
                    "white-space:nowrap", "flex:0 0 auto",
                    "color:#fff", "font:14px sans-serif", "font-weight:bold",
                    "text-shadow:0 1px 1px #000", "cursor:pointer"
                ].join(";");
                button.addEventListener("click", function(event) {
                    event.preventDefault();
                    event.stopPropagation();
                    handler();
                });
                buttons[key] = button;
                container.appendChild(button);
            }

            if (featureEnabled("moveSpeed")) makeSegment("speed", toggleSpeed);
            if (featureEnabled("moveSpeed") && featureEnabled("through")) {
                var separator = document.createElement("span");
                separator.style.cssText = [
                    "display:block", "width:1px", "height:20px", "flex:0 0 1px",
                    "background:rgba(255,255,255,.28)", "pointer-events:none"
                ].join(";");
                container.appendChild(separator);
            }
            if (featureEnabled("through")) makeSegment("through", toggleThrough);
            controls.appendChild(container);
        }

        function makeBattleOutcomeControl() {
            var container = document.createElement("div");
            container.style.cssText = [
                "height:32px", "display:none", "align-items:center",
                "border:1px solid rgba(255,255,255,.4)", "border-radius:5px",
                "background:rgba(55,55,60,.9)", "overflow:hidden",
                "box-shadow:none", "box-sizing:border-box"
            ].join(";");

            function makeSegment(label, handler, hoverColor) {
                var button = document.createElement("button");
                button.type = "button";
                button.textContent = label;
                button.style.cssText = [
                    "min-width:0", "width:auto", "height:100%", "padding:0 10px", "margin:0",
                    "border:0", "border-radius:0", "background:transparent",
                    "appearance:none", "-webkit-appearance:none", "box-shadow:none",
                    "outline:none", "box-sizing:border-box", "display:block",
                    "white-space:nowrap", "flex:0 0 auto",
                    "color:#fff", "font:14px sans-serif", "font-weight:bold",
                    "text-shadow:0 1px 1px #000", "cursor:pointer"
                ].join(";");
                button.addEventListener("mouseenter", function() {
                    button.style.background = hoverColor;
                });
                button.addEventListener("mouseleave", function() {
                    button.style.background = "transparent";
                });
                button.addEventListener("click", function(event) {
                    event.preventDefault();
                    event.stopPropagation();
                    handler();
                });
                return button;
            }

            var victory = makeSegment(text.victory, winBattle, "rgba(34,139,86,.92)");
            var separator = document.createElement("span");
            separator.style.cssText = [
                "display:block", "width:1px", "height:20px", "flex:0 0 1px",
                "background:rgba(255,255,255,.28)", "pointer-events:none"
            ].join(";");
            var defeat = makeSegment(text.defeat, loseBattle, "rgba(176,62,62,.94)");
            container.appendChild(victory);
            container.appendChild(separator);
            container.appendChild(defeat);
            buttons.battleOutcome = { container: container, victory: victory, defeat: defeat };
            controls.appendChild(container);
        }

        function eventBelongsToControls(event) {
            var target = event && event.target;
            while (target) {
                if (target === controls) return true;
                target = target.parentNode;
            }
            return false;
        }

        function blockControlPointerEvent(event) {
            var target = event && event.target;
            var isRange = target && String(target.type || "").toLowerCase() === "range";
            // A parent preventDefault() cancels the browser's native range
            // drag/click behavior. Let range inputs perform their default
            // action while still stopping the event before RPG Maker sees it.
            if (!isRange && event && typeof event.preventDefault === "function") {
                event.preventDefault();
            }
            if (event && typeof event.stopPropagation === "function") event.stopPropagation();
            if (event && typeof event.stopImmediatePropagation === "function") {
                event.stopImmediatePropagation();
            }
        }

        function guardRpgMakerTouchInput() {
            if (typeof TouchInput === "undefined" || !TouchInput || TouchInput._renpyLensGuarded) return;
            TouchInput._renpyLensGuarded = true;
            ["_onMouseDown", "_onMouseMove", "_onMouseUp", "_onWheel",
             "_onTouchStart", "_onTouchMove", "_onTouchEnd", "_onTouchCancel",
             "_onPointerDown", "_onPointerMove", "_onPointerUp"].forEach(function(name) {
                var original = TouchInput[name];
                if (typeof original !== "function") return;
                TouchInput[name] = function(event) {
                    if (eventBelongsToControls(event)) return;
                    return original.apply(this, arguments);
                };
            });
        }

        function ensureControls() {
            if (typeof document === "undefined" || !document.body) return;
            if (controls && controls.parentNode) return;
            controls = document.createElement("div");
            controls.id = "renpylens-qol-controls";
            controls.style.cssText = [
                "position:fixed", "top:10px", "right:10px", "z-index:2147483646",
                "display:none", "gap:6px", "align-items:center", "pointer-events:auto",
                "user-select:none", "touch-action:none"
            ].join(";");
            ["pointerdown", "pointermove", "pointerup", "mousedown", "mousemove",
             "mouseup", "touchstart", "touchmove", "touchend", "click",
             "dblclick", "contextmenu", "wheel"]
                .forEach(function(name) {
                    controls.addEventListener(name, blockControlPointerEvent, false);
                });
            document.body.appendChild(controls);
            guardRpgMakerTouchInput();
            if (document.addEventListener) {
                document.addEventListener("pointerdown", function(event) {
                    if (!autoPanelOpen || eventBelongsToControls(event)) return;
                    closeAutoPanel();
                }, true);
            }
            if (typeof window !== "undefined" && window.addEventListener) {
                window.addEventListener("blur", closeAutoPanel, false);
            }
            if (featureEnabled("textSpeed")) makeButton("textSpeed", cycleTextSpeed);
            if (featureEnabled("messageOpacity")) makeButton("opacity", cycleMessageOpacity);
            if (featureEnabled("autoAdvance")) makeAutoControl();
            if (featureEnabled("moveSpeed") || featureEnabled("through")) {
                makeExplorationControl();
            }
            if (featureEnabled("encounters")) makeButton("encounters", toggleEncounters);
            if (featureEnabled("battleVictory")) makeBattleOutcomeControl();
        }

        function paintButton(button, label, enabled) {
            if (!button) return;
            button.textContent = label + ": " + text[enabled ? "on" : "off"];
            button.style.background = enabled ? "rgba(34,139,86,.92)" : "rgba(55,55,60,.86)";
        }

        var originalSystemInitialize = Game_System.prototype.initialize;
        Game_System.prototype.initialize = function() {
            originalSystemInitialize.call(this);
            this._rpgMakerQoL = {
                textSpeed: "normal", speed: false, through: false, noEncounters: false,
                messageOpacity: 100, autoAdvance: false, autoAdvanceSpeed: 50
            };
        };

        var originalIsThrough = Game_Player.prototype.isThrough;
        Game_Player.prototype.isThrough = function() {
            var data = state();
            return !!(featureEnabled("through") && data && data.through) ||
                originalIsThrough.call(this);
        };

        var originalRealMoveSpeed = Game_Player.prototype.realMoveSpeed;
        Game_Player.prototype.realMoveSpeed = function() {
            var speed = originalRealMoveSpeed.call(this);
            var data = state();
            return featureEnabled("moveSpeed") && data && data.speed ? Math.max(speed, 6) : speed;
        };

        var originalExecuteEncounter = Game_Player.prototype.executeEncounter;
        Game_Player.prototype.executeEncounter = function() {
            var data = state();
            if (featureEnabled("encounters") && data && data.noEncounters) {
                this.makeEncounterCount();
                return false;
            }
            return originalExecuteEncounter.call(this);
        };

        // Guard the earlier eligibility check as well as executeEncounter.
        // Some encounter plugins consult canEncounter directly and bypass the
        // stock executeEncounter implementation.
        var originalCanEncounter = Game_Player.prototype.canEncounter;
        if (typeof originalCanEncounter === "function") {
            Game_Player.prototype.canEncounter = function() {
                var data = state();
                if (featureEnabled("encounters") && data && data.noEncounters) {
                    return false;
                }
                return originalCanEncounter.call(this);
            };
        }

        function messageIsBusy() {
            try {
                return typeof $gameMessage !== "undefined" && $gameMessage &&
                    typeof $gameMessage.isBusy === "function" && $gameMessage.isBusy();
            } catch (error) {
                return false;
            }
        }

        function isImmediateTouchBattle(event) {
            if (!event || Number(event._trigger) !== 2) return false;
            var list = null;
            try {
                if (typeof event.list === "function") list = event.list();
                if (!list && typeof event.page === "function" && event.page()) {
                    list = event.page().list;
                }
            } catch (error) {
                return false;
            }
            if (!Array.isArray(list)) return false;
            for (var index = 0; index < list.length; index += 1) {
                var code = Number((list[index] || {}).code || 0);
                if (code === 0 || code === 108 || code === 408) continue;
                return code === 301;
            }
            return false;
        }

        // Parallel common events (for example a companion conversation) do
        // not make Game_Map.isEventRunning() true in RPG Maker MV. Without an
        // extra guard, a moving event can therefore start a battle while a
        // dialogue or choice window is active and leave $gameMessage corrupt
        // across the scene transition. Also treat touch events whose first
        // real command is Battle Processing as roaming map encounters. This
        // leaves explicit story battles and player-triggered battles intact.
        if (typeof Game_Event !== "undefined" && Game_Event &&
                typeof Game_Event.prototype.checkEventTriggerTouch === "function") {
            var originalCheckEventTriggerTouch = Game_Event.prototype.checkEventTriggerTouch;
            Game_Event.prototype.checkEventTriggerTouch = function() {
                if (messageIsBusy()) return;
                var data = state();
                if (featureEnabled("encounters") && data && data.noEncounters &&
                        isImmediateTouchBattle(this)) {
                    return;
                }
                return originalCheckEventTriggerTouch.apply(this, arguments);
            };
        }

        var originalSceneUpdate = Scene_Base.prototype.update;
        Scene_Base.prototype.update = function() {
            originalSceneUpdate.call(this);
            refreshControls();
        };

        document.addEventListener("keydown", function(event) {
            if (event.repeat || event.ctrlKey || event.altKey || event.metaKey) return;
            if (event.keyCode === 71 && featureEnabled("moveSpeed")) toggleSpeed();
            else if (event.keyCode === 72 && featureEnabled("through")) toggleThrough();
            else if (event.keyCode === 78 && featureEnabled("encounters")) toggleEncounters();
            else if (event.keyCode === 75 && featureEnabled("battleVictory")) winBattle();
            else return;
            event.preventDefault();
        }, true);

        var originalUpdateMessage = Window_Message.prototype.updateMessage;
        Window_Message.prototype.updateMessage = function() {
            var data = state();
            var mode = featureEnabled("textSpeed") && data ? data.textSpeed : "normal";
            if (mode === "normal") return originalUpdateMessage.apply(this, arguments);
            if (mode === "instant") {
                var previousShowFast = this._showFast;
                this._showFast = true;
                var instantResult = originalUpdateMessage.apply(this, arguments);
                this._showFast = previousShowFast;
                return instantResult;
            }
            var firstResult = originalUpdateMessage.apply(this, arguments);
            if (firstResult && this._textState && !this.pause && !(this._waitCount > 0)) {
                return originalUpdateMessage.apply(this, arguments) || firstResult;
            }
            return firstResult;
        };

        var originalUpdateBackground = Window_Message.prototype.updateBackground;
        if (typeof originalUpdateBackground === "function") {
            Window_Message.prototype.updateBackground = function() {
                var result = originalUpdateBackground.apply(this, arguments);
                applyMessageOpacity(this);
                return result;
            };
        }

        var originalUpdateBackgroundDimmer = Window_Message.prototype.updateBackgroundDimmer;
        if (typeof originalUpdateBackgroundDimmer === "function") {
            Window_Message.prototype.updateBackgroundDimmer = function() {
                var result = originalUpdateBackgroundDimmer.apply(this, arguments);
                var data = state();
                if (featureEnabled("messageOpacity") && data && data.messageOpacity !== 100 &&
                        this._dimmerSprite) {
                    this._dimmerSprite.opacity = Math.round(
                        Number(this.openness || 255) * data.messageOpacity / 100
                    );
                }
                return result;
            };
        }

        function messageHasInteractiveInput(windowObject) {
            try {
                if (windowObject.isAnySubWindowActive && windowObject.isAnySubWindowActive()) {
                    return true;
                }
                if ($gameMessage.isChoice && $gameMessage.isChoice()) return true;
                if ($gameMessage.isNumberInput && $gameMessage.isNumberInput()) return true;
                if ($gameMessage.isItemChoice && $gameMessage.isItemChoice()) return true;
            } catch (error) {}
            return false;
        }

        function autoAdvanceDelayFrames() {
            var raw = "";
            try { raw = String($gameMessage.allText() || ""); } catch (error) {}
            var readable = raw.replace(/\\[A-Za-z]+\[[^\]]*\]|\\[.\\|!><^{}]/g, "");
            var baseDelay = Math.max(120, Math.min(360, 90 + readable.length * 3));
            var data = state();
            var speed = data ? data.autoAdvanceSpeed : 50;
            var factor;
            if (speed <= 33) {
                // Preserve the former full range in roughly the first third.
                factor = 1.5 - speed / 33;
            } else {
                // The remaining range continues from the former maximum
                // (0.5x delay) down to a very fast 0.05x delay.
                factor = 0.5 - ((speed - 33) / 67) * 0.45;
            }
            return Math.max(6, Math.round(baseDelay * factor));
        }

        var originalUpdateInput = Window_Message.prototype.updateInput;
        if (typeof originalUpdateInput === "function") {
            Window_Message.prototype.updateInput = function() {
                var result = originalUpdateInput.apply(this, arguments);
                var data = state();
                if (!featureEnabled("autoAdvance") || !data || !data.autoAdvance ||
                        !this.pause || messageHasInteractiveInput(this)) {
                    this._renpyLensAutoFrames = null;
                    return result;
                }
                if (this._textState && this._textState.index < this._textState.text.length) {
                    this._renpyLensAutoFrames = null;
                    return result;
                }
                if (this._renpyLensAutoFrames == null) {
                    this._renpyLensAutoFrames = autoAdvanceDelayFrames();
                }
                this._renpyLensAutoFrames -= 1;
                if (this._renpyLensAutoFrames <= 0) {
                    this._renpyLensAutoFrames = null;
                    this.pause = false;
                    this._textState = null;
                    this.terminateMessage();
                }
                return true;
            };
        }
    }

    var originalSetupChild = Game_Interpreter.prototype.setupChild;
    if (typeof originalSetupChild === "function") {
        Game_Interpreter.prototype.setupChild = function() {
            var result = originalSetupChild.apply(this, arguments);
            if (this._childInterpreter) {
                this._childInterpreter._renpyLensParentInterpreter = this;
                this._childInterpreter._renpyLensTextReplacements = [];
            }
            return result;
        };
    }

    var originalCommand355 = Game_Interpreter.prototype.command355;
    if (typeof originalCommand355 === "function") {
        Game_Interpreter.prototype.command355 = function() {
            var rules = textReplacementRules(commandScript(this));
            var result = originalCommand355.apply(this, arguments);
            if (rules.length) {
                // Nested message-filter common events should inform the
                // message interpreter which later builds the prefetch list.
                var target = this;
                while (target) {
                    if (!target._renpyLensTextReplacements) {
                        target._renpyLensTextReplacements = [];
                    }
                    Array.prototype.push.apply(target._renpyLensTextReplacements, rules);
                    target = target._renpyLensParentInterpreter || null;
                }
            }
            return result;
        };
    }

    var originalCommand101 = Game_Interpreter.prototype.command101;
    Game_Interpreter.prototype.command101 = function() {
        lastInterpreter = this;
        return originalCommand101.apply(this, arguments);
    };

    var originalStartMessage = Window_Message.prototype.startMessage;
    Window_Message.prototype.startMessage = function() {
        var result = originalStartMessage.apply(this, arguments);
        var raw = "";
        try { raw = $gameMessage.allText(); } catch (error) {}
        lastMessageSegment = makeSegment(raw, this, "", false);
        emitCurrent(lastMessageSegment, [], false, interpreterPrefetch(this));
        return result;
    };

    var originalChoiceStart = Window_ChoiceList.prototype.start;
    Window_ChoiceList.prototype.start = function() {
        var result = originalChoiceStart.apply(this, arguments);
        var windowObject = this._messageWindow || null;
        var choices = [];
        var list = this._list || [];
        for (var index = 0; index < list.length; index += 1) {
            choices.push(makeSegment(String(list[index].name || ""), windowObject, "", false));
        }
        emitCurrent(lastMessageSegment || makeSegment("", windowObject, "", false), choices, true, []);
        return result;
    };

    if (qolEnabled) setupQoL();

    send({
        type: "hook_ready",
        capabilities: ["dialogue", "speaker", "choices", "prefetch", "offline_scan"]
    });
    send({ type: "runtime_ready" });
})();
