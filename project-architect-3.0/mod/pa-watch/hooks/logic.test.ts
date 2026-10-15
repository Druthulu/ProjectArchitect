import { describe, expect, test } from 'claude-code/testing'

import { decide, errorText, ledgerPath, paSetting, releaseOf, heartbeatText, versionAtLeast, versionOf } from './activation'
import { clip, eventFile } from './debug'
import { isNarration, startsLine, startText } from './feed'
import { displayWho, entryPath, entryText, noteLines, noteWho, partnerIdOf, stepFactsText, whoOf } from './journal'
import { argSummary, queuedReply, requestName, validateInput } from './tools'

// The same vectors as mod/pa/hooks/logic.test.ts: the two copies of activation.ts must agree.
const ON = '{"mod":"on","warmer":"off","upgrade":"ask"}'
const V = '2.1.293'

describe('activation (copy of the router\'s)', () => {
  test('pa.json mod: on, and nothing else, turns it on', async () => {
    expect(decide(ON, undefined, V).active).toBe(true)
    expect(decide(null, undefined, V)).toEqual({ active: false, reason: 'no .claude/pa.json' })
    expect(decide('{"mod":"off"}', undefined, V).active).toBe(false)
    expect(decide('{"mod":true}', undefined, V).active).toBe(false)
    expect(decide('{}', undefined, V).active).toBe(false)
    expect(decide('not json', undefined, V).active).toBe(false)
  })

  test('PA_MOD_OFF, non-empty and not 0, turns it off', async () => {
    expect(decide(ON, '1', V).active).toBe(false)
    expect(decide(ON, 'yes', V).active).toBe(false)
    expect(decide(ON, '0', V).active).toBe(true)
    expect(decide(ON, '', V).active).toBe(true)
  })

  test('the version floor compares numerically', async () => {
    expect(decide(ON, undefined, '2.1.292').active).toBe(true)
    expect(decide(ON, undefined, '2.1.291').active).toBe(false)
    expect(decide(ON, undefined, '2.1.1000').active).toBe(true)
    expect(decide(ON, undefined, '2.2.0').active).toBe(true)
    expect(decide(ON, undefined, '10.0.0').active).toBe(true)
    expect(decide(ON, undefined, '1.9.999').active).toBe(false)
    expect(decide(ON, undefined, 'dev').active).toBe(false)
    expect(versionAtLeast('2.1.300-dev.20260920', '2.1.292')).toBe(true)
    expect(versionAtLeast('x', '2.1.292')).toBe(null)
    expect(versionOf({ version: '2.1.293-dev.20261001.t1.sha1', base: '2.1.293-dev' })).toBe('2.1.293-dev')
    expect(versionOf({ version: '2.1.293' })).toBe('2.1.293')
  })

  test('pa.json settings', async () => {
    expect(paSetting('{"python":"C:/py/python.exe"}', 'python')).toBe('C:/py/python.exe')
    expect(paSetting('{"python":3}', 'python')).toBe('')
    expect(paSetting(null, 'python')).toBe('')
  })

  test('ledger path: PA_MOD_PA3_DIR, else home', async () => {
    expect(ledgerPath('Z:\\snap\\project-architect-3.0\\', 'C:\\Users\\u')).toBe('Z:/snap/project-architect-3.0/pa_ledger.py')
    expect(ledgerPath(undefined, 'C:\\Users\\u')).toBe('C:/Users/u/.claude/pa3/pa_ledger.py')
    expect(ledgerPath('', '/home/u')).toBe('/home/u/.claude/pa3/pa_ledger.py')
    expect(ledgerPath(undefined, '')).toBe('')
  })
})

describe('watcher helpers', () => {
  test('event files', async () => {
    expect(eventFile('d', 1700000000000, 7, 'watch.activation')).toBe('d/1700000000000-00007-watch.activation.json')
    expect(clip('abcdef', 3)).toBe('abc…')
  })
})

