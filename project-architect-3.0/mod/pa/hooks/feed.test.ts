// The live feed's pure parts added in 3.23 T13d.c2 (feed.ts): the runs the terminal draws, the draw rule's choice, the
// feed block on a developer message, the launcher's error, and the restart's wait.
import { describe, expect, test } from 'claude-code/testing'

import { ROLE_HEX } from './card'
import {
  editHead, editOfRecord, editText, emptyLife, FEED_BLOCK_CAP, FEED_BLOCK_MAX, feedBlock, feedErrorLine, feedEvents, feedLook, type FeedRecord, type FeedRun, feedText,
  announces, modelName, parseFeedLine, quote, replyRecord, shownOf, type Speaker, speakerOf, staleOf, unseenFeed,
} from './feed'

const rec = (n: number, agent: string, text: string, type = 'coder-opus55', at = n): FeedRecord => ({ n, agent, type, desc: 'd', planning: false, text, at })
const CODER: Speaker = { role: 'coder', label: 'Coder · T3' }
const EXPERT: Speaker = { role: 'expert', label: 'Expert · T3' }

describe('the feed in the cards\' look (3.23 T13d.c2)', () => {
  test('runs: titled on a speaker change, one run per speaker, a first run continuing the last speaker untitled', async () => {
    const items = [{ rec: rec(1, 'C1', 'Adding plus.\nThen tests.'), who: CODER }, { rec: rec(2, 'C1', 'Done.'), who: CODER }, { rec: rec(3, 'E1', 'Checking.'), who: EXPERT }]
    const a = feedText(items, '')
    expect(a.runs).toEqual([
      { label: 'Coder · T3', role: 'coder', titled: true, lines: ['Adding plus.', 'Then tests.', '', 'Done.'] },
      { label: 'Expert · T3', role: 'expert', titled: true, lines: ['Checking.'] },
    ])
    const b = feedText([{ rec: rec(4, 'E1', 'Still checking.'), who: EXPERT }], a.speaker)
    expect(b.text).toBe('> Still checking.')
    expect(a.text).toBe('**🟩 Coder · T3**\n> Adding plus.\n> Then tests.\n>\n> Done.\n\n**🟦 Expert · T3**\n> Checking.')
    expect(b.runs).toEqual([{ label: 'Expert · T3', role: 'expert', titled: false, lines: ['Still checking.'] }])
  })

  // 3.24 T13p: the phone has no render hook; the stored text is blockquote markdown, so the run's markdown renders there.
  test('3.24 T13p: a run with a heading, a list, backticks and a fence is stored as a well-formed blockquote', async () => {
    const said = '## Plan\n- run `make test`\n- then **ship**\n\n```sh\nmake test 2>&1\n```\nDone.'
    const a = feedText([{ rec: rec(1, 'C1', said), who: CODER }, { rec: rec(2, 'C1', 'Next.'), who: CODER }, { rec: rec(3, 'E1', 'Ok.'), who: EXPERT }], '')
    const [coder, expert] = a.text.split('\n\n**')
    const body = (coder ?? '').split('\n').slice(1)
    expect(body.every(l => l === '>' || l.startsWith('> '))).toBe(true)
    expect(body.map(l => l.replace(/^> ?/, '')).join('\n')).toBe(`${said}\n\nNext.`)
    expect(body.filter(l => l.startsWith('> ```')).length).toBe(2)
    expect(expert).toBe('🟦 Expert · T3**\n> Ok.')
    expect(a.runs[0]?.lines.join('\n')).toBe(`${said}\n\nNext.`)
  })

  test('the draw rule\'s choice under the four styles: a note card\'s border, the role\'s hex, the title only when titled', async () => {
    const run = { label: 'Coder · T3', role: 'coder' as const, titled: true, lines: ['x'] }
    expect(feedLook(run, 'mixed')).toEqual({ border: 'quote', color: ROLE_HEX.coder, title: 'Coder · T3', lines: ['x'] })
    expect(feedLook(run, 'bar').border).toBe('quote')
    expect(feedLook(run, 'boxed').border).toBe('round')
    expect(feedLook(run, 'plain')).toEqual({ border: null, color: ROLE_HEX.coder, title: '● Coder · T3', lines: ['x'] })
    expect(feedLook({ ...run, titled: false }, 'plain').title).toBe('')
    expect(feedLook({ ...run, titled: false }, 'boxed').title).toBe('')
    expect(feedLook({ ...run, role: null, label: 'general-purpose' }, 'mixed').color).toBeUndefined()
  })
})

