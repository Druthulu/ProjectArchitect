import { describe, expect, test } from 'claude-code/testing'

import { buildIndex, dayFilePath, type Entry, entryPath, indexAgrees, indexPath, parseEntry, parseEntryPath, parseIndex, renderEntry, splitDay } from './journal'

describe('journal naming (vectors shared with pa-watch)', () => {
  test('the pinned vectors', async () => {
    expect(entryPath('2026-10-07', '143005', 7, 'r', 'dev', 'msg')).toBe('.claude-state/journal/2026-10-07/143005-007r-dev-msg.md')
    expect(entryPath('2026-10-07', '143005', 8, 'w', 'partner', 'thinking')).toBe(
      '.claude-state/journal/2026-10-07/143005-008w-partner-thinking.md',
    )
    expect(indexPath('2026-10')).toBe('.claude-state/journal/INDEX-2026-10.md')
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
    expect(() => indexPath('2026-1')).toThrow()
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
    expect(parseEntryPath('x/2026-10-07/143005-008w-expert-msg.md')).toBe(null)
    expect(parseEntryPath('.claude-state/journal/INDEX-2026-10.md')).toBe(null)
  })

  test('3.23 T7: the rolled form <date>.md#<stem> parses; mixed forms do not', async () => {
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md#143005-008w-partner-thinking')).toEqual({
      date: '2026-10-06', time: '143005', seq: 8, writer: 'w', who: 'partner', kind: 'thinking',
    })
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md#143005-008w-partner-thinking.md')).toBe(null)
    expect(parseEntryPath('.claude-state/journal/2026-10-06.md')).toBe(null)
  })
})

describe('journal rolled days (3.23 T7)', () => {
  // A day file as tools/journal_rollup.py render_day writes it: a header, then `## <stem>` + the entry text unchanged.
  const live = renderEntry({ ts: '2026-10-07T09:00:00.000Z' }, 'Live hello')
  const rolledMsg = renderEntry({ ts: '2026-10-06T08:00:00.000Z', session: 'S0' }, 'Rolled hello\n\n## not a stem\nmore')
  const rolledAns = renderEntry({ ts: '2026-10-06T08:00:10.000Z' }, 'Rolled answer')
  const day = `# Journal 2026-10-06 (2 entries, rolled by tools/journal_rollup.py)\n## 080000-001r-dev-msg\n${rolledMsg}## 080010-002r-claude-answer\n${rolledAns}`

  test('splitDay: one entry per stem heading, paths <date>.md#<stem>, frontmatter parsed', async () => {
    const es = splitDay('2026-10-06', day)
    expect(es.map((e) => e.path)).toEqual([
      '.claude-state/journal/2026-10-06.md#080000-001r-dev-msg',
      '.claude-state/journal/2026-10-06.md#080010-002r-claude-answer',
    ])
    expect(es[0]?.meta).toEqual({ ts: '2026-10-06T08:00:00.000Z', session: 'S0' })
    expect(es[0]?.body).toBe('Rolled hello\n\n## not a stem\nmore')
    expect(es[1]?.body).toBe('Rolled answer')
    expect(dayFilePath('2026-10-06')).toBe('.claude-state/journal/2026-10-06.md')
  })

  test('the index of a rolled day plus a live day; indexAgrees on both forms', async () => {
    const all: Entry[] = [...splitDay('2026-10-06', day), { path: '.claude-state/journal/2026-10-07/090000-001r-dev-msg.md', ...(parseEntry(live) ?? { meta: {}, body: '' }) }]
    const idx = buildIndex(all)
    expect(idx).toBe(
      '2026-10-06T08:00:00.000Z | dev | msg | Rolled hello | .claude-state/journal/2026-10-06.md#080000-001r-dev-msg\n' +
      '2026-10-06T08:00:10.000Z | claude | answer | Rolled answer | .claude-state/journal/2026-10-06.md#080010-002r-claude-answer\n' +
      '2026-10-07T09:00:00.000Z | dev | msg | Live hello | .claude-state/journal/2026-10-07/090000-001r-dev-msg.md\n',
    )
    expect(indexAgrees(idx, all.map((e) => e.path))).toBe(true)
    expect(indexAgrees(idx, all.map((e) => e.path.replace('2026-10-06.md#', '2026-10-06/') + (e.path.includes('#') ? '.md' : '')))).toBe(false)
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

describe('journal index', () => {
  const d = '.claude-state/journal/2026-10-07'
  const entries: Entry[] = [
    { path: `${d}/143010-002r-partner-answer.md`, meta: { ts: '2026-10-07T14:30:10Z' }, body: '\n  Yes | no, and ' + 'x'.repeat(100) },
    { path: `${d}/143005-001r-dev-msg.md`, meta: { ts: '2026-10-07T14:30:05Z' }, body: 'Can we ship?\nmore' },
    { path: `${d}/143005-001w-thinker-thinking.md`, meta: { ts: '2026-10-07T14:30:05Z' }, body: 'a \\ b' },
  ]

  test('one line per entry, sorted by ts then path, first line clipped and pipes escaped', async () => {
    const text = buildIndex(entries)
    const lines = text.trimEnd().split('\n')
    expect(lines.length).toBe(3)
    expect(lines[0]).toBe(`2026-10-07T14:30:05Z | dev | msg | Can we ship? | ${d}/143005-001r-dev-msg.md`)
    expect(lines[1]).toBe(`2026-10-07T14:30:05Z | thinker | thinking | a \\\\ b | ${d}/143005-001w-thinker-thinking.md`)
    expect(lines[2].startsWith('2026-10-07T14:30:10Z | partner | answer | Yes \\| no, and x')).toBe(true)
    const parsed = parseIndex(text)
    expect(parsed[2].first.length).toBe(80)
    expect(parsed[2].first.startsWith('Yes | no')).toBe(true)
    expect(parsed[1].first).toBe('a \\ b')
    expect(parsed.map((l) => l.path)).toEqual([entries[1].path, entries[2].path, entries[0].path])
  })

  test('indexAgrees only for exactly the listed paths', async () => {
    const text = buildIndex(entries)
    const paths = entries.map((e) => e.path)
    expect(indexAgrees(text, paths)).toBe(true)
    expect(indexAgrees(text, [...paths].reverse())).toBe(true)
    expect(indexAgrees(text, paths.slice(1))).toBe(false)
    expect(indexAgrees(text, [...paths, `${d}/150000-003r-dev-msg.md`])).toBe(false)
    expect(indexAgrees(text + text, paths)).toBe(false)
    expect(indexAgrees('', [])).toBe(true)
    expect(indexAgrees('', paths)).toBe(false)
  })
})

describe('spawn entries (3.24 T17b)', () => {
  test('agentType is read and written', async () => {
    const text = ['---', 'agentId: P1', 'agentType: pa-planner', 'model: claude-opus-5-5[1m]', 'effort: max', 'managed: yes', '---', 'pa-planner ran.', ''].join('\n')
    expect(parseEntry(text)?.meta).toEqual({ agentId: 'P1', agentType: 'pa-planner', model: 'claude-opus-5-5[1m]', effort: 'max' })
    expect(renderEntry({ agentType: 'pa-planner' }, 'x')).toContain('agentType: pa-planner')
  })
})