// 3.24 T13n: real signatures from the attended run (session d76796c5, subagent ac948616000000028.jsonl): line 29, T2's
// "I'll write a bash transcript script ..." (a narration block), and line 21, an ordinary thinking block.
const SIG_NARRATION = 'CAQSqwkKEQgSGAI4AUIJbmFycmF0aW9uEgyzxzcVZBdSFCHWNdEaDCR+dK4EP9++KfFL6CIwtsBlH6LIr7SuNCjqxh3I3cNBy8sTaF9QFqGLYDXaRusP2jT9Fvq3Vq018Z/Ufcl4KscIfV7lEkgVwg0ja+NNaODYWduJDd82CC/50EbdjnwCFlF0Y4cR2Abby/d3Ip6tTJYJSSY3uGLsLRMwOrw8FOQM33EyYs7AXfWjglKqGFzikgCPGuKbAO4Y25E3an1HpnupvSe4iX7anETXzhSw3hy4C44MzyTkC+iruNhMkdzFZxFV/GzvvUPi8YEmcls3/aUKRB8r1vjqd2Q7xDBQfYeVZnAaL5J456GLi7I844AS7QQV3gYdVHR1fXUuxWAl79siAO+rkYt3+8bl95FQZBo7S/fP+ogOM9TwGr8VrQNISPZBK9/osVgo7Fh0U25ji1nH631LsyhB1hVrJU7j9opVJIElo4CTPRLNqDSxZKTlCbDJvQfBdS/8oHKaXtnDjrDnv9+KHrEAz0xNL2UcxlJNlvsouURQrUFuiTFbAFO2vyIa6uVrgCxI7T+py4W71erGooISdkt+y6Lf5oLimkC0vbu856o0aNyec+v2SNR8KlSiD4iYzkUZCIxVwREvDTobJTpzl4IO+g2WnXXy4Jf31G3qI5tzA8T//OF//wayv4s8qNKZupTG7KAi3n8NK5jHcWVu6Op8xEuJCPbtiY3rSuIqw1PrxxvwKD6Dteddmya7OmnrQanZ2JonHci/H4dT6UwmND/j/ILQ8FcilBvgUf7i3bb840bowmSHCcR1E1wPF4RQcWVUiupxLsy3bQ1WP3fi606btPAoJ4g9xnP7hjUygdTh+rkdWpZf2PdafGylYxkMrmuR8r3Fm2Kv6CZSsxQpmdbIu3iOlZySE9annWmi6iNhg2wLTLV51A81yV3HGUy0bl/ekBiI0VCv/pmT3cgIAK2ULWs6P85881nMlbPNYUlW3Vo0RVKv8pWjZRpb9cBzFcs420P6onavJMxD8AwE23GBR8XhH7h3QSh78EdTnUxJ5Yd1ymWEkLSA0ZY5OKqCIXyxo0QbmP8hdp4zE0I73nH4H0LJe98zZiUEBv8tFzz7zLGQJ/Q5/MhrlfFroA12R5oSSAemunyRXRsgJe/1b7K2J0ToUFAgjw6ZjM4PDWvnKFcJGsDlHgQCIfUXe9rE89C85EQJxqHjYRiiiaNm+lADOWVpYL/gUHvvqw3G8SBOLssx9c0yB91MIlCSwcFWZST7SK5WuRacKH5/LAlNMlk4K2SQ+a9BY3r0fU/yhCtAHOW8UsKzE+TmXCEaTNdgltbMDMmqsaXID00PDWOfN/W129BRk81f60XjxOxRhztuJyRtrNaYc9SNl8GcmwD/H7JAuKZySjLSXOlYKYtvOVMSTqNB3mXTkoQfGl8OOyh6TYMsHzcbez36VOLkmlM4TikiGam5lEj/ASjabbwvgqwdu8kwtR8OLKrCqvcbCRIVxMQ5ru75RTtA6oUe59tM7TsXEA3WvXqMmv11qtst3croYQ+gqetBzCDgH4R7n9Ti1i/107HlMGbzOtpTXWjwbcDPGAE='
const SIG_THINKING = 'CAQS3QUKEAgSGAI4AUIIdGhpbmtpbmcSDOrluh0DloaXh5s2EhoMHDdKnYZU6m/dlDX6IjC+YP0Zz5K2gGEnQjpw549f0DjKuFQer0L8SuiUiHHFYvBXNXxsri9+liXeU6ldvWMq+gTMs9WuU/mxtB2GV6FWb5TkvIv2sacg5emmtoJ9FwPem7KeDG8MAWO1GyrOYcFaha8/qBq4auB7cZJjrxKfbqlZ/J3fXMo+h0ixoT0ZGNMyQ3Y5JyizLSptotZpqOg36SCwRgicJLZ5y+lqXKgeXnu1IIvYnjdEmC87ecTkXpzA/hcHSN07VDRSMiitzDAGsmh1q2L9b+WBaPjoLHa/HVLoy6pa0R14YBbU/wKAS67lwEu/ln04/isjEzwCD02djcpBlHEQapJiKUT274Jn8DaJqV0Wjz520I34r1Rsvd9jVvlT0yXH5jHuw5DLS7DgT1d/uEZjk/QXaV2qfV9e9CLHfv7HrO9hOhDE+ewhF7t+2QTBZji7kw8xd+ITTXY7/rI8aTXIv5tbBhYzb+asS0p61t/MhaV1mREibm5s/ABzh3P8JwYtIhxk2b5jTscpIv90LxinbShdNdfGov9recGWFFCEzrgbR8oxMxI5ByXL2SVTFFK2YsHt7wB9gHIpmD5wO5paNKztTY7URMvu1bY3I3HMLOj2lJnhEdr7u2mePvDohZz8vcfzyvevHPWSINq86aBp/Bonp/m+hqaO+xcx5DDg36YvjoXyJ6pyo86acO6BfJpDcB4+roM4+bwF86Q5nALkFNudHN5y4SCey7kQdMT59Wb6LXPZfDe9ofk8Mxs/tU216eKbw5TSiNb2fttzCUHnLivVKp+TyjWxYTXoK+Y5TpXiFBDZqzDhH4GpZYx7pP5n4I13RRHqCnCGh9ocTJJ/1ClYNsc+ENQYtSM/amsTGANRRSocraJMdufyC1LC9H0TdDenSWU0aObLSbRKsJOR5UOFG+7XGAE='

