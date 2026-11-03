// 3.32.1 T2: the stall snapshot's pure parts: a helper transcript's tail, the process list's redaction, the 60,000 cap.
import { describe, expect, test } from 'claude-code/testing'

import { processLines, redact, SNAPSHOT_CAP, type Snapshot, snapshotText, stallFile, transcriptTail } from './stall'

const row = (type: string, ts: string, content: unknown, version = '2.1.300'): string =>
  JSON.stringify({ type, timestamp: ts, version, message: { content } })
const use = (id: string, name: string, input: Record<string, unknown>): Record<string, unknown> => ({ type: 'tool_use', id, name, input })
const result = (id: string, text: string): Record<string, unknown> => ({ type: 'tool_result', tool_use_id: id, content: [{ type: 'text', text }] })
const notice = (id: string, summary: string): string =>
  `<task-notification>\n<task-id>${id}</task-id>\n<status>completed</status>\n<summary>${summary}</summary>\n</task-notification>`

/** A coder's transcript: a background gate never answered, a background build answered, a retriever never answered. */
export const CODER_JSONL = [
  row('user', '2026-11-02T10:00:00Z', 'CODER TASK T1.c1', '2.1.299'),
  row('assistant', '2026-11-02T10:00:01Z', [use('u1', 'Read', { file_path: 'C:\\r\\calc\\add.py' })]),
  row('user', '2026-11-02T10:00:02Z', [result('u1', 'def add')]),
  row('assistant', '2026-11-02T10:00:03Z', [use('u2', 'Bash', { command: 'bash tools/run.sh build -- make all', run_in_background: true })]),
  row('user', '2026-11-02T10:00:04Z', [result('u2', 'Command running in background with ID: bld1')]),
  row('assistant', '2026-11-02T10:00:05Z', [use('u3', 'Bash', { command: `bash tools/run.sh gate -- ${'x'.repeat(200)}`, run_in_background: true })]),
  row('user', '2026-11-02T10:00:06Z', [result('u3', 'Command running in background with ID: gate2')]),
  row('assistant', '2026-11-02T10:00:07Z', [use('u4', 'Agent', { subagent_type: 'retriever-code', description: 'find the callers' })]),
  row('user', '2026-11-02T10:00:08Z', [result('u4', 'Async agent launched. agentId: r9')]),
  row('user', '2026-11-02T10:01:00Z', notice('bld1', 'build finished')),
  row('assistant', '2026-11-02T10:01:01Z', [{ type: 'text', text: `Build green; waiting on the gate. ${'y'.repeat(400)}` }]),
].join('\n')