describe('the feed block on a developer message (3.23 T13d.c2)', () => {
  const log = (recs: FeedRecord[]): string => recs.map(r => JSON.stringify(r)).join('\n') + '\n'

  test('drawn records since the last answer, not the partner\'s, in order; nothing drawn yet: none', async () => {
    const text = log([rec(1, 'C1', 'old', 'coder-opus55', 100), rec(2, 'P1', 'mine', '', 300), rec(3, 'E1', 'mine too', 'expert-opus55', 300), rec(4, 'C1', 'new', 'coder-opus55', 400), rec(5, 'C1', 'not drawn', 'coder-opus55', 500)])
    expect(unseenFeed(text, 4, 200, 'P1').map(r => r.n)).toEqual([3, 4])
    expect(unseenFeed(text, 4, 200, 'E1').map(r => r.n)).toEqual([4])
    expect(unseenFeed(text, null, 0, 'P1')).toEqual([])
  })

  test('a record\'s kind (summary or text, 3.23 T13d.c5) is accepted and passes no field of its own', async () => {
    const line = '{"agent":"C1","type":"coder-opus55","desc":"","planning":false,"text":"Let me load the skill.","at":5,"kind":"summary","n":7}'
    expect(parseFeedLine(line)).toEqual({ n: 7, agent: 'C1', type: 'coder-opus55', desc: '', planning: false, text: 'Let me load the skill.', at: 5 })
  })

  test('the latest 40 records at most', async () => {
    const recs = Array.from({ length: 50 }, (_, i) => rec(i + 1, 'C1', `line ${String(i + 1)}`))
    const got = unseenFeed(log(recs), 50, 0, 'P1')
    expect(got.length).toBe(FEED_BLOCK_MAX)
    expect(got[0]?.n).toBe(11)
  })

  test('the block: a first line naming the log, then label: line per text line; empty: absent', async () => {
    expect(feedBlock([{ rec: rec(1, 'C1', 'Adding plus.\n\n  Then tests.'), who: CODER }, { rec: rec(2, 'E1', 'Checking.'), who: EXPERT }], '.run/mod/S1/feed')).toBe([
      '‹pa› the feed you have not seen (since your last answer; older lines: .run/mod/S1/feed/log.jsonl):',
      'Coder · T3: Adding plus.', 'Coder · T3: Then tests.', 'Expert · T3: Checking.',
    ].join('\n'))
    expect(feedBlock([], 'd')).toBe('')
    // 3.23 T13d.c7: each record's text cut at FEED_BLOCK_CAP chars, `…` when cut.
    expect(feedBlock([{ rec: rec(3, 'C1', 'x'.repeat(FEED_BLOCK_CAP + 5)), who: CODER }], 'd').split('\n')[1])
      .toBe(`Coder · T3: ${'x'.repeat(FEED_BLOCK_CAP)}…`)
  })
})

describe('a cut feed line (3.23 T13d.c6a)', () => {
  test('a record line Claude Code cut, the last of its event, is a cut event with its number; other broken lines are notes', async () => {
    const t = '<event>{"pa":"feed","n":4,"agent":"E1","text":"short"}\n{"pa":"feed","n":5,"agent":"E1","type":"expert-opus55","text":"a long summ…(truncated)</event>'
    expect(feedEvents(t).map(x => (x.kind === 'record' ? `r${String(x.rec.n)}` : x.kind === 'cut' ? `c${String(x.n)}` : x.kind))).toEqual(['r4', 'c5'])
    expect(feedEvents('<event>{"pa":"feed","agent":"E1","text":"x…(truncated)</event>')).toEqual([{ kind: 'note', line: '{"pa":"feed","agent":"E1","text":"x…(truncated)' }])
  })
})

