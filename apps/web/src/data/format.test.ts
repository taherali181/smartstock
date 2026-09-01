import { describe, expect, it } from 'vitest'
import { quantityText } from './format'

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
