import { describe, expect, test } from 'claude-code/testing'

import { CHAIN_SCRIPTS, parseAuditorReturn, parseCoderReturn, parseExpertContract, splitCommand } from './contract'
import { EDITS_LINES, EXPERT_RETURNS } from './contract.vectors'

const PY = 'C:/py/python.exe'

describe('expert contract', () => {
  test('the eight real returns: STATUS, LOG, COMMIT, MILESTONE', async () => {
    expect(EXPERT_RETURNS.length).toBe(8)
    for (const [src, status, text] of EXPERT_RETURNS) {
      const c = parseExpertContract(text)
      expect(c === null ? `null ${src}` : c.status).toBe(status)
      expect(c?.log.startsWith('phase-ends/current/') || c?.log.startsWith('none')).toBe(true)
      expect(c?.commit).toMatch(/^([0-9a-f]{7}|none)$/)
      expect(c?.milestone).toMatch(/^(n\/a|green)$/)
      expect(c?.recommended.length ?? 0).toBeGreaterThan(20)
      expect(c?.recommended.includes('```')).toBe(false)
    }
  })

  test('fields verbatim: milestone, backslashes, multi-field question', async () => {
    const green = parseExpertContract(EXPERT_RETURNS[1]?.[2] ?? '')
    expect([green?.ctx, green?.commit, green?.milestone]).toEqual(['~55k', '07bb04a', 'green'])
    const review = parseExpertContract(EXPERT_RETURNS[4]?.[2] ?? '')
    expect(review?.recommended).toContain('Z:\\Storage\\git\\pa3-calc\\phase-ends\\current\\REVIEW.md')
    const q = parseExpertContract(EXPERT_RETURNS[2]?.[2] ?? '')
    expect(q?.question.startsWith('The `## Interfaces` entry')).toBe(true)
    expect(q?.question.endsWith('unless registration is fixed first.')).toBe(true)
    expect(q?.recommended.endsWith('Then respawn T2.')).toBe(true)
    expect(q?.log).toBe('none (no code written; task not started beyond reading the plan and tasks/T1.md)')
    const lf = parseExpertContract(EXPERT_RETURNS[7]?.[2] ?? '')
    expect(lf?.recommended).toContain("`grep -c -U -P '\\r' FILE`")
    expect(lf?.question).toBe('')
  })

  test('a hand-back with prose before the block; the first block wins', async () => {
    const text = `Done; the gate is green.\n\n${EXPERT_RETURNS[0]?.[2] ?? ''}\n\nSTATUS: blocked\nLOG: x`
    const c = parseExpertContract(text)
    expect([c?.status, c?.log, c?.commit]).toEqual(['done', 'phase-ends/current/tasks/T2.md', 'fcfe40c'])
  })

  test('no STATUS, or STATUS past 2000 chars: null', async () => {
    expect(parseExpertContract('.')).toBe(null)
    expect(parseExpertContract('Coder T3.c1 is running the gate.')).toBe(null)
    expect(parseExpertContract(`${'x'.repeat(2001)}\nSTATUS: done`)).toBe(null)
    expect(parseExpertContract('status=Blocked')?.status).toBe('blocked')
  })

  // 3.25 T3: the plain hand-back.
  test('plain forms: the first word is the status, the rest the free text', async () => {
    expect([parseExpertContract('Done.')?.status, parseExpertContract('Done.')?.summary]).toEqual(['done', ''])
    const bold = parseExpertContract('**Done** — added x\n\nThe gate is green.')
    expect([bold?.status, bold?.summary]).toEqual(['done', 'added x\n\nThe gate is green.'])
    expect(parseExpertContract('done: x')?.summary).toBe('x')
    const q = parseExpertContract('Question: x?')
    expect([q?.status, q?.question, q?.recommended]).toEqual(['question', 'x?', ''])
    const b = parseExpertContract('Blocked — y')
    expect([b?.status, b?.summary]).toEqual(['blocked', 'y'])
    expect(parseCoderReturn('## Partial, the gate is red')?.status).toBe('partial')
  })

  test('plain: legacy STATUS still wins; no status word, `.`, blank and WAITING: are null', async () => {
    expect(parseExpertContract('Notes.\nSTATUS: done\nLOG: l')?.log).toBe('l')
    expect(parseExpertContract('I think it works')).toBe(null)
    expect(parseExpertContract('Doneness is near')).toBe(null)
    expect(parseExpertContract('.')).toBe(null)
    expect(parseExpertContract('  \n ')).toBe(null)
    expect(parseExpertContract('WAITING: C1 running')).toBe(null)
  })

  test('the commit: a COMMIT field, else a hash after "commit" on its line', async () => {
    expect(parseExpertContract('STATUS: done\nCOMMIT: def5678 (note)')?.commit).toBe('def5678')
    expect(parseCoderReturn('Done. Added x.\nCOMMIT: abc1234')?.commit).toBe('abc1234')
    expect(parseCoderReturn('Done, see commit abc1234 for it.')?.commit).toBe('abc1234')
    expect(parseExpertContract('Done; committed as 0123abcd.')?.commit).toBe('0123abcd')
    expect(parseExpertContract('Done. abc1234 is the hash.')?.commit).toBe('')
  })
})

