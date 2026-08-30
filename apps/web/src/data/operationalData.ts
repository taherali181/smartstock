import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../api/client'
import type { Product } from '../types'

function numberField(value: unknown, fallback = 0) {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return fallback
}

function stringField(value: unknown, fallback: string) {
  return typeof value === 'string' && value.trim() ? value : fallback
}

export function useOperationalProducts() {
  return useQuery({
    queryKey: ['phase-2-operational-products'],
    queryFn: async (): Promise<Product[]> => {
      const [productResult, positionResult, warehouseResult] = await Promise.all([
        apiClient.GET('/v1/products', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/inventory/positions', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/warehouses', { params: { query: { limit: 250 } } }),
      ])
      if (!productResult.data || !positionResult.data || !warehouseResult.data) {
        throw new Error('SmartStock operational records could not be loaded')
      }

      const warehouseNames = new Map(
        warehouseResult.data.items.map((warehouse) => [warehouse.id, warehouse.name]),
      )
      const aggregates = new Map<string, {
        available: number
        reserved: number
        onHand: number
        inventoryValue: number
        warehouses: Set<string>
        updatedAt: string
      }>()
      for (const position of positionResult.data.items) {
        const current = aggregates.get(position.product_id) ?? {
          available: 0,
          reserved: 0,
          onHand: 0,
          inventoryValue: 0,
          warehouses: new Set<string>(),
          updatedAt: position.updated_at,
        }
        current.available += numberField(position.available)
        current.reserved += numberField(position.reserved)
        current.onHand += numberField(position.on_hand)
        current.inventoryValue += numberField(position.inventory_value)
        current.warehouses.add(warehouseNames.get(position.warehouse_id) ?? position.warehouse_id)
        if (position.updated_at > current.updatedAt) current.updatedAt = position.updated_at
        aggregates.set(position.product_id, current)
      }

      return productResult.data.items.map((product) => {
        const position = aggregates.get(product.id)
        const available = position?.available ?? 0
        const reorderAt = numberField(product.custom_fields.reorder_at)
        const status: Product['status'] = available <= 0
          ? 'Out of stock'
          : reorderAt > 0 && available >= reorderAt * 4
            ? 'Overstock'
            : reorderAt > 0 && available <= reorderAt
              ? 'Low stock'
              : 'Healthy'
        return {
          id: product.id,
          sku: product.sku,
          name: product.name,
          baseUom: product.base_uom,
          category: stringField(product.custom_fields.category, 'Uncategorized'),
          warehouse: position?.warehouses.size
            ? [...position.warehouses].sort().join(', ')
            : 'No stock location',
          available,
          committed: position?.reserved ?? 0,
          incoming: 0,
          reorderAt,
          price: position?.onHand ? position.inventoryValue / position.onHand : 0,
          status,
          trend: [],
          version: product.version,
          updatedAt: position?.updatedAt ?? product.updated_at,
        }
      })
    },
  })
}
