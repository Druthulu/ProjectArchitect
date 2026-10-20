// 3.29.9: the display trim's pure parts (trim.ts): the cut point a mark finds, the request's instructions, a failure.
import { describe, expect, test } from 'claude-code/testing'

import { anchorOf, cutPoint, fingerprint, markOf, tailOf, trimFailure, trimInstructions } from './trim'

const row = (role: 'user' | 'assistant', text: string) => ({ role, text, toolUses: [] })
const R = [row('user', 'a'), row('assistant', 'b'), row('user', 'c'), row('assistant', 'd'), row('user', 'e'), row('assistant', 'f')]

describe('display trim (3.29.9)', () => {
  test('a fingerprint tells role, text and tool blocks apart', () => {
    expect(fingerprint(row('user', 'a'))).not.toBe(fingerprint(row('assistant', 'a')))
    expect(fingerprint(row('user', 'a'))).not.toBe(fingerprint(row('user', 'b')))
    expect(fingerprint({ role: 'user', text: '', toolResults: [{}] })).not.toBe(fingerprint(row('user', '')))
    expect(tailOf(R, 2)).toEqual([fingerprint(R[0]!), fingerprint(R[1]!)])
  })

  test('the exact place, else the latest earlier place, past assistant rows to a user row', () => {
    expect(cutPoint(R, { len: 4, tail: tailOf(R, 4) })).toBe(4)
    // a row gone before it: the place moved back one
    expect(cutPoint(R.slice(1), { len: 4, tail: tailOf(R, 4) })).toBe(3)
    // an assistant row at the cut: the next user row
    expect(cutPoint(R, { len: 3, tail: tailOf(R, 3) })).toBe(4)
  })

  test('no place, nothing to drop or nothing kept: -1', () => {
    expect(cutPoint(R.slice(4), { len: 4, tail: tailOf(R, 4) })).toBe(-1) // the mark's rows are gone (trimmed)
    expect(cutPoint(R, { len: 0, tail: [] })).toBe(-1)
    expect(cutPoint(R, { len: 6, tail: tailOf(R, 6) })).toBe(-1)
    expect(cutPoint(R, { len: 5, tail: tailOf(R, 5) })).toBe(-1) // only an assistant row after it
    expect(cutPoint(R.slice(1), { len: 2, tail: [] })).toBe(-1) // an empty tail matches only at its exact place
  })

  // H1.c2: a reload gives other rows (texts a few chars shorter, a hand-back row the live list lacked): the anchor finds it.
  const HB = 'Another Claude session sent a message: <agent-message from="E1">done</agent-message>'
  const NOTE1 = '<task-notification>\n<task-id>f1</task-id>\n{"n": 7}\n</task-notification>'
  const LIVE = [row('user', 'hi'), row('assistant', 'Sent. '), row('user', NOTE1), row('assistant', 'card one\n\n'), row('assistant', 'card two  ')]
  const RELOADED = [row('user', 'hi'), row('assistant', 'Sent.'), row('user', NOTE1.replace(/\n/g, ' ')), row('assistant', 'card one'),
    row('user', HB), row('assistant', 'card two'), row('user', 'next'), row('assistant', 'ok'), row('user', 'more'), row('assistant', 'fine')]
  const LMARK = { len: LIVE.length, tail: tailOf(LIVE, LIVE.length), anchor: anchorOf(LIVE, LIVE.length) }

  test('the anchor: the last user row before the place that is no hand-back, whitespace removed', () => {
    expect(anchorOf(LIVE, LIVE.length)).toBe(NOTE1.replace(/\s+/g, ''))
    expect(anchorOf([...LIVE, row('user', HB)], LIVE.length + 1)).toBe(NOTE1.replace(/\s+/g, ''))
    expect(anchorOf([row('assistant', 'x')], 1)).toBe('')
    expect(anchorOf([row('user', 'x'.repeat(5_000))], 1).length).toBe(4_000)
  })

  test('a reload-shaped list: no tail matches; the cut is just after the anchor, past assistant rows', () => {
    expect(cutPoint(RELOADED, { len: LMARK.len, tail: LMARK.tail })).toBe(-1)
    expect(cutPoint(RELOADED, LMARK)).toBe(4) // after NOTE1 (2), past `card one`: the hand-back row that followed it
  })

  test('the anchor\'s last occurrence counts; no match is -1; an old mark without one still cuts by its tail', () => {
    const twice = [...RELOADED.slice(0, 8), row('user', NOTE1), row('assistant', 'again'), row('user', 'last')]
    expect(cutPoint(twice, LMARK)).toBe(10)
    expect(cutPoint(RELOADED, { ...LMARK, anchor: 'nothinglikeit' })).toBe(-1)
    expect(markOf({ sid: 'S1', task: 'T1', len: 4, tail: tailOf(R, 4) })).toEqual({ sid: 'S1', task: 'T1', len: 4, tail: tailOf(R, 4) })
    expect(cutPoint(R, markOf({ sid: 'S1', task: 'T1', len: 4, tail: tailOf(R, 4) })!)).toBe(4)
  })

  test('the instructions carry the cut and the rows before it; a mark is read loosely', () => {
    expect(trimInstructions(R, 4)).toBe(`pa-trim ${JSON.stringify({ k: 4, tail: tailOf(R, 4) })}`)
    expect(markOf({ sid: 'S1', task: 'T1', len: 4, tail: ['x', 3] })).toEqual({ sid: 'S1', task: 'T1', len: 4, tail: ['x'] })
    expect([markOf(null), markOf({ sid: 'S1', task: 'T1' }), markOf('x')]).toEqual([null, null, null])
  })

  test('a headless refusal skips; another retries until the last attempt', () => {
    const headless = new Error('session.compact is not available in a headless (-p / SDK) session yet')
    expect(trimFailure(headless, 1, 15)).toBe('headless')
    expect(trimFailure(new Error('a turn is running'), 1, 15)).toBe('retry')
    expect(trimFailure(new Error('a turn is running'), 15, 15)).toBe('giveup')
  })
})
