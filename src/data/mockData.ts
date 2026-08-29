import type { Activity, Product } from '../types'

export const products: Product[] = [
  { sku: 'AUR-1042', name: 'Auralite Desk Lamp', category: 'Lighting', warehouse: 'Brooklyn Hub', available: 184, committed: 24, incoming: 120, reorderAt: 80, price: 89, status: 'Healthy', trend: [7, 10, 8, 13, 12, 17, 15] },
  { sku: 'NEX-2088', name: 'Nexus Cable Kit', category: 'Accessories', warehouse: 'Brooklyn Hub', available: 26, committed: 18, incoming: 80, reorderAt: 45, price: 34, status: 'Low stock', trend: [17, 15, 13, 11, 8, 6, 4] },
  { sku: 'VLT-3031', name: 'Volt Travel Adapter', category: 'Power', warehouse: 'Austin Central', available: 0, committed: 12, incoming: 200, reorderAt: 60, price: 42, status: 'Out of stock', trend: [16, 13, 9, 7, 3, 1, 0] },
  { sku: 'FRM-4410', name: 'Form Mechanical Keys', category: 'Peripherals', warehouse: 'Reno West', available: 612, committed: 43, incoming: 0, reorderAt: 120, price: 118, status: 'Overstock', trend: [9, 10, 10, 8, 8, 7, 7] },
  { sku: 'PLS-5507', name: 'Pulse USB-C Hub', category: 'Accessories', warehouse: 'Austin Central', available: 94, committed: 31, incoming: 60, reorderAt: 65, price: 74, status: 'Healthy', trend: [8, 9, 12, 11, 14, 16, 18] },
  { sku: 'ARC-6612', name: 'Arc Monitor Stand', category: 'Workspace', warehouse: 'Reno West', available: 43, committed: 14, incoming: 48, reorderAt: 50, price: 149, status: 'Low stock', trend: [14, 12, 12, 10, 8, 7, 5] },
]

export const activities: Activity[] = [
  { id: 1, title: 'Reorder window approaching', detail: 'Nexus Cable Kit · 9 days of cover', time: '4 min', tone: 'warning' },
  { id: 2, title: 'Purchase order received', detail: 'PO-2048 · 120 units checked in', time: '28 min', tone: 'accent' },
  { id: 3, title: 'Demand anomaly detected', detail: 'Pulse USB-C Hub · +31% velocity', time: '1 hr', tone: 'warning' },
  { id: 4, title: 'Shopify sync complete', detail: '246 orders · 0 conflicts', time: '2 hr', tone: 'muted' },
]

export const forecast = [
  { day: '01', actual: 42, predicted: 46 },
  { day: '05', actual: 51, predicted: 49 },
  { day: '09', actual: 47, predicted: 55 },
  { day: '13', actual: 62, predicted: 61 },
  { day: '17', actual: 59, predicted: 68 },
  { day: '21', actual: 76, predicted: 74 },
  { day: '25', actual: 72, predicted: 81 },
  { day: '29', actual: 88, predicted: 89 },
]