describe('a feed that cannot start (3.23 T13d.c2)', () => {
  test('the launcher\'s error record is an error event; its line names pa.json\'s python key', async () => {
    const t = '<task-notification>\n<summary>Monitor event: "pa feed"</summary>\n<event>{"pa":"feed","error":"no python"}</event>\n</task-notification>'
    expect(feedEvents(t)).toEqual([{ kind: 'error', error: 'no python' }])
    expect(feedErrorLine('no python')).toBe("The live feed could not start: no Python found (pa.json's python key).")
  })

  test('no "no heartbeat" restart while a restart\'s Monitor waits; none ever after the error', async () => {
    const life = { ...emptyLife(), devAt: 0, restarts: [30_000] }
    expect(staleOf(life, null, 100_000)).toBe('none')
    expect(staleOf({ ...life, waiting: true }, null, 100_000)).toBe('')
    expect(staleOf({ ...life, error: 'no python' }, null, 100_000)).toBe('')
    expect(staleOf({ ...life, seen: true, error: 'no python' }, null, 100_000)).toBe('')
  })
})

describe('3.24 T8b: file edit cards in the feed', () => {
  const EDIT = { path: 'src/calc.py', created: false, add: 2, del: 1, hunks: ['@@ -1,2 +1,3 @@\n def f():\n-    return 1\n+    x = 2\n+    return x', '@@ -20,1 +21,1 @@\n-a\n+b'], more: 4 }
  const edited = (n: number, agent: string): FeedRecord => ({ ...rec(n, agent, 'Updated src/calc.py (+2 -1)'), edit: EDIT })

  test('an edit record line parses with its edit; a bad edit field is dropped', async () => {
    const line = JSON.stringify({ agent: 'C1', type: 'coder-opus55', desc: '', planning: false, text: 'Updated src/calc.py (+2 -1)', at: 5, kind: 'edit', edit: EDIT, n: 9 })
    expect(parseFeedLine(line)?.edit).toEqual(EDIT)
    expect(parseFeedLine(line.replace('"path":"src/calc.py"', '"path":""'))?.edit).toBeUndefined()
  })

  test('the header and the phone\'s text: the header line, the hunks in one diff fence, the rows left out', async () => {
    expect(editHead(EDIT)).toEqual({ verb: 'Updated src/calc.py', counts: ' (+2 -1)' })
    expect(editText(EDIT)).toBe(['Updated src/calc.py (+2 -1)', '```diff', ...EDIT.hunks.join('\n').split('\n'), '```', '… +4 lines'].map(quote).join('\n'))
    const made = { path: 'n.md', created: true, add: 2, del: 0, hunks: ['# Title\nbody'], more: 0 }
    expect(editHead(made)).toEqual({ verb: 'Created n.md', counts: ' (2 lines)' })
    expect(editText(made)).toBe('> Created n.md (2 lines)\n> ```diff\n> +# Title\n> +body\n> ```')
  })

  test('an edit is a run of its own: titled on a speaker change, else a continuation; text after it starts a new run', async () => {
    const items = [{ rec: rec(1, 'C1', 'Fixing f.'), who: CODER }, { rec: edited(2, 'C1'), who: CODER }, { rec: rec(3, 'C1', 'Testing.'), who: CODER }, { rec: edited(4, 'E1'), who: EXPERT }]
    const a = feedText(items, '')
    expect(a.runs).toEqual([
      { label: 'Coder · T3', role: 'coder', titled: true, lines: ['Fixing f.'] },
      { label: 'Coder · T3', role: 'coder', titled: false, lines: [], edit: EDIT },
      { label: 'Coder · T3', role: 'coder', titled: false, lines: ['Testing.'] },
      { label: 'Expert · T3', role: 'expert', titled: true, lines: [], edit: EDIT },
    ])
    expect(a.text).toBe([`**🟩 Coder · T3**`, '> Fixing f.', '', editText(EDIT), '', '> Testing.', '', `**🟦 Expert · T3**`, editText(EDIT)].join('\n'))
  })

  test('each style\'s note form carries the edit: mixed and bar a side bar, boxed a round border, plain none', async () => {
    const run = { label: 'Coder · T3', role: 'coder' as const, titled: true, lines: [], edit: EDIT }
    expect(feedLook(run, 'mixed')).toEqual({ border: 'quote', color: ROLE_HEX.coder, title: 'Coder · T3', lines: [], edit: EDIT })
    expect(feedLook(run, 'bar')).toEqual(expect.objectContaining({ border: 'quote', edit: EDIT }))
    expect(feedLook(run, 'boxed')).toEqual(expect.objectContaining({ border: 'round', edit: EDIT }))
    expect(feedLook(run, 'plain')).toEqual(expect.objectContaining({ border: null, title: '● Coder · T3', edit: EDIT }))
  })

  test('Claude\'s feed block names the edit by its header', async () => {
    expect(feedBlock([{ rec: edited(2, 'C1'), who: CODER }], 'D')).toContain('\nCoder · T3: Updated src/calc.py (+2 -1)')
  })
})

