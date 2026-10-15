// The cards (3.23 T13, card.ts): the stored text, the diff cut, the finish kinds, the note route, the style, the palette.
import { describe, expect, test } from 'claude-code/testing'

import {
  type BandAgent, bandCells, bandHex, bandRole, bandRows, bandStepOf, bandView, acceptedCard, claudeCard, claudeRows, elapsedText, borderOf, cardStyleOf, cardText, loopNoteOf, coderIdOf, FILES_MAX, finishCardOf, firstLine, noteCardOf, numstatOf, parseCard, ROLE_HEX, type Sender,
} from './card'
import { parseCoderReturn, parseExpertContract } from './contract'
import { classify, isNote, noteBody } from './route'

// The role colors (3.23 T13); pa-watch's copy (stream.ts ROLE_COLOR) retired with the `colors` option in T13d.c3.
const PALETTE = { claude: '#d7875f', planning: '#af5fff', expert: '#0087ff', coder: '#00af00', thinker: '#00afaf' } as const

const hb = (from: string, report: string): string =>
  `<agent-message from="${from}">\n[Subagent hand-back] The text below is the final report … The report follows:\n`
  + `${report.split('\n').map(l => `  ${l}`).join('\n')}\n  \n</agent-message>`
const EXPERT: Sender = { role: 'expert', task: 'T3', coder: '' }
const CODER: Sender = { role: 'coder', task: 'T3', coder: 'c2' }

describe('card text', () => {
  test('a bold title led by the square, the body, the diff in a fence; clean markdown, parsed back', async () => {
    const t = cardText('coder', 'T3 · c2', 'Fixed the parser.', { done: true, diff: '@@ -1 +1 @@\n-a\n+b\n' })
    expect(t).toBe('**🟩 ✓ Coder · T3 · c2**\n\nFixed the parser.\n\n```diff\n@@ -1 +1 @@\n-a\n+b\n```')
    expect(t).not.toMatch(/<|‹/)
    expect(parseCard(t)).toEqual({ role: 'coder', done: true, title: '✓ Coder · T3 · c2', body: 'Fixed the parser.', diff: '@@ -1 +1 @@\n-a\n+b' })
    expect(parseCard(cardText('expert', 'T1', 'My approach.'))).toEqual({ role: 'expert', done: false, title: 'Expert · T1', body: 'My approach.', diff: '' })
    expect(parseCard('Just a reply.')).toBe(null)
  })

  test('the first meaningful line skips blanks, fences, headings marks and KEY: lines', async () => {
    expect(firstLine('\n```\nSTATUS: done\n## Did the thing.\nmore')).toBe('Did the thing.')
    expect(coderIdOf('LOG: phase-ends/current/logs/T13a.c2.md')).toBe('c2')
  })
})

