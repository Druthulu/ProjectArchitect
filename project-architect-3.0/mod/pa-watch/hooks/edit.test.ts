// 3.24 T8b: the edit card's diff builder (edit.ts): hunks with 3 lines of context, `...`-separated hunks, the 30-row
// cap, a new file, MultiEdit, CRLF content, and which calls count (success only).
import { describe, expect, test } from 'claude-code/testing'

import { diffRows, editHeader, editOf, editsShown, editView, hunksOf, linesOf, patchView, shownPath } from './edit'

const nums = (from: number, to: number): string[] => Array.from({ length: to - from + 1 }, (_, i) => `line ${from + i}`)
const text = (ls: readonly string[]): string => `${ls.join('\n')}\n`

describe('the diff builder', () => {
  test('one changed line: one hunk, 3 lines of context each side, numbered by its header', async () => {
    const a = nums(1, 20)
    const b = a.map(l => (l === 'line 10' ? 'line ten' : l))
    const v = editView('a.ts', text(a), text(b))
    expect(v).toEqual({
      path: 'a.ts', created: false, add: 1, del: 1, more: 0,
      hunks: ['@@ -7,7 +7,7 @@\n line 7\n line 8\n line 9\n-line 10\n+line ten\n line 11\n line 12\n line 13'],
    })
    expect(editHeader(v)).toBe('Updated a.ts (+1 -1)')
  })

  test('changes far apart are two hunks; close ones merge', async () => {
    const a = nums(1, 40)
    const far = a.map(l => (l === 'line 5' || l === 'line 30' ? `${l}!` : l))
    expect(editView('a', text(a), text(far)).hunks.map(h => h.split('\n')[0])).toEqual(['@@ -2,7 +2,7 @@', '@@ -27,7 +27,7 @@'])
    const near = a.map(l => (l === 'line 5' || l === 'line 11' ? `${l}!` : l))
    expect(editView('a', text(a), text(near)).hunks.map(h => h.split('\n')[0])).toEqual(['@@ -2,13 +2,13 @@'])
  })

  test('removals come before additions; an insertion keeps its numbers', async () => {
    const rows = diffRows(['a', 'b', 'c'], ['a', 'x', 'y', 'c'])
    expect(rows.map(r => `${r.sign}${r.text}`)).toEqual([' a', '-b', '+x', '+y', ' c'])
    expect(editView('f', 'a\nb\n', 'a\nnew\nb\n').hunks).toEqual(['@@ -1,2 +1,3 @@\n a\n+new\n b'])
  })

  test('the cap: 30 rows in all, a cut hunk re-headed, the rest counted', async () => {
    const v = editView('big.txt', text(nums(1, 10)), text(nums(1, 50).map(l => `${l} new`)))
    expect(v.add).toBe(50)
    expect(v.del).toBe(10)
    expect(v.hunks.length).toBe(1)
    const rows = (v.hunks[0] ?? '').split('\n')
    expect(rows.length).toBe(31) // header + 30 rows
    expect(rows[0]).toBe('@@ -1,10 +1,20 @@')
    expect(v.more).toBe(30)
  })

  test('a new file: its first 30 lines, the line count, the rest counted', async () => {
    const v = editView('src\\new.py', null, text(nums(1, 42)))
    expect(editHeader(v)).toBe('Created src\\new.py (42 lines)')
    expect(v.hunks).toEqual([nums(1, 30).join('\n')])
    expect(v.more).toBe(12)
    expect(editHeader(editView('one', null, 'x'))).toBe('Created one (1 line)')
  })

  test('CRLF content diffs by line, and control characters are spaced', async () => {
    expect(linesOf('a\r\nb\r\n')).toEqual(['a', 'b'])
    expect(linesOf('a\x1bb')).toEqual(['a b'])
    const v = editView('w.txt', 'one\r\ntwo\r\nthree\r\n', 'one\r\n2\r\nthree\r\n')
    expect(v.hunks).toEqual(['@@ -1,3 +1,3 @@\n one\n-two\n+2\n three'])
  })

  test('hunksOf: none when nothing changed', async () => {
    expect(hunksOf(diffRows(['a'], ['a']))).toEqual([])
  })

  test('the tool\'s structured patch, when the old text is missing', async () => {
    const v = patchView('p', [{ oldStart: 4, newStart: 4, lines: [' a', '-b', '+c', ' d'] }])
    expect(v).toEqual({ path: 'p', created: false, add: 1, del: 1, more: 0, hunks: ['@@ -4,3 +4,3 @@\n a\n-b\n+c\n d'] })
  })

  test('shownPath: project-relative with its own separators; outside the root, whole', async () => {
    expect(shownPath('Z:\\r\\src\\a.ts', 'Z:/r')).toBe('src\\a.ts')
    expect(shownPath('z:/R/src/a.ts', 'Z:\\r\\')).toBe('src/a.ts')
    expect(shownPath('C:/other/a.ts', 'Z:/r')).toBe('C:/other/a.ts')
  })
})