describe('3.24 T13B: reply records, the curator and the auditor, plan-draft labels', () => {
  test('a reply record round-trips its number; its run is a Claude card, stored as the card; the next line is titled', async () => {
    const line = replyRecord({ agent: 'P1', n: 4, text: 'Unasked news.\n\n- one', at: 9 }).trim()
    const r = parseFeedLine(JSON.stringify({ ...JSON.parse(line), n: 7 }))
    expect(r).toEqual({ n: 7, agent: 'P1', type: '', desc: 'Claude', planning: false, text: 'Unasked news.\n\n- one', at: 9, reply: 4 })
    const CLAUDE: Speaker = { role: 'claude', label: 'Claude' }
    const out = feedText([{ rec: rec(6, 'P1', 'Checking.', ''), who: CLAUDE }, { rec: r as FeedRecord, who: CLAUDE }, { rec: rec(8, 'P1', 'More.', ''), who: CLAUDE }], '')
    expect(out.text).toBe('**🟧 Claude**\n> Checking.\n\n**🟧 Claude**\n\nUnasked news.\n\n- one\n\n**🟧 Claude**\n> More.')
    expect(out.runs.map(x => [x.titled, x.card === true])).toEqual([[true, false], [true, true], [true, false]])
    expect(feedLook(out.runs[1] as FeedRun, 'mixed').card).toBe(true)
    expect(unseenFeed(`${JSON.stringify({ ...JSON.parse(line), n: 1 })}\n`, 1, 0, '')).toEqual([])
  })

  test('the curator and the auditor speak in their own colors', async () => {
    const s = { role: 'other' as const, task: '', coder: '' }
    expect(speakerOf(rec(1, 'K', 'x', 'memory-curator'), 'P1', s)).toEqual({ role: 'curator', label: 'Curator' })
    expect(speakerOf(rec(1, 'A', 'x', 'auditor'), 'P1', s)).toEqual({ role: 'auditor', label: 'Auditor' })
    expect(speakerOf(rec(1, 'K', 'x', 'pa-thinker'), 'P1', s)).toEqual({ role: 'thinker', label: 'Thinker' }) // 3.25 T6b
    expect(ROLE_HEX.curator).toBe('#d75faf')
    expect(ROLE_HEX.auditor).toBe('#5f87af')
  })

  test('a plan draft\'s label replaces the verb and path', async () => {
    const e = editOfRecord({ path: '.run/PHASE_PLAN.draft.md', created: true, add: 58, del: 0, hunks: [], more: 0, label: 'Plan drafted' })
    expect(e === undefined ? '' : `${editHead(e).verb}${editHead(e).counts}`).toBe('Plan drafted (58 lines)')
    const u = editOfRecord({ path: 'p', created: false, add: 3, del: 1, hunks: [], more: 0, label: 'Plan draft updated' })
    expect(u === undefined ? '' : `${editHead(u).verb}${editHead(u).counts}`).toBe('Plan draft updated (+3 -1)')
    expect(editOfRecord({ path: 'p', created: true, add: 1, del: 0, hunks: [], more: 0 })?.label).toBeUndefined()
  })
})