describe('the transcript tail (3.32.1 T2)', () => {
  test('tool calls, open background work, the answered one left out, the last text and the newest version', async () => {
    const t = transcriptTail(CODER_JSONL)
    expect(t.version).toBe('2.1.300')
    expect(t.tools).toEqual([
      '2026-11-02T10:00:01 Read add.py',
      '2026-11-02T10:00:03 Bash [background] bash tools/run.sh build -- make all',
      `2026-11-02T10:00:05 Bash [background] ${`bash tools/run.sh gate -- ${'x'.repeat(200)}`.slice(0, 119)}…`,
      '2026-11-02T10:00:07 Agent retriever-code: find the callers',
    ])
    expect(t.open).toEqual([
      `shell gate2 started 2026-11-02T10:00:06, no completion notice: [background] ${`bash tools/run.sh gate -- ${'x'.repeat(200)}`.slice(0, 119)}…`,
      'agent r9 started 2026-11-02T10:00:08, no completion notice: retriever-code: find the callers',
    ])
    expect(t.lastText).toHaveLength(300)
    expect(t.lastText.startsWith('Build green; waiting on the gate.')).toBe(true)
  })

  test('the last 15 tool calls only; a notice still waiting stays open; a message back after it closes it', async () => {
    const rows = Array.from({ length: 20 }, (_, i) => row('assistant', `2026-11-02T10:00:${String(10 + i)}Z`, [use(`g${String(i)}`, 'Grep', { pattern: `p${String(i)}` })]))
    expect(transcriptTail(rows.join('\n')).tools).toEqual(rows.slice(5).map((_, i) => `2026-11-02T10:00:${String(15 + i)} Grep p${String(i + 5)}`))
    const waits = [
      row('assistant', '2026-11-02T10:00:00Z', [use('a1', 'Agent', { subagent_type: 'pa-coder', description: 'c1' })]),
      row('user', '2026-11-02T10:00:01Z', [result('a1', 'agentId: k1')]),
      row('user', '2026-11-02T10:00:02Z', notice('k1', 'Agent is waiting for a shell')),
    ]
    expect(transcriptTail(waits.join('\n')).open).toEqual(
      ['agent k1 last notice 2026-11-02T10:00:02 says still waiting (completed: Agent is waiting for a shell): pa-coder: c1'])
    const back = [...waits, row('user', '2026-11-02T10:00:03Z', '<agent-message from="k1">done</agent-message>')]
    expect(transcriptTail(back.join('\n')).open).toEqual([])
  })

  test('3.32.1 T3: a foreground call moved to the background at its time limit counts as a started shell', async () => {
    const rows = [
      row('assistant', '2026-11-02T10:00:00Z', [use('p1', 'Bash', { command: 'python -' })]),
      row('user', '2026-11-02T10:02:00Z', [result('p1', 'Command did not complete within its 120s timeout and was moved to the background (ID: mv1)')]),
    ]
    expect(transcriptTail(rows.join('\n')).open).toEqual(['shell mv1 started 2026-11-02T10:02:00, no completion notice: python -'])
  })
})

describe('the process list (3.32.1 T2)', () => {
  test('secrets after key, token, secret, password or auth are masked, any case, = or a space', async () => {
    expect(redact('node app.js --api-key=abc123 --TOKEN xyz SECRET="a b" password: pw Authorization Bearer q')).toBe(
      'node app.js --api-key=[redacted] --TOKEN [redacted] SECRET=[redacted] password: [redacted] Authorization [redacted] q')
    expect(redact('python train.py --epochs 3')).toBe('python train.py --epochs 3')
  })

  test('lines cut to 160, blank lines dropped, at most 400 and a count of the rest', async () => {
    const out = processLines(['PID PPID ELAPSED COMMAND', '', `12 1 01:00 ${'z'.repeat(300)}`, ...Array.from({ length: 450 }, (_, i) => `${String(i)} 1 00:01 sleep`)].join('\r\n'))
    expect(out).toHaveLength(401)
    expect(out[0]).toBe('PID PPID ELAPSED COMMAND')
    expect(out[1]).toHaveLength(160)
    expect(out[400]).toBe('(52 more lines)')
  })
})

describe('the snapshot file (3.32.1 T2)', () => {
  const base: Snapshot = {
    task: 'T1', attempt: 1, expert: { id: 'E1', type: 'pa-expert' }, route: 'done', ageMs: 124_000, pa3: '3.32.1',
    claudeCode: '2.1.300', platform: 'Windows_NT', session: 'S1', at: '2026-11-02T10:03:00.000Z',
    list: [{ id: 'C1', type: 'pa-coder', description: 'T1.c1 roads', status: 'running', parentId: 'E1' }],
    shells: null, facts: [{ id: 'E1', text: '{"status":"done"}' }], tails: [{ id: 'C1', type: 'pa-coder', tail: transcriptTail(CODER_JSONL) }],
    processes: ['PID PPID ELAPSED COMMAND'], gaps: [],
  }

  test('its path, and the cap at 60,000 characters', async () => {
    expect(stallFile('T1', '2026-11-02', '100300')).toBe('.run/mod/stalls/T1-20261102-100300.md')
    expect(snapshotText(base).length).toBeLessThan(SNAPSHOT_CAP)
    const big = snapshotText({ ...base, processes: Array.from({ length: 400 }, () => 'p'.repeat(160)) })
    expect(big).toHaveLength(SNAPSHOT_CAP)
    expect(big.endsWith('\n[cut at 60,000 characters]\n')).toBe(true)
  })
})
