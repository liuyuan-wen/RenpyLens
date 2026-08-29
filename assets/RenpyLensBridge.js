/*:
 * @plugindesc RenpyLens runtime text bridge for RPG Maker MV/MZ.
 * @author RenpyLens
 * @param socketPort
 * @type number
 * @default 19876
 * @param sessionId
 * @type string
 * @default
 * @help This file is installed and removed automatically by RenpyLens.
 */

(function() {
    "use strict";

    var PLUGIN_NAME = "RenpyLensBridge";
    var parameters = PluginManager.parameters(PLUGIN_NAME) || {};
    var socketPort = Number(parameters.socketPort || 19876);
    var sessionId = String(parameters.sessionId || "");
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
        source = source.replace(/\\(?:C|I|FS|PX|PY|OW|OC|SE|SP|SM|SA)\[[^\]]*\]/gi, "");
        source = source.replace(/\\[.\\|!><^{}]/g, "");
        source = source.replace(/<\/?(?:WordWrap|CENTER|LEFT|RIGHT|TOP|MIDDLE|BOTTOM)>/gi, "");
        source = source.replace(/[ \t]+\n/g, "\n").trim();
        return { source: source, token_values: values };
    }

    function stripRendered(text) {
        var value = String(text || "").replace(/\x1b(?:C|I|FS|PX|PY|OW|OC|SE|SP|SM|SA)\[[^\]]*\]/gi, "");
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

    function makeSegment(raw, windowObject, speakerHint, italic) {
        var extracted = extractSpeaker(raw, windowObject);
        if (speakerHint) extracted.speaker = String(speakerHint);
        var tokens = tokenData(extracted.text);
        return {
            source: tokens.source,
            display_text: resolveRaw(windowObject, extracted.text),
            token_values: tokens.token_values,
            who: stripRendered(extracted.speaker),
            italic: !!italic
        };
    }

    function interpreterPrefetch(windowObject) {
        var items = [];
        var seen = {};
        var interpreter = lastInterpreter;
        if (!interpreter || !interpreter._list) return items;
        var list = interpreter._list;
        var index = Number(interpreter._index || 0) + 1;
        var boundaries = { 102:1, 111:1, 112:1, 113:1, 115:1, 119:1, 201:1, 301:1 };
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
                var segment = makeSegment(lines.join("\n"), windowObject, nativeSpeaker, false);
                if (segment.source && !seen[segment.source]) {
                    seen[segment.source] = true;
                    items.push(segment);
                }
                index = cursor;
                continue;
            }
            index += 1;
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

    send({
        type: "hook_ready",
        capabilities: ["dialogue", "speaker", "choices", "prefetch", "offline_scan"]
    });
    send({ type: "runtime_ready" });
})();