describe('every agent names its model and effort (3.24 T17b)', () => {
  test('a model id as the developer reads it; the shown text', async () => {
    expect(['claude-opus-5-5', 'claude-fable-5-1', 'claude-sonnet-5-5-20260301', 'claude-opus-5-5[1m]', 'claude-haiku-5', 'opus'].map(modelName))
      .toEqual(['Opus 5.5', 'Fable 5.1', 'Sonnet 5.5', 'Opus 5.5', 'Haiku 5', 'opus'])
    expect([shownOf('claude-opus-5-5', 'max'), shownOf('claude-opus-5-5', ''), shownOf('', 'max')]).toEqual(['Opus 5.5 · max', 'Opus 5.5', ''])
  })
  test('the first titled run of an agent carries it, once; Claude never', async () => {
    const shown = { E1: 'Opus 5.5 · medium', T1: 'Opus 5.5 · max' }
    const THINKER: Speaker = { role: 'thinker', label: 'Thinker' }
    const a = feedText([{ rec: rec(1, 'E1', 'Starting T1.'), who: { role: 'expert', label: 'Expert · T1' } }, { rec: rec(2, 'T1', 'Weighing.', 'thinker'), who: THINKER },
      { rec: rec(3, 'E1', 'Next.'), who: { role: 'expert', label: 'Expert · T1' } }], '', shown)
    expect(a.runs.map(r => r.label)).toEqual(['Expert · T1 · Opus 5.5 · medium', 'Thinker · Opus 5.5 · max', 'Expert · T1'])
    expect(a.text.startsWith('**🟦 Expert · T1 · Opus 5.5 · medium**')).toBe(true)
    expect(a.announced).toEqual(['E1', 'T1'])
    expect(feedText([{ rec: rec(1, 'E1', 'x'), who: EXPERT }], '').announced).toEqual([])
    expect([announces(rec(1, 'P1', 'x', ''), 'P1'), announces(rec(1, 'P1', 'x', 'pa-claude'), 'P1'), announces({ ...rec(1, 'E1', 'x'), reply: 1 }, 'P1'), announces(rec(1, 'E1', 'x'), 'P1')])
      .toEqual([false, false, false, true])
  })
  test('the planner speaks as Planning', async () => {
    expect(speakerOf(rec(1, 'L1', 'Reading.', 'pa-planner'), 'P1', { role: 'other', task: '', coder: '' })).toEqual({ role: 'planning', label: 'Planning' })
  })
  test('3.3 T7: an external coder speaks as a coder titled by its brief id; its model name as given', async () => {
    const ext = { ...rec(1, 'ext-T4.c1', 'Editing.', 'ext-coder'), desc: 'T4.c1' }
    expect(speakerOf(ext, 'P1', { role: 'expert', task: 'T4', coder: '' })).toEqual({ role: 'coder', label: 'External coder · T4.c1' })
    expect(speakerOf({ ...ext, desc: '' }, 'P1', { role: 'other', task: '', coder: '' })).toEqual({ role: 'coder', label: 'External coder' })
    expect(announces(ext, 'P1')).toBe(true)
    expect(shownOf('DeepSeek V4.1 Flash', '')).toBe('DeepSeek V4.1 Flash')
  })
})

describe('3.24 T17g: Claude\'s reply cards name their model and effort', () => {
  test('a reply record carries `shown` through the log; its card and run are titled with it', async () => {
    const line = replyRecord({ agent: 'P1', n: 4, text: 'Done.', at: 9, shown: shownOf('claude-opus-5-5[1m]', 'medium') }).trim()
    const r = parseFeedLine(JSON.stringify({ ...JSON.parse(line), n: 7 }))
    expect(r).toEqual({ n: 7, agent: 'P1', type: '', desc: 'Claude', planning: false, text: 'Done.', at: 9, reply: 4, shown: 'Opus 5.5 · medium' })
    const out = feedText([{ rec: r as FeedRecord, who: { role: 'claude', label: 'Claude' } }], '')
    expect(out.text).toBe('**🟧 Claude · Opus 5.5 · medium**\n\nDone.')
    expect(out.runs[0]?.label).toBe('Claude · Opus 5.5 · medium')
    expect('shown' in (JSON.parse(replyRecord({ agent: 'P1', n: 1, text: 'x', at: 1, shown: '' })) as object)).toBe(false)
  })
})
