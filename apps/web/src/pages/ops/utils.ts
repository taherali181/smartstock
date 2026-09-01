export const CURRENT_USER = '00000000-0000-0000-0000-000000000001'

export function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message
  if (typeof error === 'string') return error
  if (error && typeof error === 'object') {
    const value = error as Record<string, unknown>
    if (typeof value.detail === 'string') return value.detail
    if (Array.isArray(value.detail)) {
      return value.detail
        .map((item) => {
          if (item && typeof item === 'object' && 'msg' in item) return String(item.msg)
          return String(item)
        })
        .join('; ')
    }
    if (typeof value.title === 'string') return value.title
  }
  return 'The operation could not be completed.'
}

export function requireData<T>(
  result: { data?: T; error?: unknown },
  fallback: string,
): T {
  if (result.data !== undefined) return result.data
  const message = errorMessage(result.error)
  throw new Error(message === 'The operation could not be completed.' ? fallback : message)
}

export function formatQuantity(value: string | number | null | undefined) {
  const amount = Number(value ?? 0)
  return Number.isFinite(amount)
    ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 3 }).format(amount)
    : String(value ?? '0')
}

export function formatMoney(value: string | number | null | undefined, currency = 'USD') {
  const amount = Number(value ?? 0)
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(Number.isFinite(amount) ? amount : 0)
}

export function shortId(value: string | null | undefined) {
  return value ? value.slice(0, 8) : '—'
}