describe('narration signatures (3.24 T13n)', () => {
  test("T2's real narration signature reads narration; an ordinary thinking block's does not", async () => {
    expect(isNarration(SIG_NARRATION)).toBe(true)
    expect(isNarration(SIG_THINKING)).toBe(false)
  })

  test('a missing, malformed or truncated signature is no narration', async () => {
    expect(isNarration(undefined)).toBe(false)
    expect(isNarration('')).toBe(false)
    expect(isNarration('not base64 at all!')).toBe(false)
    expect(isNarration('////')).toBe(false)
    expect(isNarration(SIG_NARRATION.slice(0, 40))).toBe(false)
  })
})

describe('tools', () => {
  test('validateInput, good and bad, every tool', async () => {
    expect(validateInput('plan', { path: 'plan.md' })).toBe(null)
    expect(validateInput('plan', { path: 'plan.md', title: 'T' })).toBe(null)
    expect(validateInput('plan', {})).toBe('path is required')
    expect(validateInput('plan', { path: 'p', title: 3 })).toBe('title must be a string')
    expect(validateInput('plan', { path: 'p', kind: 'phase', rules: ['M13: r'], candidates: [] })).toBe(null)
    expect(validateInput('plan', { path: 'p', kind: 'draft' })).toBe('kind must be generation, phase or adhoc')
    expect(validateInput('plan', { path: 'p', rules: 'M13' })).toBe('rules must be an array of strings')
    expect(validateInput('plan', { path: 'p', candidates: [3] })).toBe('candidates must be an array of strings')
    expect(validateInput('ask', { question: 'q?', options: ['a', 'b'] })).toBe(null)
    expect(validateInput('ask', { question: 'q?', options: ['a', 'b', 'c', 'd'] })).toBe(null)
    expect(validateInput('ask', { question: 'q?', options: ['a'] })).toBe('options must be 2-4 strings')
    expect(validateInput('ask', { question: 'q?', options: ['a', 'b', 'c', 'd', 'e'] })).toBe('options must be 2-4 strings')
    expect(validateInput('ask', { question: 'q?', options: ['a', 2] })).toBe('options must be non-empty strings')
    expect(validateInput('ask', { options: ['a', 'b'] })).toBe('question is required')
    expect(validateInput('topic', { summary: 'new' })).toBe(null)
    expect(validateInput('topic', {})).toBe(null)
    expect(validateInput('topic', { summary: 1 })).toBe('summary must be a string')
    expect(validateInput('release', { hold: 'question', note: 'go' })).toBe(null)
    expect(validateInput('release', { hold: 'nap' })).toBe('hold must be talk, review, question, close, stopped, generation or session')
    expect(validateInput('release', { reason: 'done' })).toBe('hold is required')
    expect(validateInput('plan_edit', { command: 'add T9', why: 'agreed' })).toBe(null)
    expect(validateInput('plan_edit', { command: ' ' })).toBe('command is empty')
    expect(validateInput('plan_edit', { why: 'x' })).toBe('command is required')
    expect(validateInput('steer', { task: 'T1', text: 'call it plus' })).toBe(null)
    expect(validateInput('steer', { task: 'T1' })).toBe('text is required')
    expect(validateInput('steer', { text: 'x' })).toBe('task is required')
    expect(validateInput('plan', null)).toBe('input must be an object')
    expect(validateInput('nope', {})).toBe('unknown tool nope')
  })

  test('request naming (the router\'s vectors) and replies', async () => {
    expect(requestName(1700000000000, 2, 'plan_edit')).toBe('1700000000000-2-plan_edit.json')
    expect(() => requestName(1, 1, 'Bad Kind')).toThrow('bad request kind')
    expect(queuedReply('plan', '5-1-plan')).toBe('queued plan 5-1-plan; the answer arrives later as a message — end your turn now')
    expect(queuedReply('release', '5-2-release')).toBe('queued release 5-2-release; a message follows only if it is refused or changes the outcome — end your turn now')
    expect(queuedReply('topic', '5-3-topic')).toBe('queued topic 5-3-topic; no reply will follow')
    expect(argSummary({ command: 'x'.repeat(300) }).length).toBe(120)
  })
})