describe('which calls draw', () => {
  const ORIG = 'def f():\n    return 1\n'
  const ok = (result: Record<string, unknown>): Record<string, unknown> => ({ result, ref: 1, text: 'ok' })

  test('who: Claude, experts and coders; not retrievers, planners or unknown agents', async () => {
    expect(['pa-claude', 'expert-opus55', 'expert-fable-5m', 'coder-opus55'].map(t => editsShown(t, false))).toEqual([true, true, true, true])
    expect(['pa-expert', 'pa-coder', 'pa-thinker'].map(t => editsShown(t, false))).toEqual([true, true, false]) // 3.25 T6b
    expect(['retriever-code', 'planner-phase', 'thinker', 'memory-curator', ''].map(t => editsShown(t, false))).toEqual([false, false, false, false, false])
    expect(editsShown('', true)).toBe(true)
    expect(editsShown('pa-planner', false)).toBe(true) // 3.24 T17b: its draft's labelled line
  })

  test('an Edit applies to the original file (CRLF too); a replace_all every occurrence', async () => {
    const v = editOf('Edit', { file_path: '/r/a.py', old_string: 'return 1', new_string: 'return 2' },
      ok({ filePath: '/r/a.py', originalFile: ORIG.replace(/\n/g, '\r\n'), structuredPatch: [] }), '/r')
    expect(v).toEqual({ path: 'a.py', created: false, add: 1, del: 1, more: 0, hunks: ['@@ -1,2 +1,2 @@\n def f():\n-    return 1\n+    return 2'] })
    const all = editOf('Edit', { file_path: '/r/a', old_string: 'x', new_string: 'y', replace_all: true }, ok({ filePath: '/r/a', originalFile: 'x\nx\n' }), '/r')
    expect(all?.add).toBe(2)
  })

  test('3.24 T13C: a plan draft is one labelled line, no hunks; other created files unchanged', async () => {
    const body = text(nums(1, 58))
    const v = editOf('Write', { file_path: 'Z:\\r\\.run\\PHASE_PLAN.draft.md', content: body }, ok({ type: 'create', filePath: 'Z:\\r\\.run\\PHASE_PLAN.draft.md', content: body }), 'Z:/r')
    expect(v).toEqual({ path: '.run\\PHASE_PLAN.draft.md', created: true, add: 58, del: 0, hunks: [], more: 0, label: 'Plan drafted' })
    expect(editHeader(v!)).toBe('Plan drafted (58 lines)')
    const g = editOf('Write', { file_path: '/r/.run/GENERATION_PLAN.draft.md', content: 'a\nc\n' }, ok({ type: 'update', filePath: '/r/.run/GENERATION_PLAN.draft.md', content: 'a\nc\n', originalFile: 'a\nb\n' }), '/r')
    expect([editHeader(g!), g?.hunks]).toEqual(['Plan draft updated (+1 -1)', []])
    const other = editOf('Write', { file_path: '/r/.run/notes.md', content: body }, ok({ type: 'create', filePath: '/r/.run/notes.md', content: body }), '/r')
    expect([editHeader(other!), other?.label, other?.more]).toEqual(['Created .run/notes.md (58 lines)', undefined, 28])
  })

  test('a MultiEdit applies its edits in order', async () => {
    const v = editOf('MultiEdit', { file_path: '/r/m', edits: [{ old_string: 'a', new_string: 'b' }, { old_string: 'b\nc', new_string: 'B\nC' }] },
      ok({ filePath: '/r/m', originalFile: 'a\nc\n' }), '/r')
    expect(v?.hunks).toEqual(['@@ -1,2 +1,2 @@\n-a\n-c\n+B\n+C'])
  })

  test('a Write: create shows the content, update diffs it', async () => {
    expect(editOf('Write', { file_path: '/r/n.md', content: 'a\nb\n' }, ok({ type: 'create', filePath: '/r/n.md', content: 'a\nb\n', originalFile: null }), '/r'))
      .toEqual({ path: 'n.md', created: true, add: 2, del: 0, hunks: ['a\nb'], more: 0 })
    expect(editOf('Write', { file_path: '/r/n.md', content: 'a\nc\n' }, ok({ type: 'update', filePath: '/r/n.md', content: 'a\nc\n', originalFile: 'a\nb\n' }), '/r')?.hunks)
      .toEqual(['@@ -1,2 +1,2 @@\n a\n-b\n+c'])
  })

  test('a NotebookEdit diffs the cell', async () => {
    const v = editOf('NotebookEdit', { notebook_path: '/r/n.ipynb', new_source: 'x = 2' },
      ok({ notebook_path: '/r/n.ipynb', old_source: 'x = 1', new_source: 'x = 2', cell_type: 'code', language: 'python', edit_mode: 'replace' }), '/r')
    expect(v?.path).toBe('n.ipynb')
    expect(v?.hunks).toEqual(['@@ -1,1 +1,1 @@\n-x = 1\n+x = 2'])
  })

  test('success only: a deny, an error, a staged edit, a failed notebook edit and other tools draw nothing', async () => {
    const input = { file_path: '/r/a.py', old_string: 'return 1', new_string: 'return 2' }
    expect(editOf('Edit', input, { deny: 'no' }, '/r')).toBe(null)
    expect(editOf('Edit', input, { isError: true, text: 'String not found', result: 'String not found' }, '/r')).toBe(null)
    expect(editOf('Edit', input, ok({ filePath: '/r/a.py', originalFile: ORIG, staged: true }), '/r')).toBe(null)
    expect(editOf('NotebookEdit', { notebook_path: '/r/n.ipynb' }, ok({ notebook_path: '/r/n.ipynb', error: 'bad cell', new_source: '' }), '/r')).toBe(null)
    expect(editOf('Bash', { command: 'sed -i s/1/2/ a.py' }, ok({ stdout: '' }), '/r')).toBe(null)
    expect(editOf('Read', { file_path: '/r/a.py' }, ok({ file: {} }), '/r')).toBe(null)
  })
})
