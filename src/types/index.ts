export type View = 'overview' | 'inventory' | 'forecasting' | 'assistant' | 'orders' | 'purchasing' | 'warehouses'

export interface Product {
  sku: string
  name: string
  category: string
  warehouse: string
  available: number
  committed: number
  incoming: number
  reorderAt: number
  price: number
  status: 'Healthy' | 'Low stock' | 'Out of stock' | 'Overstock'
  trend: number[]
}

export interface Activity {
  id: number
  title: string
  detail: string
  time: string
  tone: 'accent' | 'warning' | 'muted'
}
