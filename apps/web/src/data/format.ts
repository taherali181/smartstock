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

/** Human label for an API enum value such as `in_progress` or `report-exception`.
 *
 * Both separators are replaced, globally: `replace('-', ' ')` only substitutes
 * the first occurrence and leaves underscores alone, which rendered warehouse
 * task states as "In_progress".
 */
export function enumLabel(value: string): string {
  return value
    .replace(/[-_]+/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}
