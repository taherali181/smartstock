export interface Product {
  id: string
  sku: string
  name: string
  baseUom: string
  category: string
  warehouse: string
  available: number
  committed: number
  incoming: number
  reorderAt: number
  price: number
  status: 'Healthy' | 'Low stock' | 'Out of stock' | 'Overstock'
  trend: number[]
  version: number
  updatedAt: string
}