describe('finish kinds', () => {
  test('expert: its SUMMARY and the done mark, no status or commit; without SUMMARY its first meaningful line', async () => {
    const r = 'STATUS: done\nLOG: logs/T3.md\nCOMMIT: 1a2b3c4\nSUMMARY: The parser now reads quotes.'
    expect(parseExpertContract(r)?.summary).toBe('The parser now reads quotes.')
    expect(finishCardOf(EXPERT, hb('E1', r))).toBe('**🟦 ✓ Expert · T3**\n\nThe parser now reads quotes.')
    expect(finishCardOf(EXPERT, hb('E1', 'Parser fixed.\nSTATUS: done\nCOMMIT: none'))).toBe('**🟦 ✓ Expert · T3**\n\nParser fixed.')
  })

  test('expert: the done mark (and the finished border) only for STATUS done (3.23 T13d.c3)', async () => {
    const card = finishCardOf(EXPERT, hb('E1', 'STATUS: blocked\nLOG: logs/T3.md\nSUMMARY: The build fails on Windows.'))
    expect(card).toBe('**🟦 Expert · T3**\n\nThe build fails on Windows.')
    expect(parseCard(card ?? '')?.done).toBe(false)
    expect(parseCard(finishCardOf(EXPERT, hb('E1', 'STATUS: done\nSUMMARY: Done.')) ?? '')?.done).toBe(true)
  })

  test('3.24 T8b: coder: a finish card with its commit and the commit\'s files with +/- counts', async () => {
    const r = 'STATUS: done\nCHANGED: a.ts:1\nCOMMIT: 9f8e7d6c5b\nDEVIATIONS: none\nSUMMARY: One line changed.'
    expect(parseCoderReturn(r)).toEqual({ status: 'done', summary: 'One line changed.', commit: '9f8e7d6c5b' })
    const files = numstatOf('3\t1\tsrc/calc.py\r\n12\t0\ttests/test_calc.py\n-\t-\tlogo.png\n\n')
    expect(files).toEqual([{ path: 'src/calc.py', add: 3, del: 1 }, { path: 'tests/test_calc.py', add: 12, del: 0 }, { path: 'logo.png', add: null, del: null }])
    const card = finishCardOf(CODER, hb('C1', r), files)
    expect(card).toBe('**🟩 ✓ Coder · T3 · c2, committed 9f8e7d6**\n\nOne line changed.\n\n- `src/calc.py` +3 −1\n- `tests/test_calc.py` +12 −0\n- `logo.png` (binary)')
    expect(parseCard(card ?? '')).toEqual(expect.objectContaining({ role: 'coder', done: true, title: '✓ Coder · T3 · c2, committed 9f8e7d6' }))
    expect(borderOf('mixed', true)).toBe('round')
    // 3.24 T12c: a note after the hash (a live coder's COMMIT line) still gives the commit
    const noted = r.replace('COMMIT: 9f8e7d6c5b', 'COMMIT: 9f8e7d6 (rename, on top of e45aa75; no amend)')
    expect(parseCoderReturn(noted)?.commit).toBe('9f8e7d6')
    expect(finishCardOf(CODER, hb('C1', noted), files)).toBe(card)
    expect(parseCoderReturn('STATUS: done\nCOMMIT: none (nothing to commit)')?.commit).toBe('none (nothing to commit)')
    // no commit, no files; blocked: no done mark; a long list is cut
    expect(finishCardOf(CODER, hb('C1', 'STATUS: blocked\nCOMMIT: none\nBLOCKER: x'))).toBe('**🟩 Coder · T3 · c2**')
    const many = Array.from({ length: FILES_MAX + 3 }, (_, i) => ({ path: `f${i}`, add: 1, del: 0 }))
    expect(finishCardOf(CODER, hb('C1', r), many)?.endsWith(`\n- \`f${FILES_MAX - 1}\` +1 −0\n- … +3 files`)).toBe(true)
  })

  test('thinker: one line; the rest stay the stub', async () => {
    expect(finishCardOf({ role: 'thinker', task: '', coder: '' }, hb('K', 'The answer.'))).toBe('**🟦 ✓ Thinker**\n\nAnswered; Claude relays it.')
    expect(finishCardOf({ role: 'other', task: 'T3', coder: '' }, hb('R', 'STATUS: done'))).toBe(null)
    expect(finishCardOf(EXPERT, hb('E1', '.'))).toBe(null)
    expect(finishCardOf(EXPERT, hb('E1', 'WAITING: coder T3.c1'))).toBe(null)
    expect(finishCardOf(EXPERT, hb('E1', 'no contract here'))).toBe(null)
  })

  test('3.25 T3: a plain done gets its finish card, the summary from the free text', async () => {
    expect(finishCardOf(EXPERT, hb('E1', 'Done. The parser reads quotes.'))).toBe('**🟦 ✓ Expert · T3**\n\nThe parser reads quotes.')
    expect(finishCardOf(CODER, hb('C1', '**Done** — one line changed, commit 9f8e7d6.'), [{ path: 'a.ts', add: 1, del: 0 }]))
      .toBe('**🟩 ✓ Coder · T3 · c2, committed 9f8e7d6**\n\none line changed, commit 9f8e7d6.\n\n- `a.ts` +1 −0')
  })
})

describe('notes (3.23 T13)', () => {
  test('a SendMessage to main is a note, not a hand-back; its card names the sender', async () => {
    const note = '<agent-message from="E1">\nT3 approach: quote-aware split, then tests.\n</agent-message>'
    expect(isNote(note)).toBe(true)
    expect(isNote(hb('E1', 'STATUS: done'))).toBe(false)
    expect(classify({ kind: 'peer' }, note, false)).toBe('note')
    expect(noteBody(note)).toBe('T3 approach: quote-aware split, then tests.')
    expect(noteCardOf(EXPERT, noteBody(note))).toBe('**🟦 Expert · T3**\n\nT3 approach: quote-aware split, then tests.')
    expect(noteCardOf(CODER, 'Plan.')).toBe('**🟩 Coder · T3 · c2**\n\nPlan.')
    expect(noteCardOf({ role: 'coder', task: 'T3', coder: '' }, 'Plan.')).toBe('**🟩 Coder · T3**\n\nPlan.')
    expect(noteCardOf({ role: 'other', task: 'T3', coder: '' }, 'Plan.')).toBe(null)
    expect(noteCardOf({ role: 'thinker', task: '', coder: '' }, 'Plan.')).toBe(null)
  })
})

