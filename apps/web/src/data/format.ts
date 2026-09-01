/** Exact decimal text without trailing-zero noise.
 *
 * The ledger stores nine decimal places, so a quantity of 1 arrives as
 * "1.000000000". Trailing zeros are trimmed for display; nothing is rounded and
 * a genuinely fractional value keeps every significant digit. Values stay
 * strings end to end so no quantity ever passes through a float.
 */
export function quantityText(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return ''
  let text = String(value)
  if (!/^-?\d+(\.\d+)?$/.test(text)) return text
  if (text.includes('.')) text = text.replace(/0+$/, '').replace(/\.$/, '')
  return text || '0'
}
