import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { apiClient } from '../../api/client'
import type { components } from '../../api/schema'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OpsShell,
  StatePill,
} from './shared'
import { formatMoney, formatQuantity, requireData, shortId } from './utils'

type Condition = components['schemas']['StockCondition']

export function InventoryPage() {
  const [warehouseId, setWarehouseId] = useState('')
  const [locationId, setLocationId] = useState('')
  const [condition, setCondition] = useState<Condition | ''>('')

  const reference = useQuery({
    queryKey: ['ops', 'inventory', 'reference'],
    queryFn: async () => {
      const [warehouses, products, positions] = await Promise.all([
        apiClient.GET('/v1/warehouses', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/products', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/inventory/positions', { params: { query: { limit: 250 } } }),
      ])
      return {
        warehouses: requireData(warehouses, 'Warehouses could not be loaded').items,
        products: requireData(products, 'Products could not be loaded').items,
        positions: requireData(positions, 'Inventory locations could not be loaded').items,
      }
    },
  })

  const inventory = useQuery({
    queryKey: ['ops', 'inventory', 'positions', warehouseId, locationId, condition],
    queryFn: async () => {
      const [positions, summary] = await Promise.all([
        apiClient.GET('/v1/inventory/positions', {
          params: {
            query: {
              warehouse_id: warehouseId || undefined,
              bin_id: locationId || undefined,
              condition: condition || undefined,
              limit: 250,
            },
          },
        }),
        apiClient.GET('/v1/reports/stock-summary', {
          params: {
            query: {
              warehouse_id: warehouseId || undefined,
              condition: condition || undefined,
            },
          },
        }),
      ])
      return {
        positions: requireData(positions, 'Inventory positions could not be loaded').items,
        summary: requireData(summary, 'Inventory summary could not be loaded').items,
      }
    },
  })

  const warehouseNames = useMemo(
    () => new Map(reference.data?.warehouses.map((item) => [item.id, item.code]) ?? []),
    [reference.data],
  )
  const productNames = useMemo(
    () => new Map(reference.data?.products.map((item) => [item.id, item]) ?? []),
    [reference.data],
  )
  const locations = useMemo(() => {
    const values = reference.data?.positions
      .filter((item) => !warehouseId || item.warehouse_id === warehouseId)
      .map((item) => item.location_id) ?? []
    return [...new Set(values)].sort()
  }, [reference.data, warehouseId])
  const incoming = useMemo(() => {
    const result = new Map<string, number>()
    for (const item of inventory.data?.summary ?? []) {
      const key = [item.product_id, item.warehouse_id, item.condition, item.uom].join(':')
      result.set(key, Number(item.incoming))
    }
    return result
  }, [inventory.data])
  const totals = useMemo(() => {
    return (inventory.data?.positions ?? []).reduce(
      (sum, item) => ({
        onHand: sum.onHand + Number(item.on_hand),
        reserved: sum.reserved + Number(item.reserved),
        available: sum.available + Number(item.available),
        value: sum.value + Number(item.inventory_value),
      }),
      { onHand: 0, reserved: 0, available: 0, value: 0 },
    )
  }, [inventory.data])

  const loading = reference.isLoading || inventory.isLoading
  const error = reference.error || inventory.error

  return (
    <OpsShell
      eyebrow="Authoritative inventory"
      title="Inventory positions"
      actions={
        <button
          className="ops-button"
          onClick={() => void Promise.all([reference.refetch(), inventory.refetch()])}
          disabled={reference.isFetching || inventory.isFetching}
        >
          <RefreshCw size={15} /> Refresh
        </button>
      }
    >
      <div className="ops-toolbar">
        <label className="ops-field">
          <span>Warehouse</span>
          <select
            value={warehouseId}
            onChange={(event) => {
              setWarehouseId(event.target.value)
              setLocationId('')
            }}
          >
            <option value="">All warehouses</option>
            {reference.data?.warehouses.map((item) => (
              <option key={item.id} value={item.id}>{item.code} — {item.name}</option>
            ))}
          </select>
        </label>
        <label className="ops-field">
          <span>Bin / location</span>
          <select value={locationId} onChange={(event) => setLocationId(event.target.value)}>
            <option value="">All bins</option>
            {locations.map((id) => <option key={id} value={id}>{shortId(id)}</option>)}
          </select>
        </label>
        <label className="ops-field">
          <span>Condition</span>
          <select
            value={condition}
            onChange={(event) => setCondition(event.target.value as Condition | '')}
          >
            <option value="">All conditions</option>
            <option value="sellable">Sellable</option>
            <option value="quarantined">Quarantined</option>
            <option value="damaged">Damaged</option>
            <option value="expired">Expired</option>
          </select>
        </label>
      </div>

      <div className="ops-grid">
        <div className="ops-card"><span>On hand</span><strong>{formatQuantity(totals.onHand)}</strong></div>
        <div className="ops-card"><span>Reserved</span><strong>{formatQuantity(totals.reserved)}</strong></div>
        <div className="ops-card"><span>Available</span><strong>{formatQuantity(totals.available)}</strong></div>
        <div className="ops-card"><span>Inventory value</span><strong>{formatMoney(totals.value)}</strong></div>
      </div>

      {error && <ErrorState error={error} />}
      <section className="ops-panel">
        <div className="ops-panel-head">
          <h2>Position ledger projection</h2>
          <small>{inventory.data?.positions.length ?? 0} positions</small>
        </div>
        {loading ? <LoadingState /> : inventory.data?.positions.length ? (
          <div className="ops-table-wrap">
            <table className="ops-table">
              <thead>
                <tr>
                  <th>SKU / product</th>
                  <th>Warehouse</th>
                  <th>Bin</th>
                  <th>Condition</th>
                  <th>Lot / serial</th>
                  <th className="ops-number">On hand</th>
                  <th className="ops-number">Reserved</th>
                  <th className="ops-number">Available</th>
                  <th className="ops-number">Incoming</th>
                  <th className="ops-number">Value</th>
                </tr>
              </thead>
              <tbody>
                {inventory.data.positions.map((item) => {
                  const product = productNames.get(item.product_id)
                  const key = [item.product_id, item.warehouse_id, item.condition, item.uom].join(':')
                  return (
                    <tr key={[
                      item.product_id,
                      item.warehouse_id,
                      item.location_id,
                      item.condition,
                      item.lot_id,
                      item.serial_id,
                    ].join(':')}>
                      <td><strong>{product?.sku ?? shortId(item.product_id)}</strong><br /><small>{product?.name}</small></td>
                      <td>{warehouseNames.get(item.warehouse_id) ?? shortId(item.warehouse_id)}</td>
                      <td title={item.location_id}>{shortId(item.location_id)}</td>
                      <td><StatePill value={item.condition} /></td>
                      <td className="ops-muted">{item.lot_id ? `LOT ${shortId(item.lot_id)}` : item.serial_id ? `SN ${shortId(item.serial_id)}` : '—'}</td>
                      <td className="ops-number">{formatQuantity(item.on_hand)} {item.uom}</td>
                      <td className="ops-number">{formatQuantity(item.reserved)}</td>
                      <td className="ops-number">{formatQuantity(item.available)}</td>
                      <td className="ops-number">{formatQuantity(incoming.get(key) ?? 0)}</td>
                      <td className="ops-number">{formatMoney(item.inventory_value)}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        ) : <EmptyState>No inventory positions match these filters.</EmptyState>}
      </section>
    </OpsShell>
  )
}