describe('style and palette', () => {
  test('card_style: mixed by default; the borders per style', async () => {
    expect(cardStyleOf({})).toBe('mixed')
    expect(cardStyleOf({ card_style: 'nope' })).toBe('mixed')
    expect(cardStyleOf({ card_style: 'plain' })).toBe('plain')
    expect([borderOf('mixed', false), borderOf('mixed', true)]).toEqual(['quote', 'round'])
    expect([borderOf('boxed', false), borderOf('boxed', true)]).toEqual(['round', 'round'])
    expect([borderOf('bar', false), borderOf('bar', true)]).toEqual(['quote', 'quote'])
    expect(borderOf('plain', true)).toBe(null)
  })

  test('the role colors are the PALETTE', async () => {
    for (const role of ['claude', 'planning', 'expert', 'coder', 'thinker'] as const) {
      expect(ROLE_HEX[role]).toBe(PALETTE[role])
    }
  })
})

describe('the band (3.23 T13)', () => {
  const ag = (p: Partial<BandAgent> & { id: string }): BandAgent =>
    ({ type: 'expert-opus55', status: 'running', description: '', since: 0, ...p })

  test("rows: the loop expert, then its live coders by first seen; the coder's c<k>, the last step, the elapsed time", () => {
    const agents = [
      ag({ id: 'E1', description: 'T3 split quotes', since: 1_000, step: 'Agent coder T3.c1' }),
      ag({ id: 'C2', type: 'coder-opus55', parentId: 'E1', description: 'T3.c2 tests', since: 50_000, step: 'Bash  run.sh  test' }),
      ag({ id: 'C1', type: 'coder-opus55', parentId: 'E1', description: 'T3.c1 the parser', since: 20_000, step: 'Edit route.ts' }),
      ag({ id: 'C0', type: 'coder-opus55', parentId: 'E1', status: 'completed', description: 'T3.c0', since: 5_000 }),
      ag({ id: 'P', type: 'pa-claude', description: 'partner', since: 0 }),
      ag({ id: 'R1', type: 'retriever-code', parentId: 'C1', description: 'where is classify', since: 60_000 }),
    ]
    expect(bandRows(agents, [{ id: 'E1', task: 'T3' }], 3_721_000)).toEqual([
      { role: 'expert', name: 'expert-opus55', task: 'T3', step: 'Agent coder T3.c1', elapsed: '1h02m' },
      { role: 'coder', name: 'coder-opus55', task: 'T3 · c1', step: 'Edit route.ts', elapsed: '1h01m' },
      { role: 'coder', name: 'coder-opus55', task: 'T3 · c2', step: 'Bash run.sh test', elapsed: '1h01m' },
      { role: 'retriever', name: 'retriever-code', task: 'T3', step: '', elapsed: '1h01m' },
    ])
    expect(bandRows(agents, [], 0)).toEqual([])
    expect(elapsedText(42_500)).toBe('42s')
    expect(bandHex('coder')).toBe('#00af00')
    expect(bandHex('retriever')).toBeUndefined()
  })

  test('cells fit the columns: the dim cell gives way first, then the task, then the name', () => {
    const r = { role: 'coder', name: 'coder-opus55', task: 'T3 · c1', step: 'Edit route.ts', elapsed: '4m' }
    expect(bandCells(r, 80)).toEqual({ name: 'coder-opus55', task: ' T3 · c1', dim: '  Edit route.ts · 4m' })
    const c = bandCells(r, 24)
    expect(c.name + c.task + c.dim).toHaveLength(24)
    expect(c.dim).toBe('  E…')
    expect(bandCells(r, 10)).toEqual({ name: 'coder-opu…', task: '', dim: '' })
  })
})

describe('the band\'s working line (3.23 T13d.c2)', () => {
  test('the facts\' line, else the last step, else none', async () => {
    expect(bandStepOf({ line: 'Adding the operator.', steps: ['Edit calc.py'] })).toBe('Adding the operator.')
    expect(bandStepOf({ line: '', steps: ['Read plan', 'Edit calc.py'] })).toBe('Edit calc.py')
    expect(bandStepOf({ steps: [] })).toBeUndefined()
    expect(bandStepOf(null)).toBeUndefined()
  })
})

