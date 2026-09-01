import { describe, expect, it } from 'vitest'
import { enumLabel, quantityText } from './format'

describe('quantityText', () => {
  it('trims the ledger trailing zeros without rounding', () => {
    expect(quantityText('1.000000000')).toBe('1')
    expect(quantityText('5.000000000')).toBe('5')
    expect(quantityText('142.250000000')).toBe('142.25')
  })

  it('keeps every significant digit of a genuine fraction', () => {
    expect(quantityText('0.000000001')).toBe('0.000000001')
    expect(quantityText('0.5')).toBe('0.5')
  })

  it('handles zero and negatives', () => {
    expect(quantityText('0.000000000')).toBe('0')
    expect(quantityText('-3.500')).toBe('-3.5')
  })

  it('passes through anything that is not a plain decimal', () => {
    expect(quantityText('')).toBe('')
    expect(quantityText(null)).toBe('')
    expect(quantityText(undefined)).toBe('')
    expect(quantityText('1e5')).toBe('1e5')
  })
})

describe('enumLabel', () => {
  it('replaces underscores, which rendered task states as In_progress', () => {
    expect(enumLabel('in_progress')).toBe('In Progress')
    expect(enumLabel('partially_received')).toBe('Partially Received')
  })

  it('replaces every hyphen, not just the first', () => {
    expect(enumLabel('report-exception')).toBe('Report Exception')
    expect(enumLabel('a-b-c')).toBe('A B C')
  })

  it('leaves a plain word alone apart from capitalising it', () => {
    expect(enumLabel('open')).toBe('Open')
    expect(enumLabel('count')).toBe('Count')
  })
})