describe('auditor return', () => {
  test('the agents/auditor.md shape', async () => {
    const a = parseAuditorReturn(
      'STATUS: done\nVERDICTS: 4 (1 tool · 1 split · 0 guard · 1 script-fix · 1 leave)\nFIXED: 1 (ab12cd3)\nCANDIDATES: 1 tool · 0 rule\nFILE: phase-ends/current/AUDIT.md\n',
    )
    expect(a).toEqual({
      status: 'done', verdicts: '4 (1 tool · 1 split · 0 guard · 1 script-fix · 1 leave)', fixed: '1 (ab12cd3)',
      candidates: '1 tool · 0 rule', file: 'phase-ends/current/AUDIT.md', recommended: '',
    })
    expect(parseAuditorReturn('```\nSTATUS: blocked\nRECOMMENDED: rerun audit\n```')?.recommended).toBe('rerun audit')
    expect(parseAuditorReturn('no contract')).toBe(null)
  })
})

describe('splitCommand', () => {
  test('the real critic EDITS line: quoted ; | && $( are literal; python and PY alike', async () => {
    const [, critic] = EDITS_LINES[0] ?? ['', '']
    expect(splitCommand(critic, PY)).toEqual(splitCommand(critic.replace(/^python /, 'PY '), PY))
    const r = splitCommand(critic.replace(/^python /, 'PY '), PY)
    if (!('argv' in r)) {
      throw new Error(r.refused)
    }
    expect(r.argv.length).toBe(27)
    expect(r.argv.slice(0, 6)).toEqual([PY, 'tools/plan_edit.py', 'add-task', '--after', 'T3', '--title'])
    expect(r.argv[6]).toBe("README.md:39 --version sample calc 1.1.0 -> calc 1.2.0 (the release's README sample per the 2.2 version-source decision)")
    const verify = r.argv[r.argv.indexOf('--verify') + 1] ?? ''
    expect(verify.startsWith(`test "$(grep -A1 'python -m calc --version$' README.md|tail -1)" = 'calc 1.2.0' && `)).toBe(true)
    expect(verify).toContain("tr -d '\\r'")
    expect(verify).toContain("grep -c $'\\r' README.md")
    expect(r.argv.slice(-2)).toEqual(['--by', 'critic'])
  })

  test('the router chain and expert lines', async () => {
    expect(splitCommand(EDITS_LINES[1]?.[1] ?? '', PY)).toEqual({ refused: '&& outside quotes' })
    expect(splitCommand(EDITS_LINES[2]?.[1] ?? '', PY)).toEqual({
      argv: [PY, 'tools/plan_edit.py', 'set-status', 'T3', 'done', '--by', 'developer'],
    })
    expect(splitCommand((EDITS_LINES[3]?.[1] ?? '').replace(/^python /, 'PY '), PY)).toEqual({
      argv: [PY, 'tools/plan_edit.py', 'set-status', 'T2', 'done', '--by', 'developer'],
    })
    expect(splitCommand(EDITS_LINES[4]?.[1] ?? '', PY)).toEqual({
      argv: [PY, 'tools/plan_edit.py', 'add-task', '--title', 'tool candidate <name>: <does>', '--done-when',
        "<the candidate's replaces line, as an observable>", '--coder', 'opus55', '--by', 'developer'],
    })
    expect(splitCommand(EDITS_LINES[5]?.[1] ?? '', PY)).toEqual({
      argv: [PY, 'tools/plan_edit.py', 'append-change', "candidate <name> dropped: <the developer's words>"],
    })
  })

  test('the close chain allows genend_index.py (CHAIN_SCRIPTS); a plan_edit does not (3.23 T6b)', async () => {
    const line = 'C:/Py/python.exe tools/genend_index.py assemble 1'
    expect(splitCommand(line, PY, CHAIN_SCRIPTS)).toEqual({ argv: [PY, 'tools/genend_index.py', 'assemble', '1'] })
    expect(splitCommand('python tools/plan_edit.py gen-close 1.1 --by router', PY, CHAIN_SCRIPTS))
      .toEqual({ argv: [PY, 'tools/plan_edit.py', 'gen-close', '1.1', '--by', 'router'] })
    expect(splitCommand(line, PY)).toEqual({ refused: 'not an allowed script: tools/genend_index.py' })
    expect(splitCommand('PY tools/status.py clear', PY, CHAIN_SCRIPTS)).toEqual({ refused: 'not an allowed script: tools/status.py' })
  })

  test('refusals', async () => {
    expect(splitCommand('PY tools/plan_edit.py show; rm -rf x', PY)).toEqual({ refused: '; outside quotes' })
    expect(splitCommand('PY tools/plan_edit.py show | tee x', PY)).toEqual({ refused: '| outside quotes' })
    expect(splitCommand('PY tools/plan_edit.py show > x', PY)).toEqual({ refused: '> outside quotes' })
    expect(splitCommand('PY tools/plan_edit.py show < x', PY)).toEqual({ refused: '< outside quotes' })
    expect(splitCommand('PY tools/plan_edit.py append-change "$(whoami)"', PY)).toEqual({ refused: '$( command substitution' })
    expect(splitCommand('PY tools/plan_edit.py append-change $(whoami)', PY)).toEqual({ refused: '$( command substitution' })
    expect(splitCommand('PY tools/plan_edit.py append-change "a `b`"', PY)).toEqual({ refused: 'a backtick outside single quotes' })
    expect(splitCommand('PY tools/plan_edit.py append-change "open', PY)).toEqual({ refused: 'an unterminated quote' })
    expect(splitCommand('PY tools/status.py set', PY)).toEqual({ refused: 'not an allowed script: tools/status.py' })
    expect(splitCommand('bash -c "PY tools/plan_edit.py"', PY)).toEqual({ refused: 'bash runs nothing here: only tools/plan_edit.py or tools/rules_add.py' })
    expect(splitCommand('python tools/status.py set', PY)).toEqual({ refused: 'not an allowed script: tools/status.py' })
    expect(splitCommand('python -c "import os"', PY)).toEqual({ refused: 'not an allowed script: -c' })
    expect(splitCommand('tools/other.py x', PY)).toEqual({ refused: 'not an allowed script: tools/other.py' })
    expect(splitCommand('   ', PY)).toEqual({ refused: 'an empty command' })
    expect(splitCommand('add-task --title "x; y" && rm x', PY)).toEqual({ refused: '&& outside quotes' })
    expect(splitCommand('PY tools/plan_edit.py show', '')).toEqual({ refused: 'no python in pa.json' })
  })

  test('command forms: PY, any interpreter, the script first, bare args; rules_add only when named', async () => {
    const want = { argv: [PY, 'tools/plan_edit.py', 'add-task', '--title', 'a b', '--by', 'developer'] }
    const tail = 'add-task --title "a b" --by developer'
    for (const head of ['PY tools/plan_edit.py', 'python tools/plan_edit.py', 'python3 tools/plan_edit.py', 'py tools/plan_edit.py',
      'C:/Users/u/AppData/Local/Programs/Python/Python314/python.exe tools/plan_edit.py',
      '"C:\\Program Files\\Python\\python.exe" "tools\\plan_edit.py"', '/usr/bin/python3.14 ./tools/plan_edit.py',
      'tools/plan_edit.py', './tools/plan_edit.py', 'plan_edit.py', '']) {
      expect(splitCommand(`${head} ${tail}`.trim(), PY)).toEqual(want)
    }
    expect(splitCommand('python tools/rules_add.py promote x', PY)).toEqual({ argv: [PY, 'tools/rules_add.py', 'promote', 'x'] })
    expect(splitCommand('tools/rules_add.py promote x', PY)).toEqual({ argv: [PY, 'tools/rules_add.py', 'promote', 'x'] })
    expect(splitCommand('promote x', PY)).toEqual({ argv: [PY, 'tools/plan_edit.py', 'promote', 'x'] })
  })

  test('quoting: single quotes literal, backslashes, rules_add', async () => {
    expect(splitCommand("PY tools/rules_add.py promote 'a `b` $(c) ; d' \"e \\\"f\\\" \\$g\" h\\ i", PY)).toEqual({
      argv: [PY, 'tools/rules_add.py', 'promote', 'a `b` $(c) ; d', 'e "f" $g', 'h i'],
    })
    expect(splitCommand("PY tools/plan_edit.py append-change \"C:\\dir\\x\" ''", PY)).toEqual({
      argv: [PY, 'tools/plan_edit.py', 'append-change', 'C:\\dir\\x', ''],
    })
  })
})
