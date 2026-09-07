# -*- coding: utf-8 -*-

import json
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required for bridge tests")
class RpgMakerBridgeTests(unittest.TestCase):
    def test_save_anywhere_uses_native_save_scene_only_from_map(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
function Element() {{ this.children=[]; this.listeners={{}}; this.style={{}}; this.parentNode=null; this.id=''; this.textContent=''; }}
Element.prototype.appendChild = function(child) {{ child.parentNode=this; this.children.push(child); }};
Element.prototype.addEventListener = function(name, fn) {{ this.listeners[name]=fn; }};
Element.prototype.setAttribute = function() {{}};
function findById(node, id) {{
  if (node.id === id) return node;
  for (const child of node.children) {{ const found=findById(child,id); if (found) return found; }}
  return null;
}}
const body = new Element();
const documentListeners = {{}};
const document = {{
  body, createElement: () => new Element(), getElementById: id => findById(body,id),
  addEventListener: (name, fn) => {{ documentListeners[name]=fn; }}
}};
function Game_System() {{}} Game_System.prototype.initialize = function() {{}};
Game_System.prototype.isSaveEnabled = function() {{ return false; }};
function Game_Player() {{}} Game_Player.prototype.isThrough = () => false;
Game_Player.prototype.realMoveSpeed = () => 4;
Game_Player.prototype.executeEncounter = () => true;
function Game_Interpreter() {{}} Game_Interpreter.prototype.command101 = () => true;
Game_Interpreter.prototype.updateWaitMode = () => false;
function Window_Message() {{}} Window_Message.prototype.startMessage = function() {{}};
Window_Message.prototype.convertEscapeCharacters = value => value;
Window_Message.prototype.updateMessage = () => false;
function Window_ChoiceList() {{ this._list=[]; }} Window_ChoiceList.prototype.start = function() {{}};
function Scene_Base() {{}} Scene_Base.prototype.update = function() {{}};
function inheritScene(child) {{ child.prototype=Object.create(Scene_Base.prototype); child.prototype.constructor=child; }}
function Scene_Title() {{}} inheritScene(Scene_Title);
function Scene_Boot() {{}} inheritScene(Scene_Boot);
function Scene_Map() {{}} inheritScene(Scene_Map);
function Scene_Save() {{}} inheritScene(Scene_Save);
function Scene_Menu() {{}} inheritScene(Scene_Menu);
function Scene_Battle() {{}} inheritScene(Scene_Battle);
const pushed = [];
const SceneManager = {{ _scene:new Scene_Map(), push: scene => pushed.push(scene) }};
let messageBusy = false;
let eventRunning = false;
const context = {{
  console, JSON, Object, String, Number, Math, Array,
  PluginManager: {{ parameters: () => ({{
    qolEnabled:'true', qolLocale:'en_US', socketPort:'1',
    qolFeatures:JSON.stringify({{saveAnywhere:true}})
  }}) }},
  Utils: {{ RPGMAKER_NAME:'MV', RPGMAKER_VERSION:'1.6.2' }},
  Game_System, Game_Player, Game_Interpreter, Window_Message, Window_ChoiceList,
  Scene_Base, Scene_Title, Scene_Boot, Scene_Map, Scene_Save, Scene_Menu, Scene_Battle,
  SceneManager, document,
  window: {{ setTimeout: () => 1, clearTimeout: () => {{}}, addEventListener: () => {{}} }},
  $gameMessage: {{ allText: () => '', isBusy: () => messageBusy }},
  $gameMap: {{ isEventRunning: () => eventRunning }},
  $gameParty: {{ inBattle: () => false, members: () => [] }},
  $gameVariables: {{ value: () => 0 }}, $gameActors: {{ actor: () => null }},
  TextManager: {{ currencyUnit:'G' }},
  require: () => {{ throw new Error('disabled'); }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
context.$gameSystem = new context.Game_System(); context.$gameSystem.initialize();
context.SceneManager._scene.update();
const controls = document.getElementById('renpylens-qol-controls');
if (!controls || controls.children.length !== 1) throw new Error('save button missing');
const saveButton = controls.children[0];
if (saveButton.textContent !== 'Save' || saveButton.style.display !== 'block') {{
  throw new Error('save button was not visible on the map');
}}
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (pushed.length !== 1 || pushed[0] !== context.Scene_Save) {{
  throw new Error('map save did not open exactly once');
}}
context.SceneManager._scene = new context.Scene_Save(); context.SceneManager._scene.update();
context.SceneManager._scene = new context.Scene_Map(); context.SceneManager._scene.update();
eventRunning = true;
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (pushed.length !== 2) throw new Error('running event blocked save');
context.SceneManager._scene = new context.Scene_Save(); context.SceneManager._scene.update();
context.SceneManager._scene = new context.Scene_Map(); context.SceneManager._scene.update();
eventRunning = false;
messageBusy = true;
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (pushed.length !== 3) throw new Error('dialogue blocked save');
context.SceneManager._scene = new context.Scene_Save(); context.SceneManager._scene.update();
context.SceneManager._scene = new context.Scene_Map(); context.SceneManager._scene.update();
let prevented = false;
documentListeners.keydown({{
  repeat:false, ctrlKey:true, altKey:false, metaKey:false, keyCode:83,
  preventDefault() {{ prevented=true; }}
}});
documentListeners.keydown({{
  repeat:true, ctrlKey:true, altKey:false, metaKey:false, keyCode:83,
  preventDefault() {{ throw new Error('repeat was handled'); }}
}});
if (!prevented || pushed.length !== 4) throw new Error('Ctrl+S did not open save exactly once');
for (const scene of [new context.Scene_Title(), new context.Scene_Menu(), new context.Scene_Battle(), new context.Scene_Save()]) {{
  context.SceneManager._scene = scene; scene.update();
  let blockedPrevented = false;
  documentListeners.keydown({{
    repeat:false, ctrlKey:true, altKey:false, metaKey:false, keyCode:83,
    preventDefault() {{ blockedPrevented=true; }}
  }});
  if (!blockedPrevented || pushed.length !== 4) throw new Error('save opened outside the map');
  if (saveButton.style.display !== 'none') throw new Error('save button visible outside the map');
}}
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_save_anywhere_uses_pre_dialogue_and_pre_choice_checkpoint(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
function Element() {{ this.children=[]; this.listeners={{}}; this.style={{}}; this.parentNode=null; this.id=''; this.textContent=''; }}
Element.prototype.appendChild = function(child) {{ child.parentNode=this; this.children.push(child); }};
Element.prototype.addEventListener = function(name, fn) {{ this.listeners[name]=fn; }};
Element.prototype.setAttribute = function() {{}};
function findById(node, id) {{
  if (node.id === id) return node;
  for (const child of node.children) {{ const found=findById(child,id); if (found) return found; }}
  return null;
}}
const body = new Element();
const document = {{
  body, createElement: () => new Element(), getElementById: id => findById(body,id),
  addEventListener: () => {{}}
}};
function Game_System() {{}} Game_System.prototype.initialize = function() {{}};
function Game_Player() {{}} Game_Player.prototype.isThrough = () => false;
Game_Player.prototype.realMoveSpeed = () => 4;
Game_Player.prototype.executeEncounter = () => true;
let attachChoice = false;
function Game_Interpreter() {{ this._index=0; this._indent=0; this._branch={{}}; this._list=[]; }}
Game_Interpreter.prototype.currentCommand = function() {{ return this._list[this._index]; }};
Game_Interpreter.prototype.command101 = function() {{
  this._index += 1;
  if (attachChoice) this.setupChoices([]);
  return false;
}};
Game_Interpreter.prototype.command102 = function() {{ this.setupChoices([]); this._index += 1; return false; }};
Game_Interpreter.prototype.setupChoices = function() {{}};
Game_Interpreter.prototype.command404 = function() {{ return true; }};
Game_Interpreter.prototype.clear = function() {{ this._branch={{}}; }};
Game_Interpreter.prototype.updateWaitMode = () => false;
function Window_Message() {{}} Window_Message.prototype.startMessage = function() {{}};
Window_Message.prototype.convertEscapeCharacters = value => value;
Window_Message.prototype.updateMessage = () => false;
function Window_ChoiceList() {{ this._list=[]; }} Window_ChoiceList.prototype.start = function() {{}};
function Scene_Base() {{}} Scene_Base.prototype.update = function() {{}};
function inheritScene(child) {{ child.prototype=Object.create(Scene_Base.prototype); child.prototype.constructor=child; }}
function Scene_Title() {{}} inheritScene(Scene_Title);
function Scene_Boot() {{}} inheritScene(Scene_Boot);
function Scene_Map() {{}} inheritScene(Scene_Map);
function Scene_Save() {{}} inheritScene(Scene_Save); Scene_Save.prototype.terminate = function() {{}};
let marker = 0;
let activeInterpreter = null;
const DataManager = {{
  makeSaveContents: () => ({{
    marker,
    interpreterIndex: activeInterpreter ? activeInterpreter._index : -1,
    branch: activeInterpreter ? Object.assign({{}}, activeInterpreter._branch) : {{}}
  }})
}};
const JsonEx = {{ stringify: JSON.stringify, parse: JSON.parse }};
const pushed = [];
const mapScene = new Scene_Map();
const SceneManager = {{ _scene:mapScene, push: scene => pushed.push(scene) }};
let messageBusy = false;
const context = {{
  console, JSON, Object, String, Number, Math, Array,
  PluginManager: {{ parameters: () => ({{
    qolEnabled:'true', qolLocale:'en_US', socketPort:'1',
    qolFeatures:JSON.stringify({{saveAnywhere:true}})
  }}) }},
  Utils: {{ RPGMAKER_NAME:'MV', RPGMAKER_VERSION:'1.6.2' }},
  Game_System, Game_Player, Game_Interpreter, Window_Message, Window_ChoiceList,
  Scene_Base, Scene_Title, Scene_Boot, Scene_Map, Scene_Save,
  SceneManager, DataManager, JsonEx, document,
  window: {{ setTimeout: () => 1, clearTimeout: () => {{}}, addEventListener: () => {{}} }},
  $gameMessage: {{ allText: () => '', isBusy: () => messageBusy }},
  $gameMap: {{ isEventRunning: () => true }},
  $gameParty: {{ inBattle: () => false, members: () => [] }},
  $gameVariables: {{ value: () => 0 }}, $gameActors: {{ actor: () => null }},
  TextManager: {{ currencyUnit:'G' }},
  require: () => {{ throw new Error('disabled'); }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
context.$gameSystem = new context.Game_System(); context.$gameSystem.initialize();
mapScene.update();
const saveButton = document.getElementById('renpylens-qol-controls').children[0];
const interpreter = new context.Game_Interpreter();
activeInterpreter = interpreter;
interpreter._list = [
  {{code:101, indent:0, parameters:[]}},
  {{code:102, indent:0, parameters:[]}},
  {{code:402, indent:0, parameters:[1]}},
  {{code:404, indent:0, parameters:[]}},
  {{code:101, indent:0, parameters:[]}},
  {{code:0, indent:0, parameters:[]}}
];

marker = 10;
attachChoice = true;
interpreter.command101();
interpreter._branch[0] = 1;
marker = 20;
messageBusy = true;
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
const choiceSave = context.DataManager.makeSaveContents();
if (choiceSave.marker !== 10 || choiceSave.interpreterIndex !== 0 ||
    Object.keys(choiceSave.branch).length !== 0) {{
  throw new Error('selected choice was not restored to its unopened checkpoint');
}}
if (marker !== 20 || interpreter._branch[0] !== 1) {{
  throw new Error('checkpoint save changed the live event state');
}}

const firstSaveScene = new context.Scene_Save();
context.SceneManager._scene = firstSaveScene; firstSaveScene.update(); firstSaveScene.terminate();
context.SceneManager._scene = mapScene; mapScene.update();
messageBusy = false;
interpreter._index = 3;
interpreter.command404();
marker = 30;
attachChoice = false;
interpreter._index = 4;
interpreter.command101();
marker = 40;
messageBusy = true;
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
const dialogueSave = context.DataManager.makeSaveContents();
if (dialogueSave.marker !== 30 || dialogueSave.interpreterIndex !== 4) {{
  throw new Error('dialogue was not saved from immediately before it opened');
}}

const secondSaveScene = new context.Scene_Save();
context.SceneManager._scene = secondSaveScene; secondSaveScene.update(); secondSaveScene.terminate();
context.SceneManager._scene = mapScene; mapScene.update();
messageBusy = false;
marker = 50;
saveButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
const ordinarySave = context.DataManager.makeSaveContents();
if (ordinarySave.marker !== 50) throw new Error('ordinary map save used a stale dialogue checkpoint');
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_prefetch_escape_conversion_does_not_change_live_message_state(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
function Game_Interpreter() {{ this._index = 0; this._list = []; }}
Game_Interpreter.prototype.command101 = function() {{ return true; }};
function Window_Message() {{ this._wordWrap = false; }}
Window_Message.prototype.startMessage = function() {{
  this._wordWrap = String(context.$gameMessage.allText()).includes('<WordWrap>');
}};
Window_Message.prototype.convertEscapeCharacters = function(value) {{
  this._wordWrap = String(value).includes('<WordWrap>');
  return String(value).replace(/<\\/?WordWrap>/gi, '');
}};
function Window_ChoiceList() {{ this._list = []; }}
Window_ChoiceList.prototype.start = function() {{}};
const messages = [];
const fakeNet = {{
  createConnection: (options, callback) => {{
    const socket = {{
      end: data => messages.push(JSON.parse(data)),
      setTimeout: () => {{}}, on: () => {{}}, destroy: () => {{}}
    }};
    queueMicrotask(callback);
    return socket;
  }}
}};
const context = {{
  console, JSON, Object, String, Number, Math, Array, queueMicrotask,
  PluginManager: {{ parameters: () => ({{socketPort:'1'}}) }},
  Utils: {{ RPGMAKER_NAME:'MV', RPGMAKER_VERSION:'1.6.2' }},
  Game_Interpreter, Window_Message, Window_ChoiceList,
  require: name => {{ if (name === 'net') return fakeNet; throw new Error(name); }},
  $gameMessage: {{ allText: () => '<WordWrap>First<br>Second<BR />Third<br />Fourth' }},
  $gameVariables: {{ value: () => 0 }},
  $gameActors: {{ actor: () => null }},
  $gameParty: {{ members: () => [] }},
  TextManager: {{ currencyUnit:'G' }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
const interpreter = new context.Game_Interpreter();
interpreter._list = [
  {{code:101,parameters:[]}},
  {{code:401,parameters:['<WordWrap>First<br>Second<BR />Third<br />Fourth']}},
  {{code:101,parameters:[]}},
  {{code:401,parameters:['A future dialogue without a wrap tag.']}}
];
interpreter.command101();
const message = new context.Window_Message();
message.startMessage();
if (!message._wordWrap) {{
  throw new Error('prefetch escape conversion changed the live word-wrap state');
}}
queueMicrotask(() => {{
  const current = messages.filter(item => item.type === 'current').pop();
  const expected = 'First\\nSecond\\nThird\\nFourth';
  if (!current || current.current_segment.source !== expected ||
      current.current_segment.display_text !== expected) {{
    throw new Error('HTML break tags were not normalized to line breaks');
  }}
}});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_qol_encounter_guard_battle_outcomes_and_pointer_isolation(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
function Element() {{ this.children=[]; this.listeners={{}}; this.style={{}}; this.parentNode=null; this.id=''; this.textContent=''; }}
Element.prototype.appendChild = function(child) {{ child.parentNode=this; this.children.push(child); }};
Element.prototype.addEventListener = function(name, fn) {{ this.listeners[name]=fn; }};
Element.prototype.setAttribute = function() {{}};
function findById(node, id) {{
  if (node.id === id) return node;
  for (const child of node.children) {{ const found=findById(child,id); if (found) return found; }}
  return null;
}}
const body = new Element();
const document = {{
  body, createElement: () => new Element(), getElementById: id => findById(body,id),
  addEventListener: () => {{}}
}};
function Game_System() {{}} Game_System.prototype.initialize = function() {{}};
function Game_Player() {{ this.resetCount=0; }}
Game_Player.prototype.isThrough = () => false;
Game_Player.prototype.realMoveSpeed = () => 4;
Game_Player.prototype.canEncounter = () => true;
Game_Player.prototype.executeEncounter = () => true;
Game_Player.prototype.makeEncounterCount = function() {{ this.resetCount += 1; }};
function Game_Event(trigger, list) {{ this._trigger=trigger; this._list=list; this.touchChecks=0; }}
Game_Event.prototype.list = function() {{ return this._list; }};
Game_Event.prototype.checkEventTriggerTouch = function() {{ this.touchChecks += 1; }};
function Scene_Base() {{}} Scene_Base.prototype.update = function() {{}};
function Scene_Title() {{}} function Scene_Boot() {{}}
function Game_Interpreter() {{}} Game_Interpreter.prototype.command101 = () => true;
Game_Interpreter.prototype.updateWaitMode = function() {{ return this._waitMode === 'route'; }};
function Window_Message() {{}} Window_Message.prototype.startMessage = function() {{}};
Window_Message.prototype.convertEscapeCharacters = value => value;
Window_Message.prototype.updateMessage = () => false;
function Window_ChoiceList() {{ this._list=[]; }} Window_ChoiceList.prototype.start = function() {{}};
const enemy = {{
  hp: 100, states: [], refreshed: false, isAlive() {{ return this.hp > 0; }},
  deathStateId: () => 1, setHp(value) {{ this.hp=value; }}, addState(id) {{ this.states.push(id); }},
  isStateAffected(id) {{ return this.states.includes(id); }}, refresh() {{ this.refreshed=true; }}
}};
const actor = {{
  hp: 100, states: [], refreshed: false, isAlive() {{ return this.hp > 0; }},
  deathStateId: () => 1, setHp(value) {{ this.hp=value; }}, addState(id) {{ this.states.push(id); }},
  isStateAffected(id) {{ return this.states.includes(id); }}, refresh() {{ this.refreshed=true; }}
}};
let inBattle = true;
let messageBusy = false;
let victories = 0;
let defeats = 0;
let gameMouseDowns = 0;
const TouchInput = {{ _onMouseDown: () => {{ gameMouseDowns += 1; }} }};
const context = {{
  console, JSON, Object, String, Number, Math, Array,
  PluginManager: {{ parameters: () => ({{
    qolEnabled:'true', qolLocale:'en_US', socketPort:'1',
    qolFeatures:JSON.stringify({{moveSpeed:true,through:true,encounters:true,battleVictory:true}})
  }}) }},
  Utils: {{ RPGMAKER_NAME:'MV', RPGMAKER_VERSION:'1.6.2' }},
  Game_System, Game_Player, Game_Event, Scene_Base, Scene_Title, Scene_Boot, TouchInput,
  Game_Interpreter, Window_Message, Window_ChoiceList, document,
  window: {{ setTimeout: () => 1, clearTimeout: () => {{}} }},
  SceneManager: {{ _scene:new Scene_Base() }},
  $gameParty: {{ inBattle: () => inBattle, members: () => [actor], battleMembers: () => [actor] }},
  $gameTroop: {{ members: () => [enemy] }},
  BattleManager: {{
    processVictory: () => {{ victories += 1; }},
    processDefeat: () => {{ defeats += 1; }}
  }},
  $gameMessage: {{ allText: () => '', isBusy: () => messageBusy }},
  $gameVariables: {{ value: () => 0 }},
  $gameActors: {{ actor: () => null }}, TextManager: {{ currencyUnit:'G' }},
  require: () => {{ throw new Error('disabled'); }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
context.$gameSystem = new context.Game_System(); context.$gameSystem.initialize();
context.SceneManager._scene.update();
const blockedCharacter = {{
  _moveRouteForcing:true, _moveRoute:{{skippable:false}}, _moveRouteIndex:2,
  _waitCount:0, _x:10, _y:12, isMoving:() => false
}};
const blockedInterpreter = new context.Game_Interpreter();
blockedInterpreter._waitMode = 'route'; blockedInterpreter._character = blockedCharacter;
for (let frame=0; frame<119; frame += 1) blockedInterpreter.updateWaitMode();
if (blockedCharacter._moveRoute.skippable) throw new Error('move route was released too early');
blockedInterpreter.updateWaitMode();
if (!blockedCharacter._moveRoute.skippable) throw new Error('blocked move route stayed locked');
const controls = document.getElementById('renpylens-qol-controls');
if (!controls || controls.children.length !== 3) throw new Error('QoL controls missing');
const exploration = controls.children[0];
if (exploration.children.length !== 3) throw new Error('movement tools are not segmented');
if (exploration.children[0].textContent !== 'High speed: Off') {{
  throw new Error('English high-speed label is incorrect');
}}
if (!exploration.children[0].style.cssText.includes('appearance:none') ||
    !exploration.children[0].style.cssText.includes('box-shadow:none') ||
    !exploration.style.cssText.includes('box-shadow:none')) {{
  throw new Error('segmented control kept native button chrome');
}}
if (!exploration.children[0].style.cssText.includes('width:auto') ||
    !exploration.children[0].style.cssText.includes('white-space:nowrap')) {{
  throw new Error('segmented movement width is not content-based');
}}
exploration.children[0].listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
exploration.children[2].listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (!context.$gameSystem._rpgMakerQoL.speed || !context.$gameSystem._rpgMakerQoL.through) {{
  throw new Error('segmented movement controls did not toggle independently');
}}
controls.children[1].listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
const player = new context.Game_Player();
if (player.canEncounter() !== false) throw new Error('canEncounter was not blocked');
if (player.executeEncounter() !== false || player.resetCount !== 1) throw new Error('executeEncounter was not blocked');
const roamingEnemy = new context.Game_Event(2, [{{code:301,parameters:[0,7,true,false]}}]);
roamingEnemy.checkEventTriggerTouch();
if (roamingEnemy.touchChecks !== 0) throw new Error('roaming touch battle was not blocked');
const storyEvent = new context.Game_Event(0, [{{code:301,parameters:[0,7,false,false]}}]);
storyEvent.checkEventTriggerTouch();
if (storyEvent.touchChecks !== 1) throw new Error('explicit story battle was blocked');
const ordinaryTouch = new context.Game_Event(2, [{{code:101,parameters:[]}}]);
messageBusy = true;
ordinaryTouch.checkEventTriggerTouch();
if (ordinaryTouch.touchChecks !== 0) throw new Error('touch event ran during a message');
messageBusy = false;
ordinaryTouch.checkEventTriggerTouch();
if (ordinaryTouch.touchChecks !== 1) throw new Error('ordinary touch event stayed blocked');
const outcome = controls.children[2];
if (outcome.children.length !== 3) throw new Error('battle outcome is not segmented');
outcome.children[0].listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (enemy.hp !== 0 || !enemy.states.includes(1)) throw new Error('enemy was not defeated');
if (victories !== 1) throw new Error('victory flow was not started');
outcome.children[2].listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (actor.hp !== 0 || !actor.states.includes(1)) throw new Error('party was not defeated');
if (defeats !== 1) throw new Error('defeat flow was not started');
context.TouchInput._onMouseDown({{target:exploration.children[0]}});
if (gameMouseDowns !== 0) throw new Error('toolbar click reached RPG Maker TouchInput');
context.TouchInput._onMouseDown({{target:body}});
if (gameMouseDowns !== 1) throw new Error('ordinary game click was blocked');
let prevented = false, stopped = false, immediate = false;
controls.listeners.mousedown({{
  preventDefault() {{ prevented=true; }}, stopPropagation() {{ stopped=true; }},
  stopImmediatePropagation() {{ immediate=true; }}
}});
if (!prevented || !stopped || !immediate) throw new Error('DOM pointer event was not isolated');
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_variable_only_dialogue_uses_visible_value_and_prefetches_parent_event(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
const messages = [];
const fakeNet = {{
  createConnection: (options, callback) => {{
    const socket = {{
      end: data => messages.push(JSON.parse(data)),
      setTimeout: () => {{}}, on: () => {{}}, destroy: () => {{}}
    }};
    queueMicrotask(callback);
    return socket;
  }}
}};
function Game_Interpreter() {{ this._index = 0; this._list = []; }}
Game_Interpreter.prototype.command101 = function() {{ return true; }};
Game_Interpreter.prototype.command355 = function() {{ return true; }};
Game_Interpreter.prototype.setupChild = function(list) {{
  this._childInterpreter = new Game_Interpreter();
  this._childInterpreter._list = list;
}};
function Window_Message() {{}}
Window_Message.prototype.startMessage = function() {{}};
Window_Message.prototype.convertEscapeCharacters = function(value) {{
  return String(value)
    .replace(/\\\\V\\[(\\d+)\\]/gi,
      (match, id) => String(context.$gameVariables.value(Number(id))))
    .replace(/\\\\w\\[(\\d+)\\]/gi,
      (match, frames) => '\\x1bw[' + frames + ']');
}};
function Window_ChoiceList() {{ this._list = []; }}
Window_ChoiceList.prototype.start = function() {{}};
let variableValue = 'This whole sentence comes from variable 25.';
const context = {{
  console, JSON, Object, String, Number, Math, Array, queueMicrotask,
  PluginManager: {{ parameters: () => ({{socketPort:'19876',sessionId:'test'}}) }},
  Utils: {{ RPGMAKER_NAME:'MV', RPGMAKER_VERSION:'1.6.2' }},
  Game_Interpreter, Window_Message, Window_ChoiceList,
  require: name => {{ if (name === 'net') return fakeNet; throw new Error(name); }},
  $gameMessage: {{ allText: () => '\\\\V[25]\\\\w[5]' }},
  $gameVariables: {{ value: () => variableValue }},
  $gameActors: {{ actor: id => Number(id) === 1 ? {{name: () => 'Henry'}} : null }},
  $gameParty: {{ members: () => [] }},
  TextManager: {{ currencyUnit:'G' }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
const parent = new context.Game_Interpreter();
parent._index = 1;
parent._list = [
  {{code:117,parameters:[2]}},
  {{code:355,parameters:['$gameVariables.setValue(21, "HeSaOp#>.You know, 1Hero. This metadata format is different.");']}},
  {{code:117,parameters:[2]}},
  {{code:355,parameters:['$gameVariables.setValue(21, "Br,fr,op.VaIerie has the next line in the parent event.");']}},
  {{code:117,parameters:[2]}},
  {{code:355,parameters:['$gameVariables.setValue(21, "Va,fr,op.Don’t worry Dear.");']}},
  {{code:117,parameters:[2]}}
];
parent.setupChild([
  {{code:355,parameters:['var res = text.replace("Dear", "Brad");']}},
  {{code:655,parameters:['res = res.replace("VaIerie", "Valerie");']}},
  {{code:101,parameters:[]}},
  {{code:101,parameters:[]}},
  {{code:401,parameters:['\\\\V[25]']}}
]);
const interpreter = parent._childInterpreter;
interpreter.command355();
interpreter.setupChild([
  {{code:355,parameters:['var res = $gameVariables.value(25).replace(/1Hero/g, $gameActors.actor(1).name());']}},
  {{code:655,parameters:['$gameVariables.setValue(25, res);']}}
]);
interpreter._childInterpreter.command355();
interpreter._index = 2;
interpreter.command101();
const message = new context.Window_Message();
message.startMessage();
queueMicrotask(() => {{
  const current = messages.filter(item => item.type === 'current').pop();
  if (!current) throw new Error('current payload missing');
  if (current.current_segment.source !== variableValue) throw new Error('visible variable value was not used');
  if (Object.keys(current.current_segment.token_values).length) throw new Error('resolved variable token leaked');
  if (current.prefetch.length !== 3) throw new Error('parent-event dialogue was not prefetched');
  if (current.prefetch[0].source !== 'You know, Henry. This metadata format is different.') throw new Error('generic metadata or actor replacement failed');
  if (current.prefetch[1].source !== 'Valerie has the next line in the parent event.') throw new Error('executed continuation replacement was not applied');
  if (current.prefetch[2].source !== 'Don’t worry Brad.') throw new Error('runtime name substitution was not applied');

  variableValue = '42';
  context.$gameMessage.allText = () => 'HP: \\\\V[25]';
  message.startMessage();
  queueMicrotask(() => {{
    const mixed = messages.filter(item => item.type === 'current').pop();
    if (mixed.current_segment.source !== 'HP: ⟦RL_V_25⟧') throw new Error('mixed variable token was not preserved');
    if (mixed.current_segment.token_values['⟦RL_V_25⟧'] !== '42') throw new Error('mixed token value missing');
  }});
}});
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_feature_filter_and_text_speed_modes(self):
        bridge = json.dumps(str(ROOT / "assets" / "RenpyLensBridge.js"))
        script = f"""
const fs = require('fs');
const vm = require('vm');
function Element(tag) {{
  this.tag = tag; this.children = []; this.listeners = {{}}; this.style = {{}};
  this.parentNode = null; this.id = ''; this.textContent = '';
}}
Element.prototype.appendChild = function(child) {{ child.parentNode = this; this.children.push(child); }};
Element.prototype.addEventListener = function(name, fn) {{ this.listeners[name] = fn; }};
Element.prototype.setAttribute = function(name, value) {{ this[name] = value; }};
function findById(node, id) {{
  if (node.id === id) return node;
  for (const child of node.children) {{ const found = findById(child, id); if (found) return found; }}
  return null;
}}
const body = new Element('body');
const document = {{
  body,
  keydown: null,
  createElement: tag => new Element(tag),
  getElementById: id => findById(body, id),
  addEventListener: (name, fn) => {{ if (name === 'keydown') document.keydown = fn; }}
}};
function Game_System() {{}} Game_System.prototype.initialize = function() {{}};
function Game_Player() {{}} Game_Player.prototype.isThrough = () => false;
Game_Player.prototype.realMoveSpeed = () => 4;
Game_Player.prototype.executeEncounter = () => true;
function Scene_Base() {{}} Scene_Base.prototype.update = function() {{}};
function Scene_Title() {{}} Scene_Title.prototype = Object.create(Scene_Base.prototype);
function Scene_Boot() {{}} Scene_Boot.prototype = Object.create(Scene_Base.prototype);
function Game_Interpreter() {{}} Game_Interpreter.prototype.command101 = function() {{ return true; }};
function Window_Message() {{ this._textState = {{ index: 0, text: 'abc' }}; this._waitCount = 0; this.pause = false; this._showFast = false; this.calls = 0; this.fastSeen = false; this._background = 0; this.opacity = 255; this.openness = 255; this.terminated = false; }}
Window_Message.prototype.startMessage = function() {{}};
Window_Message.prototype.convertEscapeCharacters = value => value;
Window_Message.prototype.updateBackground = function() {{ this.opacity = 255; }};
Window_Message.prototype.updateInput = function() {{ return !!this.pause; }};
Window_Message.prototype.isAnySubWindowActive = function() {{ return false; }};
Window_Message.prototype.terminateMessage = function() {{ this.terminated = true; }};
Window_Message.prototype.updateMessage = function() {{
  this.calls += 1; this.fastSeen = this.fastSeen || this._showFast;
  if (this.triggerWait) this._waitCount = 10;
  if (this.triggerPause) this.pause = true;
  return true;
}};
function Window_ChoiceList() {{ this._list = []; }} Window_ChoiceList.prototype.start = function() {{}};
const params = {{
  qolEnabled: 'true', qolLocale: 'en_US', socketPort: '1',
  qolFeatures: JSON.stringify({{textSpeed:true,messageOpacity:true,autoAdvance:true,moveSpeed:false,through:false,encounters:false,battleVictory:false}})
}};
const context = {{
  console, JSON, Object, String, Number, Math, Array,
  PluginManager: {{ parameters: () => params }},
  Utils: {{ RPGMAKER_NAME: 'MV', RPGMAKER_VERSION: '1.6.2' }},
  Game_System, Game_Player, Scene_Base, Scene_Title, Scene_Boot,
  Game_Interpreter, Window_Message, Window_ChoiceList,
  SceneManager: {{ _scene: new Scene_Base() }}, document,
  window: {{ setTimeout: () => 1, clearTimeout: () => {{}} }},
  require: () => {{ throw new Error('disabled'); }},
  $gameMessage: {{ allText: () => 'hello', choice: false, isChoice() {{ return this.choice; }}, isNumberInput: () => false, isItemChoice: () => false }},
  $gameParty: {{ inBattle: () => false, members: () => [] }},
  $gameTroop: {{ members: () => [] }},
  $gameVariables: {{ value: () => 0 }}, $gameActors: {{ actor: () => null }},
  TextManager: {{ currencyUnit: 'G' }}
}};
vm.createContext(context);
vm.runInContext(fs.readFileSync({bridge}, 'utf8'), context);
context.$gameSystem = new context.Game_System(); context.$gameSystem.initialize();
context.SceneManager._scene.update();
const controls = document.getElementById('renpylens-qol-controls');
if (!controls || controls.children.length !== 3) throw new Error('feature filtering failed');
const textButton = controls.children[0];
if (!textButton.style.cssText.includes('width:auto') ||
    !textButton.style.cssText.includes('white-space:nowrap')) {{
  throw new Error('tool button width is not content-based');
}}
let message = new context.Window_Message(); message.updateMessage();
if (message.calls !== 1) throw new Error('1x mode failed');
textButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
message = new context.Window_Message(); message.updateMessage();
if (message.calls !== 2) throw new Error('2x mode failed');
message = new context.Window_Message(); message.triggerWait = true; message.updateMessage();
if (message.calls !== 1) throw new Error('2x wait handling failed');
message = new context.Window_Message(); message.triggerPause = true; message.updateMessage();
if (message.calls !== 1) throw new Error('2x pause handling failed');
textButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
message = new context.Window_Message(); message.updateMessage();
if (message.calls !== 1 || !message.fastSeen) throw new Error('instant mode failed');
const opacityButton = controls.children[1];
context.SceneManager._scene._messageWindow = message;
opacityButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (context.$gameSystem._rpgMakerQoL.messageOpacity !== 70 || message.opacity !== 179) throw new Error('opacity failed');
const autoControl = controls.children[2];
const autoButton = autoControl.children[0];
const autoPanel = autoControl.children[1];
const autoRange = autoPanel.children[0];
if (!autoButton.style.cssText.includes('width:auto') ||
    !autoButton.style.cssText.includes('white-space:nowrap')) {{
  throw new Error('Auto button width is not content-based');
}}
if (autoButton.textContent !== 'Auto' || autoPanel.style.display !== 'none') {{
  throw new Error('localized auto-advance compact state failed');
}}
if (context.$gameSystem._rpgMakerQoL.autoAdvanceSpeed !== 50) {{
  throw new Error('AUTO speed default failed');
}}
autoButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (!context.$gameSystem._rpgMakerQoL.autoAdvance || autoPanel.style.display !== 'block') {{
  throw new Error('AUTO button did not reveal speed slider');
}}
autoRange.value = '100';
autoRange.listeners.input({{target:autoRange}});
if (context.$gameSystem._rpgMakerQoL.autoAdvanceSpeed !== 100) {{
  throw new Error('AUTO speed slider failed');
}}
let rangePrevented = false, rangeStopped = false, rangeImmediate = false;
controls.listeners.pointerdown({{
  target:autoRange,
  preventDefault() {{ rangePrevented=true; }},
  stopPropagation() {{ rangeStopped=true; }},
  stopImmediatePropagation() {{ rangeImmediate=true; }}
}});
if (rangePrevented || !rangeStopped || !rangeImmediate) {{
  throw new Error('range drag was cancelled or leaked to the game');
}}
message = new context.Window_Message(); message._textState = null; message.pause = true;
for (let i = 0; i < 5; i += 1) message.updateInput();
if (message.terminated) throw new Error('fast AUTO advanced too early');
message.updateInput();
if (!message.terminated) throw new Error('fast AUTO speed was not applied');
autoRange.value = '35';
autoRange.listeners.input({{target:autoRange}});
message = new context.Window_Message(); message._textState = null; message.pause = true;
for (let i = 0; i < 57; i += 1) message.updateInput();
if (message.terminated) throw new Error('one-third AUTO speed advanced too early');
message.updateInput();
if (!message.terminated) throw new Error('former maximum was not mapped near one third');
message = new context.Window_Message(); message.pause = true;
for (let i = 0; i < 150; i += 1) message.updateInput();
if (message.terminated) throw new Error('forced pause was skipped');
context.$gameMessage.choice = true;
message = new context.Window_Message(); message._textState = null; message.pause = true;
for (let i = 0; i < 150; i += 1) message.updateInput();
if (message.terminated) throw new Error('choice was skipped');
context.$gameMessage.choice = false;
autoRange.listeners.blur();
if (!context.$gameSystem._rpgMakerQoL.autoAdvance || autoPanel.style.display !== 'none') {{
  throw new Error('AUTO slider blur changed mode or left the panel open');
}}
autoButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (!context.$gameSystem._rpgMakerQoL.autoAdvance || autoPanel.style.display !== 'block') {{
  throw new Error('enabled AUTO slider could not be reopened');
}}
autoButton.listeners.click({{preventDefault(){{}},stopPropagation(){{}}}});
if (context.$gameSystem._rpgMakerQoL.autoAdvance || autoPanel.style.display !== 'none') {{
  throw new Error('AUTO button did not disable and hide slider');
}}
let prevented = false;
document.keydown({{repeat:false,ctrlKey:false,altKey:false,metaKey:false,keyCode:71,preventDefault(){{prevented=true;}}}});
if (prevented || context.$gameSystem._rpgMakerQoL.speed) throw new Error('disabled shortcut ran');
document.keydown({{repeat:false,ctrlKey:true,altKey:false,metaKey:false,keyCode:83,preventDefault(){{prevented=true;}}}});
if (prevented) throw new Error('disabled save shortcut ran');
"""
        result = subprocess.run(
            [shutil.which("node"), "-e", script],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