describe('journal (copy of the router\'s naming)', () => {
  test('entry paths', async () => {
    expect(entryPath('2026-10-07', '143005', 7, 'r', 'dev', 'msg')).toBe('.claude-state/journal/2026-10-07/143005-007r-dev-msg.md')
    expect(entryPath('2026-10-07', '143005', 8, 'w', 'partner', 'thinking')).toBe('.claude-state/journal/2026-10-07/143005-008w-partner-thinking.md')
  })

  test('frontmatter and who', async () => {
    expect(entryText({ ts: 't', agentId: 'A1' }, 'body')).toBe('---\nts: t\nagentId: A1\n---\nbody\n')
    expect(whoOf('pa-claude', 'A1', '')).toBe('claude') // 3.22 T8g
    expect(whoOf('claude', 'A1', '')).toBe('coder') // the built-in catch-all is never the partner
    expect(whoOf('partner', 'A1', '')).toBe('claude')
    expect(whoOf('thinker', 'A1', '')).toBe('thinker')
    expect(whoOf('deep-thinker', 'A1', '')).toBe('thinker')
    expect([whoOf('pa-thinker', 'A1', ''), whoOf('pa-expert', 'A1', ''), whoOf('pa-coder', 'A1', '')]).toEqual(['thinker', 'expert', 'coder']) // 3.25 T6b
    expect(whoOf('coder', 'A1', '')).toBe('coder')
    expect(whoOf('retriever-code', 'A1', 'A1')).toBe('retriever') // 3.24 T13f: by role, not every other type `coder`
    expect(whoOf('retriever-digest', 'A1', '')).toBe('retriever')
    expect(whoOf('coder-opus55', 'A1', '')).toBe('coder')
    expect(whoOf('expert-opus55', 'A1', '', 'T2 real-pipe transcripts')).toBe('expert')
    expect(whoOf('expert-fable', 'A1', '')).toBe('expert')
    expect(whoOf('memory-curator', 'A1', '')).toBe('curator')
    expect(whoOf('auditor', 'A1', '')).toBe('auditor')
    expect(whoOf('planner-phase', 'A1', '')).toBe('planner')
    expect(whoOf('pa-planner', 'A1', '')).toBe('planner') // 3.24 T17b
    expect(whoOf('', 'A1', 'A1')).toBe('claude')
    expect(whoOf('', 'A1', 'A2')).toBe('coder')
    expect(partnerIdOf('{"partner":{"agentId":"A1"}}')).toBe('A1')
    expect(partnerIdOf('{"partner":null}')).toBe('')
    expect(partnerIdOf(null)).toBe('')
    expect(displayWho('pa-claude')).toBe('Claude')
    expect(displayWho('claude')).toBe('claude')
    expect(displayWho('deep-thinker')).toBe('deep-thinker')
    expect(JSON.parse(stepFactsText('claude-opus-5-5', 'high', 51000, 7))).toEqual({ model: 'claude-opus-5-5', effort: 'high', ctx: 51000, at: 7 })
    expect(noteWho('expert-opus55', 'T12 Add plus')).toBe('T12')
    expect(noteWho('expert-fable-5m', 'phase end')).toBe('')
    expect(noteWho('coder-opus55', 'T1.c1')).toBe('coder')
    expect([noteWho('pa-expert', 'T12 Add plus'), noteWho('pa-coder', 'T1.c1')]).toEqual(['T12', 'coder']) // 3.25 T6b
    expect(noteWho('pa-claude', '')).toBe('')
    expect(noteWho('retriever-code', 'q')).toBe('')
    expect(noteLines('\n a \n\n b \n c')).toBe('a\nb')
  })
})