describe('3.24 T13B: Claude\'s reply card, the new roles, the router\'s band part', () => {
  test('every Claude reply is an orange Claude card; Curator and Auditor titles parse', async () => {
    expect(claudeCard('  Done.\n')).toBe('**🟧 Claude**\n\nDone.')
    expect(parseCard(claudeCard('Done.'))).toEqual({ role: 'claude', done: false, title: 'Claude', body: 'Done.', diff: '' })
    expect(parseCard(cardText('curator', '', 'x'))?.role).toBe('curator')
    expect(parseCard(cardText('auditor', '', 'x'))?.role).toBe('auditor')
    expect(bandRole('memory-curator')).toBe('curator')
    expect(bandRole('pa-planner')).toBe('planner') // 3.24 T17b
    expect(['pa-expert', 'pa-coder', 'pa-thinker', 'deep-thinker'].map(bandRole)).toEqual(['expert', 'coder', 'thinker', 'thinker']) // 3.25 T6b
    expect(bandHex('curator')).toBe('#d75faf')
    expect(bandHex('auditor')).toBe('#5f87af')
  })

  test('the band view: lines and rows merged; `‹pa› working` and its dots only when nothing else shows', async () => {
    const row = { role: 'claude', name: 'Claude', task: '', step: 'Read x', elapsed: '3s' }
    const idle = { rows: [], note: '', working: false, dots: 1 }
    expect(bandView({ line: '', rows: [] }, idle)).toEqual({ lines: [], rows: [] })
    expect(bandView({ line: '', rows: [] }, { ...idle, working: true, dots: 3 })).toEqual({ lines: ['‹pa› working...'], rows: [] })
    expect(bandView({ line: 'Phase 1 · T1', rows: [] }, { ...idle, working: true, note: 'conversation only: the loop runs in abcdefgh' }).lines)
      .toEqual(['Phase 1 · T1', 'conversation only: the loop runs in abcdefgh'])
    expect(bandView({ line: '', rows: [] }, { ...idle, working: true, rows: [row] })).toEqual({ lines: [], rows: [row] })
  })

  test('Claude\'s rows: the partner as Claude and its live children; the note', async () => {
    const agents: BandAgent[] = [
      { id: 'P1', type: 'pa-claude', status: 'running', description: 'Claude', since: 0, step: 'Write plan' },
      { id: 'K1', type: 'thinker', status: 'running', parentId: 'P1', description: 'q', since: 1_000 },
      { id: 'X', type: 'expert-opus55', status: 'running', description: 'T1', since: 0 },
    ]
    expect(claudeRows(agents, 'P1', 5_000).map(r => [r.role, r.name, r.step, r.elapsed])).toEqual([['claude', 'Claude', 'Write plan', '5s'], ['thinker', 'thinker', '', '4s']])
    expect(claudeRows(agents, '', 5_000)).toEqual([])
    expect(claudeRows([{ ...agents[0] as BandAgent, status: 'idle' }], 'P1', 5_000)).toEqual([])
    expect(loopNoteOf(false, 'abcdefgh-1234')).toBe('conversation only: the loop runs in abcdefgh')
    expect(loopNoteOf(true, 'abcdefgh-1234')).toBe('')
    expect(loopNoteOf(false, '')).toBe('')
  })
})

describe('3.24 T17g: Claude\'s model and effort, the accepted card\'s summary, a late report', () => {
  test('a Claude card titles the reply\'s model and effort when given', async () => {
    expect(claudeCard('Done.', 'Opus 5.5 · medium')).toBe('**🟧 Claude · Opus 5.5 · medium**\n\nDone.')
    expect(parseCard(claudeCard('Done.', 'Opus 5.5 · max'))?.title).toBe('Claude · Opus 5.5 · max')
  })

  test('the accepted card carries the task\'s SUMMARY, as a done card does', async () => {
    const c = 'STATUS: review\nSUMMARY: I ran all 55 cases through real pipes.\nLOG: tasks/T2.md\nCOMMIT: 34c2a41'
    expect(acceptedCard('T2', c)).toBe('**🟦 ✓ Expert · T2 · accepted**\n\nI ran all 55 cases through real pipes.')
    expect(acceptedCard('T2', '')).toBe('**🟦 ✓ Expert · T2 · accepted**')
  })

  test('a late report stands in for a notification that only points at a spent hand-back', async () => {
    const tn = '<task-notification>\n<task-id>E1</task-id>\n<status>completed</status>\n<result>This agent\'s report was delivered to you as a message from "E1" (its SubagentHandback call). Read it there; it is not repeated here.\n</result>\n</task-notification>'
    expect(finishCardOf(EXPERT, tn)).toBe(null)
    expect(finishCardOf(EXPERT, tn, [], 'STATUS: done\nSUMMARY: The tests pass.\nCOMMIT: 5a7f0f7')).toBe('**🟦 ✓ Expert · T3**\n\nThe tests pass.')
  })
})
