import { describe, expect, test } from 'claude-code/testing'

import { entriesOf, entryLinesOf, entryPath, filedThroughOf, parseEntry, parseEntryPath, renderEntry, TOPICS_PATH } from './journal'

describe('journal naming (vectors shared with pa-watch)', () => {
  test('the pinned vectors', async () => {
    expect(entryPath('2026-10-07', '143005', 7, 'r', 'dev', 'msg')).toBe('.claude-state/journal/2026-10-07/143005-007r-dev-msg.md')
    expect(entryPath('2026-10-07', '143005', 8, 'w', 'partner', 'thinking')).toBe(
      '.claude-state/journal/2026-10-07/143005-008w-partner-thinking.md',
    )
  })

  test('T<n> and every listed who and kind are accepted', async () => {
    expect(entryPath('2026-10-07', '000000', 0, 'r', 'T12', 'start')).toBe('.claude-state/journal/2026-10-07/000000-000r-T12-start.md')
    for (const who of ['dev', 'claude', 'partner', 'thinker', 'coder', 'critic', 'mod']) {
      expect(entryPath('2026-10-07', '000000', 999, 'w', who, 'end')).toContain(`999w-${who}-end.md`)
    }
    for (const kind of ['msg', 'answer', 'thinking', 'start', 'end', 'prompt', 'hold', 'release', 'plan', 'decision', 'note', 'steer', 'interim', 'recover', 'repeat', 'context', 'close']) {
      expect(entryPath('2026-10-07', '000000', 1, 'r', 'mod', kind)).toContain(`-mod-${kind}.md`)
    }
  })

  test('unknown values are rejected', async () => {
    expect(() => entryPath('2026-10-07', '143005', 7, 'x', 'dev', 'msg')).toThrow()
    expect(() => entryPath('2026-10-07', '143005', 7, 'r', 'expert', 'msg')).toThrow()
    expect(() => entryPath('2026-10-07', '143005', 7, 'r', 'T', 'msg')).toThrow()
    expect(() => entryPath('2026-10-07', '143005', 7, 'r', 'dev', 'memo')).toThrow()
    expect(() => entryPath('2026-10-07', '143005', 1000, 'r', 'dev', 'msg')).toThrow()
    expect(() => entryPath('2026-10-07', '143005', -1, 'r', 'dev', 'msg')).toThrow()
    expect(() => entryPath('2026-10-7', '143005', 7, 'r', 'dev', 'msg')).toThrow()
    expect(() => entryPath('2026-10-07', '14305', 7, 'r', 'dev', 'msg')).toThrow()
  })

  test('parseEntryPath inverts entryPath', async () => {
    expect(parseEntryPath('.claude-state/journal/2026-10-07/143005-008w-partner-thinking.md')).toEqual({
      date: '2026-10-07',
      time: '143005',
      seq: 8,
      writer: 'w',
      who: 'partner',
      kind: 'thinking',
    })
    // 3.32.11 T1: any who and kind of the name's shape, as journal_rollup.py (was: null for an expert's entry)
    expect(parseEntryPath('x/2026-10-07/032608-330w-planner-spawn.md')).toEqual({
      date: '2026-10-07', time: '032608', seq: 330, writer: 'w', who: 'planner', kind: 'spawn',
    })
    expect(parseEntryPath('x/2026-10-07/143005-008w-Expert-msg.md')).toBe(null)
    expect(parseEntryPath('.claude-state/journal/INDEX-2026-10.md')).toBe(null)
  })

  test('3.23 T7: the rolled form <date>.md#<stem> parses; mixed forms do not', async () => {
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md#143005-008w-partner-thinking')).toEqual({
      date: '2026-10-06', time: '143005', seq: 8, writer: 'w', who: 'partner', kind: 'thinking',
    })
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md#143005-008w-partner-thinking.md')).toBe(null)
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md')).toBe(null)
  })

  test('3.32.9: an external coder\'s entry (x) and a sequence past 999 parse; the mod still writes only r and w', async () => {
    expect(parseEntryPath('.claude-state/journal/2026-10-22/093845-002x-coder-thinking.md')).toEqual({
      date: '2026-10-22', time: '093845', seq: 2, writer: 'x', who: 'coder', kind: 'thinking',
    })
    expect(parseEntryPath('.claude-state/journal/2026-10-17.md#050646-1000w-coder-thinking')).toEqual({
      date: '2026-10-17', time: '050646', seq: 1000, writer: 'w', who: 'coder', kind: 'thinking',
    })
    expect(() => entryPath('2026-10-07', '143005', 7, 'x', 'dev', 'msg')).toThrow()
  })
})