describe('health records (3.24 T6)', () => {
  test('releaseOf: the version: line, else the first line; empty when absent', async () => {
    expect(releaseOf('name: pa3\r\nversion: 3.24.0 \n')).toBe('3.24.0')
    expect(releaseOf(' 3.24.1\nmore\n')).toBe('3.24.1')
    expect(releaseOf(null)).toBe('')
    expect(releaseOf('')).toBe('')
  })
  test('heartbeatText and errorText: the fields pa/health.py reads, at in seconds, the error clipped', async () => {
    expect(JSON.parse(heartbeatText('S1', '3.24.0', '2.1.293', 1_500, 'pa'))).toEqual({ sid: 'S1', version: '3.24.0', engine: '2.1.293', at: 1.5, plugin: 'pa' })
    const e = JSON.parse(errorText('S1', 'pa-watch', 'session.start', 'x'.repeat(600), 2_000)) as Record<string, unknown>
    expect({ ...e, error: (e['error'] as string).length }).toEqual({ sid: 'S1', plugin: 'pa-watch', hook: 'session.start', error: 500, at: 2 })
  })
})

describe('3.24 T17g: start lines', () => {
  test('the thinkers, planners, coders, the curator and the auditor get a start line; experts and retrievers do not', async () => {
    for (const t of ['pa-thinker', 'pa-coder', 'thinker', 'deep-thinker', 'pa-planner', 'planner-phase', 'coder-opus55', 'memory-curator', 'auditor']) {
      expect(startsLine(t)).toBe(true)
    }
    for (const t of ['pa-expert', 'expert-opus55', 'retriever-code', 'pa-claude', '']) {
      expect(startsLine(t)).toBe(false)
    }
    expect(startText(' Review 5.2 plan for weak spots ')).toBe('Starting: Review 5.2 plan for weak spots')
    expect(startText('')).toBe('Starting')
  })
})
