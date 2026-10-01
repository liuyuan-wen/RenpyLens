import { parser } from '@lezer/python';

const cache = new Map();
const formulas = new Map();
let nextFormulaId = 0;
export function releaseExpressionCaches() { cache.clear(); formulas.clear(); }
const TRUE = Object.freeze({ op: 'constant', value: true, key: 'True' });
const FALSE = Object.freeze({ op: 'constant', value: false, key: 'False' });
export const readable = text => String(text ?? '').split('\n').map(line => line.replace(/\s+/g, ' ').trim()).join('\n')
  .replace(/\{\/?(?:b|i|u|s|plain|size|color|font|outlinecolor|alpha|a|cps|w|p|nw|fast|k|rt|rb)(?:=[^{}]*)?\}/g, '')
  .replace(/\[\[([^\]\n]+)\]/g, '[$1]');

export function expression(source) {
  source = String(source ?? '').trim();
  if (cache.has(source)) return cache.get(source);
  const tree = parser.parse(source), statement = tree.topNode.firstChild;
  let invalid = false; tree.iterate({ enter(node) { if (node.type.isError) invalid = true; } });
  function read(node) {
    if (!node) return null;
    const text = source.slice(node.from, node.to), children = [];
    for (let child = node.firstChild; child; child = child.nextSibling) children.push(child);
    if (node.name === 'ParenthesizedExpression') return read(children[1]);
    if (node.name === 'Boolean') return { type: 'literal', value: text === 'True', text };
    if (node.name === 'None') return { type: 'literal', value: null, text };
    if (node.name === 'Number') return { type: 'literal', value: Number(text), text };
    if (node.name === 'String' && /^(['"])/.test(text)) return { type: 'literal', text,
      value: text.slice(1, -1).replace(/\\([\\'"nt])/g, (_, c) => ({ n: '\n', t: '\t' })[c] ?? c) };
    if (node.name === 'VariableName' || node.name === 'MemberExpression') return { type: 'name', text };
    if (node.name === 'UnaryExpression') {
      const operator = source.slice(children[0].from, children[0].to);
      function prefix(child) {
        const parts = []; for (let c = child.firstChild; c; c = c.nextSibling) parts.push(c);
        const op = child.name === 'BinaryExpression' ? source.slice(parts[0].to, parts.at(-1).from).trim() : '';
        // Lezer groups an unparenthesized `not a and b` as `not (a and b)`.
        // Python binds not above and/or, but below comparisons.
        if (operator === 'not' && ['and', 'or'].includes(op)) {
          const left = prefix(parts[0]), right = read(parts.at(-1));
          return { type: 'binary', operator: op, left, right, text: `${left.text} ${op} ${right.text}` };
        }
        const value = read(child);
        return { type: 'unary', operator, child: value, text: `${operator} (${value.text})` };
      }
      return prefix(children[1]);
    }
    if (node.name === 'BinaryExpression') return { type: 'binary', operator: source.slice(children[0].to, children.at(-1).from).trim(), left: read(children[0]), right: read(children.at(-1)), text };
    if (node.name === 'CallExpression') {
      const args = []; for (let child = children[1]?.firstChild; child; child = child.nextSibling)
        if (!['(', ')', ','].includes(child.name)) args.push(read(child));
      return { type: 'call', name: source.slice(children[0].from, children[0].to), args, text };
    }
    return { type: 'opaque', text };
  }
  const result = !invalid && statement?.name === 'ExpressionStatement' && !statement.nextSibling
    ? read(statement.firstChild) : { type: 'opaque', text: source };
  cache.set(source, result); return result;
}

const REPLAY_NAMES = new Set(['_in_replay', 'store._in_replay', 'renpy.store._in_replay']);
export const isReplayVariable = name => REPLAY_NAMES.has(name);

// Ren'Py starts ordinary play with _in_replay = None. Fold only this engine
// fact and literal boolean operations; game variables remain symbolic.
export function normalPlayCondition(source) {
  if (!source || source === 'MENU_FALLTHROUGH') return source;
  const literal = value => ({ type: 'literal', value, text: value === null ? 'None' : value ? 'True' : 'False' });
  function fold(ast, truth = false) {
    if (ast.type === 'name' && isReplayVariable(ast.text)) return literal(null);
    if (ast.type === 'unary' && ast.operator === 'not') {
      const child = fold(ast.child, true);
      return child.type === 'literal' ? literal(!child.value) : { ...ast, text: `not (${child.text})` };
    }
    if (ast.type !== 'binary') return ast;
    const op = ast.operator, boolean = ['and', 'or'].includes(op);
    const left = fold(ast.left, boolean && truth), right = fold(ast.right, boolean && truth);
    if (['and', 'or'].includes(op)) {
      if (left.type === 'literal') return op === 'and' ? left.value ? right : left : left.value ? left : right;
      if (truth && right.type === 'literal') {
        if (op === 'and' && !right.value || op === 'or' && right.value) return right;
        return left;
      }
    }
    if (left.type === 'literal' && right.type === 'literal' && ['==', '!=', 'is', 'is not'].includes(op)) {
      const equal = op === 'is' || op === 'is not' ? left.value === right.value
        : left.value === right.value || typeof left.value !== 'string' && typeof right.value !== 'string'
          && left.value !== null && right.value !== null && Number(left.value) === Number(right.value);
      return literal(op === '!=' || op === 'is not' ? !equal : equal);
    }
    return { ...ast, text: `(${left.text}) ${op} (${right.text})` };
  }
  const ast = expression(source);
  const referencesReplay = ast => ast.type === 'name' ? isReplayVariable(ast.text)
    : [ast.child, ast.left, ast.right, ...(ast.args || [])].filter(Boolean).some(referencesReplay);
  if (!referencesReplay(ast)) return source;
  const result = fold(ast, true);
  return result.type === 'literal' ? result.value ? 'True' : 'False' : result.text;
}

export function negate(value) {
  if (value.op === 'constant') return value.value ? FALSE : TRUE;
  if (value.op === 'not') return value.child;
  return { op: 'not', child: value, key: `not(${value.key})` };
}
function combine(op, inputs) {
  const identity = op === 'all', terms = [];
  for (const item of inputs) {
    if (item.op === 'constant') { if (item.value !== identity) return item; continue; }
    terms.push(...(item.op === op ? item.terms : [item]));
  }
  const unique = [...new Map(terms.map(term => [term.key, term])).values()];
  const keys = new Set(unique.map(term => term.key));
  if (unique.some(term => keys.has(negate(term).key))) return identity ? FALSE : TRUE;
  if (op === 'any') {
    // Combine alternative guards at a merge instead of retaining whichever
    // path arrived first. Complementary dialogue branches reduce to True.
    const groups = unique.map(term => term.op === 'all' ? term.terms : [term]);
    for (let i = 0; i < groups.length; i++) for (let j = 0; j < groups.length; j++) {
      if (i === j) continue;
      const a = groups[i], b = groups[j], bKeys = new Set(b.map(term => term.key));
      const missing = a.filter(term => !bKeys.has(term.key));
      if (!missing.length) return combine('any', unique.filter((_, index) => index !== j));
      if (missing.length === 1 && bKeys.has(negate(missing[0]).key)) {
        const common = a.filter(term => term.key !== missing[0].key);
        const remainder = b.filter(term => !common.some(item => item.key === term.key));
        if (remainder.length === 1) return combine('any', unique.filter((_, index) => index !== i && index !== j).concat(all(common)));
        if (missing[0].op === 'atom') return combine('any', unique.map((term, index) => index === j
          ? all(b.filter(item => item.key !== negate(missing[0]).key)) : term));
      }
    }
  }
  if (!unique.length) return identity ? TRUE : FALSE;
  if (unique.length === 1) return unique[0];
  const signature = `${op}(${unique.map(term => term.key).sort().join(',')})`;
  if (!formulas.has(signature)) formulas.set(signature, { op, terms: unique, key: `${op}@${nextFormulaId++}` });
  return formulas.get(signature);
}
export const all = terms => combine('all', terms);
export const any = terms => combine('any', terms);
export function substitute(value, facts) {
  if (!facts.size) return value;
  const memo = new Map();
  function replace(value) {
    if (memo.has(value.key)) return memo.get(value.key);
    const result = value.flag && facts.has(value.flag) ? facts.get(value.flag) ? TRUE : FALSE
      : value.op === 'not' ? negate(replace(value.child))
      : value.terms ? (value.op === 'all' ? all : any)(value.terms.map(replace)) : value;
    memo.set(value.key, result); return result;
  }
  return replace(value);
}
export function predicate(source, facts = new Map()) {
  if (!source || source === 'True') return TRUE;
  function read(ast) {
    if (ast.type === 'literal' && typeof ast.value === 'boolean') return ast.value ? TRUE : FALSE;
    if (ast.type === 'unary' && ast.operator === 'not') return negate(read(ast.child));
    if (ast.type === 'binary' && ['and', 'or'].includes(ast.operator))
      return (ast.operator === 'and' ? all : any)([read(ast.left), read(ast.right)]);
    if (ast.type === 'binary' && ['==', '!=', 'is', 'is not'].includes(ast.operator)) {
      const [name, literal] = ast.left.type === 'name' ? [ast.left, ast.right] : [ast.right, ast.left];
      if (name.type === 'name' && literal.type === 'literal' && typeof literal.value === 'boolean') {
        const value = read(name), positive = literal.value === ['==', 'is'].includes(ast.operator);
        return positive ? value : negate(value);
      }
    }
    if (ast.type === 'name') {
      if (facts.has(ast.text)) return facts.get(ast.text) ? TRUE : FALSE;
      return { op: 'atom', source: ast.text, flag: ast.text, key: ast.text };
    }
    return { op: 'atom', source: ast.text, key: ast.text.replace(/\s+/g, ' ') };
  }
  return read(expression(source));
}
export function predicateText(value, names = new Map(), raw = false) {
  let budget = 80;
  function format(value, raw) {
    if (--budget < 0) throw new Error('condition-size');
    if (value.op === 'constant') return value.value ? '' : '条件不成立';
    if (value.op === 'atom') return value.flag && !raw ? `已启用 ${names.get(value.flag) || value.flag}` : value.source;
    if (value.op === 'not') return value.child.flag && !raw ? `未启用 ${names.get(value.child.flag) || value.child.flag}`
      : `not (${format(value.child, true)})`;
    return value.terms.map(term => {
      const text = format(term, raw);
      return term.op === (value.op === 'all' ? 'any' : 'all') ? `(${text})` : text;
    }).join(raw ? value.op === 'all' ? ' and ' : ' or ' : value.op === 'all' ? '，且 ' : '，或 ');
  }
  try { return format(value, raw); } catch { return '多组执行条件（展开原始分支查看）'; }
}

export function effectText(effect) { return effect.guard ? `若 ${effect.guard}：${effect.text}` : effect.text; }
export function predicateReads(value) {
  const names = new Set(), visited = new Set();
  function visit(value) {
    if (visited.has(value.key)) return; visited.add(value.key);
    if (value.flag) names.add(value.flag);
    else if (value.source) {
      parser.parse(value.source).iterate({ enter(node) { if (node.name === 'VariableName') names.add(value.source.slice(node.from, node.to)); } });
    }
    if (value.child) visit(value.child); if (value.terms) value.terms.forEach(visit);
  }
  visit(value); return names;
}

// Resolve only complete, standard Ren'Py text-input blocks. Other Python code
// keeps its unknown effect, even when some of its assignments were extracted.
export function inputScriptEffects(source) {
  const tree = parser.parse(source), inputs = new Set(), rows = [];
  let invalid = false; tree.iterate({ enter(node) { if (node.type.isError) invalid = true; } });
  if (invalid) return null;
  const text = node => source.slice(node.from, node.to);
  const children = node => { const result = []; for (let child = node.firstChild; child; child = child.nextSibling) result.push(child); return result; };
  function statements(node, guard = '') {
    for (const statement of children(node)) {
      if ([':', 'Comment'].includes(statement.name)) continue;
      const parts = children(statement);
      if (statement.name === 'IfStatement') {
        if (parts.length !== 3 || parts[0].name !== 'if' || parts[2].name !== 'Body') return false;
        const test = expression(text(parts[1]));
        if (test.type !== 'unary' || test.operator !== 'not' || test.child?.type !== 'name' || !inputs.has(test.child.text)) return false;
        if (!statements(parts[2], text(parts[1]))) return false;
        continue;
      }
      if (statement.name !== 'AssignStatement' || parts.length !== 3 || text(parts[1]) !== '=') return false;
      const target = text(parts[0]), value = text(parts[2]), ast = expression(value);
      const member = children(parts[0]), persistent = parts[0].name === 'MemberExpression'
        && member.length === 3 && member[0].name === 'VariableName' && text(member[0]) === 'persistent';
      if (persistent) {
        if (ast.type !== 'name' || !inputs.has(ast.text)) return false;
      } else if (parts[0].name !== 'VariableName') return false;
      else if (ast.type === 'call' && ast.name === 'renpy.input' && !guard
        && ast.args.length === 1 && ast.args[0]?.type === 'literal' && typeof ast.args[0].value === 'string') inputs.add(target);
      else if (ast.type === 'call' && ast.name === `${target}.strip` && inputs.has(target) && !guard && !ast.args.length) {}
      else if (!(inputs.has(target) && guard && ast.type === 'literal' && typeof ast.value === 'string')) return false;
      rows.push({ target, expression: value, operator: 'set', guard, text: `${target} = ${value}`, precision: 'exact',
        persistentCopy: persistent, source });
    }
    return true;
  }
  return statements(tree.topNode) && inputs.size ? rows : null;
}

export function mergeInputScript(effects, recovered, consumed) {
  const valueKey = ast => ast.type === 'literal' ? ['literal', ast.value]
    : ast.type === 'name' ? ['name', ast.text] : ast.type === 'call' ? ['call', ast.name, ast.args.map(valueKey)] : [ast.type, ast.text];
  const match = (a, b) => a.target === b.target && a.operator === b.operator && predicate(a.guard).key === predicate(b.guard).key
    && JSON.stringify(valueKey(expression(a.expression))) === JSON.stringify(valueKey(expression(b.expression)));
  const insertions = [], matched = []; let cursor = 0;
  for (const row of recovered) {
    if (row.persistentCopy) { if (!matched.length) return false; insertions.push({ at: cursor, row }); continue; }
    const index = effects.findIndex((effect, index) => index >= cursor && !consumed.has(effect) && match(effect, row));
    if (index < 0) return false;
    matched.push(effects[index]); cursor = index + 1;
  }
  for (const row of matched) consumed.add(row);
  for (const { at, row } of insertions.reverse()) effects.splice(at, 0, row);
  return true;
}
export function variableNames(graph) {
  const labels = new Map(), normalize = text => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  for (const block of graph.blocks.values()) {
    const texts = [...block.contents.map(item => item.text), ...(graph.rawOut.get(block.id) || []).map(edge => edge.choice)];
    for (const text of texts) for (const match of readable(text).matchAll(/\[([^\]\n]{2,48})\]|\(([^()\n]{2,48})\)/g)) {
      const label = match[1] || match[2]; labels.set(normalize(label), label);
    }
  }
  const names = new Map();
  for (const block of graph.blocks.values()) for (const effect of block.effects) {
    if (!effect.target) continue;
    const suffix = effect.target.split('_').slice(1).join('_');
    const label = labels.get(normalize(effect.target)) || (suffix && labels.get(normalize(suffix)));
    if (label) names.set(effect.target, label);
  }
  return names;
}

export function describeEffects(effects, names = new Map()) {
  const rows = [];
  for (let i = 0; i < effects.length; i++) {
    const effect = effects[i], ast = expression(effect.expression), label = names.get(effect.target) || effect.target;
    const row = { kind: 'effect', text: effect.text, guard: effect.guard, sources: [effect] };
    if (effect.unknown) { row.kind = 'unknown'; row.text = '还有未解析的变量影响'; }
    else if (ast.type === 'call' && ast.name === 'renpy.input' && ast.args[0]?.type === 'literal') {
      row.kind = 'input'; row.text = '输入文本'; row.prompt = readable(ast.args[0].value);
      const strip = effects[i + 1], fallback = effects[i + 2];
      const trimmed = strip?.target === effect.target && predicate(strip.guard).key === predicate(effect.guard).key
        && strip.expression === `${effect.target}.strip()`;
      if (trimmed) { row.sources.push(strip); i++; }
      if (trimmed && fallback?.target === effect.target && predicate(fallback.guard).key === all([predicate(effect.guard), negate(predicate(effect.target))]).key
        && expression(fallback.expression).type === 'literal') {
        row.default = String(expression(fallback.expression).value); row.sources.push(fallback); i++;
      }
      const saved = effects[i + 1];
      if (saved?.persistentCopy && saved.expression === effect.target && predicate(saved.guard).key === predicate(effect.guard).key) {
        row.sources.push(saved); i++;
      }
    } else if (ast.type === 'literal' && typeof ast.value === 'boolean' && effect.operator === 'set') {
      row.text = `${ast.value ? '启用' : '关闭'} ${label}`;
    } else if (['add', 'sub'].includes(effect.operator) && ast.type === 'literal' && typeof ast.value === 'number') {
      row.text = `${label} ${effect.operator === 'add' ? '增加' : '减少'} ${ast.value}`;
      const next = effects[i + 1], limit = next && expression(next.expression);
      if (next?.target === effect.target && next.guard === effect.guard && limit.type === 'call' && limit.name === 'clamp'
        && limit.args[0]?.text === effect.target && limit.args.slice(1).length === 2 && limit.args.slice(1).every(arg => arg.type === 'literal' && typeof arg.value === 'number')) {
        row.text += `（范围 ${limit.args[1].value}～${limit.args[2].value}）`; row.sources.push(next); i++;
      }
    } else if (ast.type === 'literal' && effect.operator === 'set') row.text = `${label} 设为 ${ast.value}`;
    if (row.guard) row.condition = predicateText(effect.guardPredicate || predicate(row.guard), names);
    rows.push(row);
  }
  return rows;
}