describe('the journal reader\'s output (3.32.15: tools/journal_rollup.py entries)', () => {
  const rolledMsg = renderEntry({ ts: '2026-10-06T08:00:00.000Z', session: 'S0' }, 'Rolled hello\n\n## not a stem\nmore')
  const live = renderEntry({ ts: '2026-10-07T09:00:00.000Z' }, 'Live hello')
  // As the Python tool prints them: ASCII JSON, one entry a line, both forms; CRLF from a Windows console tolerated.
  const out = `${JSON.stringify({ path: '.claude-state/journal/2026-10-06.md#080000-001r-dev-msg', name: '2026-10-06/080000-001r-dev-msg.md', text: rolledMsg })}\r\n` +
    'refused: not a line of entries\n{"path": 3}\n\n' +
    `${JSON.stringify({ path: '.claude-state/journal/2026-10-07/090000-001r-dev-msg.md', name: '2026-10-07/090000-001r-dev-msg.md', text: live })}\n`

  test('entryLinesOf keeps the entry lines, raw; anything else is skipped', async () => {
    expect(entryLinesOf(out).map(l => [l.name, l.text])).toEqual([['2026-10-06/080000-001r-dev-msg.md', rolledMsg], ['2026-10-07/090000-001r-dev-msg.md', live]])
    expect(entryLinesOf('')).toEqual([])
  })

  test('entriesOf parses each: its path in either form, its frontmatter and body', async () => {
    const es = entriesOf(out)
    expect(es.map(e => e.path)).toEqual(['.claude-state/journal/2026-10-06.md#080000-001r-dev-msg', '.claude-state/journal/2026-10-07/090000-001r-dev-msg.md'])
    expect(es[0]?.meta).toEqual({ ts: '2026-10-06T08:00:00.000Z', session: 'S0' })
    expect(es[0]?.body).toBe('Rolled hello\n\n## not a stem\nmore')
    expect(es[1]?.body).toBe('Live hello')
    expect(parseEntryPath(es[0]?.path ?? '')?.kind).toBe('msg')
  })
})

describe('journal entries', () => {
  test('render and parse round-trip; missing keys are omitted', async () => {
    const meta = {
      ts: '2026-10-07T14:30:05.123Z',
      session: 'a1b2c3d4-0000-4000-8000-000000000000',
      agentId: 'ag_1',
      role: 'partner',
      model: 'claude-opus-5-5[1m]',
      effort: 'high',
      task: 'T6',
      usage: { input: 10, cacheRead: 12000, cacheWrite: 300, output: 45 },
    }
    const text = renderEntry(meta, 'Hello.\n\nSecond paragraph.')
    expect(text.startsWith('---\nts: 2026-10-07T14:30:05.123Z\n')).toBe(true)
    expect(text).toContain('usage: {input: 10, cacheRead: 12000, cacheWrite: 300, output: 45}')
    expect(text).not.toContain('parentId')
    expect(text).not.toContain('turnId')
    expect(parseEntry(text)).toEqual({ meta, body: 'Hello.\n\nSecond paragraph.' })
  })

  test('values are escaped to one line each', async () => {
    const meta = { task: 'a: b\nc', role: '"quoted" # x', turnId: '', parentId: ' lead' }
    const text = renderEntry(meta, 'body')
    const front = text.split('---')[1]
    expect(front.trim().split('\n').length).toBe(4)
    expect(text).toContain('task: "a: b\\nc"')
    expect(parseEntry(text)?.meta).toEqual(meta)
  })

  test('a file without frontmatter is not an entry; unknown keys are ignored', async () => {
    expect(parseEntry('no frontmatter')).toBe(null)
    expect(parseEntry('---\nts: x\n')).toBe(null)
    expect(parseEntry('---\nts: x\nother: y\n---\n\nbody\n')).toEqual({ meta: { ts: 'x' }, body: 'body' })
  })
})

describe('spawn entries (3.24 T17b)', () => {
  test('agentType is read and written', async () => {
    const text = ['---', 'agentId: P1', 'agentType: pa-planner', 'model: claude-opus-5-5[1m]', 'effort: max', 'managed: yes', '---', 'pa-planner ran.', ''].join('\n')
    expect(parseEntry(text)?.meta).toEqual({ agentId: 'P1', agentType: 'pa-planner', model: 'claude-opus-5-5[1m]', effort: 'max' })
    expect(renderEntry({ agentType: 'pa-planner' }, 'x')).toContain('agentType: pa-planner')
  })
})

describe('record entries (3.31)', () => {
  test('the record path round-trips, live and rolled', async () => {
    const p = entryPath('2026-10-30', '153000', 4, 'r', 'claude', 'record')
    expect(p).toBe('.claude-state/journal/2026-10-30/153000-004r-claude-record.md')
    expect(parseEntryPath(p)).toEqual({ date: '2026-10-30', time: '153000', seq: 4, writer: 'r', who: 'claude', kind: 'record' })
    expect(parseEntryPath('.claude-state/journal/2026-10-30.md#153000-004r-claude-record')?.kind).toBe('record')
  })

  test('through is read and written', async () => {
    const meta = { ts: '2026-10-30T15:30:00.000Z', through: '2026-10-30T15:12:04.000Z', session: 's1' }
    const text = renderEntry(meta, 'topic: alpha\nheadline: beta')
    expect(text).toContain('\nthrough: 2026-10-30T15:12:04.000Z\n')
    expect(parseEntry(text)).toEqual({ meta, body: 'topic: alpha\nheadline: beta' })
  })

  test('3.31 T2b: TOPICS.md\'s filed-through header', async () => {
    const head = '# Journal records -- rebuilt by tools/records.py index; one line per outcome\n'
    expect(filedThroughOf(`${head}# filed through: 2026-10-30T10:55:00.000Z\n# id | date\nR1 | x\n`)).toBe('2026-10-30T10:55:00.000Z')
    expect(filedThroughOf(`${head}# filed through: (none)\n`)).toBe('')
    expect(filedThroughOf(`${head}# id | date\n`)).toBe('') // an index written before the header
    expect(filedThroughOf(null)).toBe('')
    expect(TOPICS_PATH).toBe('.claude-state/journal/TOPICS.md')
  })
})
