import { useMemo, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, RefreshCw } from 'lucide-react'
import { apiClient, commandHeaders } from '../../api/client'
import type { components } from '../../api/schema'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OpsShell,
  StatePill,
} from './shared'
import {
  errorMessage,
  formatMoney,
  formatQuantity,
  requireData,
  shortId,
} from './utils'

type Order = components['schemas']['OrderResponse']
type OrderKind = components['schemas']['OrderKind']

type AllocationSnapshot = {
  orderLineId: string
  reservationId: string
  expectedPositionVersion: number
}

const purchaseCommands: Record<string, { command: string; label: string }> = {
  draft: { command: 'submit', label: 'Submit for approval' },
  pending_approval: { command: 'approve', label: 'Approve' },
  approved: { command: 'send', label: 'Send to supplier' },
  sent: { command: 'acknowledge', label: 'Acknowledge' },
}

const salesCommands: Record<string, { command: string; label: string }> = {
  quote: { command: 'convert-to-draft', label: 'Convert to draft' },
  draft: { command: 'confirm', label: 'Confirm order' },
  allocated: { command: 'start-picking', label: 'Start picking' },
  partially_allocated: { command: 'start-picking', label: 'Start picking' },
}

export function OrdersPage() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState<OrderKind>('purchase')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  const [kind, setKind] = useState<OrderKind>('purchase')
  const [orderNumber, setOrderNumber] = useState('')
  const [partyId, setPartyId] = useState('')
  const [warehouseId, setWarehouseId] = useState('')
  const [productId, setProductId] = useState('')
  const [quantity, setQuantity] = useState('10')
  const [unitPrice, setUnitPrice] = useState('1.00')
  const [allocationQuantity, setAllocationQuantity] = useState('')
  const [allocationByOrder, setAllocationByOrder] = useState<Record<string, AllocationSnapshot>>({})
  const [notice, setNotice] = useState<string | null>(null)

  const data = useQuery({
    queryKey: ['ops', 'orders'],
    queryFn: async () => {
      const [purchase, sales, products, suppliers, customers, warehouses, positions] =
        await Promise.all([
          apiClient.GET('/v1/purchase-orders', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/sales-orders', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/products', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/suppliers', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/customers', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/warehouses', { params: { query: { limit: 250 } } }),
          apiClient.GET('/v1/inventory/positions', { params: { query: { limit: 250 } } }),
        ])
      return {
        purchase: requireData(purchase, 'Purchase orders could not be loaded').items,
        sales: requireData(sales, 'Sales orders could not be loaded').items,
        products: requireData(products, 'Products could not be loaded').items,
        suppliers: requireData(suppliers, 'Suppliers could not be loaded').items,
        customers: requireData(customers, 'Customers could not be loaded').items,
        warehouses: requireData(warehouses, 'Warehouses could not be loaded').items,
        positions: requireData(positions, 'Inventory positions could not be loaded').items,
      }
    },
  })

  const orders = useMemo(
    () => tab === 'purchase' ? data.data?.purchase ?? [] : data.data?.sales ?? [],
    [data.data, tab],
  )
  const selected = selectedId
    ? orders.find((item) => item.id === selectedId)
    : orders[0]
  const productNames = useMemo(
    () => new Map(data.data?.products.map((item) => [item.id, item]) ?? []),
    [data.data],
  )
  const warehouseNames = useMemo(
    () => new Map(data.data?.warehouses.map((item) => [item.id, item.code]) ?? []),
    [data.data],
  )
  const supplierNames = useMemo(
    () => new Map(data.data?.suppliers.map((item) => [item.id, item.name]) ?? []),
    [data.data],
  )
  const customerNames = useMemo(
    () => new Map(data.data?.customers.map((item) => [item.id, item.name]) ?? []),
    [data.data],
  )

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['ops', 'orders'] })
    await queryClient.invalidateQueries({ queryKey: ['ops', 'inventory'] })
  }

  const createOrder = useMutation({
    mutationFn: async () => {
      const body = {
        order_number: orderNumber,
        party_id: partyId,
        warehouse_id: warehouseId,
        currency: 'USD',
        lines: [{
          product_id: productId,
          quantity,
          uom: productNames.get(productId)?.base_uom ?? 'ea',
          unit_price: unitPrice,
          currency: 'USD',
        }],
      }
      const result = kind === 'purchase'
        ? await apiClient.POST('/v1/purchase-orders', {
            body,
            params: { header: commandHeaders() },
          })
        : await apiClient.POST('/v1/sales-orders', {
            body,
            params: { header: commandHeaders() },
          })
      return requireData(result, 'Order creation failed')
    },
    onSuccess: async (created) => {
      setNotice(`${created.order_number} created as ${created.state}.`)
      setTab(created.kind)
      setSelectedId(created.id)
      setShowCreate(false)
      setOrderNumber('')
      await refresh()
    },
  })

  const transition = useMutation({
    mutationFn: async ({ order, command }: { order: Order; command: string }) => {
      const options = {
        params: {
          path: { order_id: order.id, command },
          header: commandHeaders(),
        },
        body: { expected_version: order.version },
      }
      const result = order.kind === 'purchase'
        ? await apiClient.POST('/v1/purchase-orders/{order_id}/commands/{command}', options)
        : await apiClient.POST('/v1/sales-orders/{order_id}/commands/{command}', options)
      return requireData(result, `The ${command} command failed`)
    },
    onSuccess: async (updated) => {
      setNotice(`${updated.order_number} is now ${updated.state.replaceAll('_', ' ')}.`)
      await refresh()
    },
  })

  const allocate = useMutation({
    mutationFn: async (order: Order) => {
      const line = order.lines[0]
      const position = data.data?.positions.find(
        (item) =>
          item.product_id === line.product_id
          && item.warehouse_id === order.warehouse_id
          && item.condition === 'sellable'
          && !item.lot_id
          && !item.serial_id,
      ) ?? data.data?.positions.find(
        (item) =>
          item.product_id === line.product_id
          && item.warehouse_id === order.warehouse_id
          && item.condition === 'sellable',
      )
      if (!position) throw new Error('No sellable inventory position exists for the first order line.')
      const result = await apiClient.POST('/v1/sales-orders/{order_id}/allocations', {
        params: {
          path: { order_id: order.id },
          header: commandHeaders(),
        },
        body: {
          expected_order_version: order.version,
          lines: [{
            order_line_id: line.id,
            location_id: position.location_id,
            quantity: allocationQuantity || line.open_quantity,
            expected_position_version: position.version,
          }],
        },
      })
      const allocated = requireData(result, 'Allocation failed')
      return {
        order: allocated.order,
        snapshot: {
          orderLineId: line.id,
          reservationId: allocated.reservation_ids[0],
          expectedPositionVersion: position.version + 1,
        },
      }
    },
    onSuccess: async ({ order, snapshot }) => {
      setAllocationByOrder((current) => ({ ...current, [order.id]: snapshot }))
      setNotice(`${order.order_number} allocated. A pick task is now available.`)
      setAllocationQuantity('')
      await refresh()
    },
  })

  const ship = useMutation({
    mutationFn: async (order: Order) => {
      const snapshot = allocationByOrder[order.id]
      if (!snapshot) {
        throw new Error('Allocate this order in the current session before shipping it.')
      }
      const result = await apiClient.POST('/v1/sales-orders/{order_id}/shipments', {
        params: {
          path: { order_id: order.id },
          header: commandHeaders(),
        },
        body: {
          expected_order_version: order.version,
          lines: [{
            order_line_id: snapshot.orderLineId,
            reservation_id: snapshot.reservationId,
            expected_reservation_version: 1,
            expected_position_version: snapshot.expectedPositionVersion,
          }],
        },
      })
      return requireData(result, 'Shipment posting failed').order
    },
    onSuccess: async (order) => {
      setAllocationByOrder((current) => {
        const next = { ...current }
        delete next[order.id]
        return next
      })
      setNotice(`${order.order_number} shipped. Stock and reservations were reconciled.`)
      await refresh()
    },
  })

  const submitCreate = (event: FormEvent) => {
    event.preventDefault()
    setNotice(null)
    createOrder.mutate()
  }
  const mutationError =
    createOrder.error || transition.error || allocate.error || ship.error
  const nextCommand = selected
    ? selected.kind === 'purchase'
      ? purchaseCommands[selected.state]
      : salesCommands[selected.state]
    : undefined

  return (
    <OpsShell
      eyebrow="Purchasing and sales"
      title="Orders"
      actions={
        <>
          <button className="ops-button" onClick={() => void data.refetch()} disabled={data.isFetching}>
            <RefreshCw size={15} /> Refresh
          </button>
          <button className="ops-button primary" onClick={() => setShowCreate((value) => !value)}>
            <Plus size={15} /> New order
          </button>
        </>
      }
    >
      {notice && <div className="ops-alert success">{notice}</div>}
      {mutationError && <div className="ops-alert danger" role="alert">{errorMessage(mutationError)}</div>}
      {data.error && <ErrorState error={data.error} />}

      {showCreate && (
        <form className="ops-panel ops-detail" onSubmit={submitCreate}>
          <div className="ops-panel-head" style={{ margin: '-18px -18px 18px' }}>
            <h2>Create an order</h2>
            <small>One line · add more in the detail workflow</small>
          </div>
          <div className="ops-form-grid">
            <label className="ops-field"><span>Order type</span>
              <select value={kind} onChange={(event) => { setKind(event.target.value as OrderKind); setPartyId('') }}>
                <option value="purchase">Purchase order</option>
                <option value="sales">Sales order</option>
              </select>
            </label>
            <label className="ops-field"><span>Order number</span>
              <input required value={orderNumber} onChange={(event) => setOrderNumber(event.target.value)} placeholder={kind === 'purchase' ? 'PO-2003' : 'SO-1005'} />
            </label>
            <label className="ops-field"><span>{kind === 'purchase' ? 'Supplier' : 'Customer'}</span>
              <select required value={partyId} onChange={(event) => setPartyId(event.target.value)}>
                <option value="">Select…</option>
                {(kind === 'purchase' ? data.data?.suppliers : data.data?.customers)?.map((item) => (
                  <option key={item.id} value={item.id}>{item.code} — {item.name}</option>
                ))}
              </select>
            </label>
            <label className="ops-field"><span>Warehouse</span>
              <select required value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)}>
                <option value="">Select…</option>
                {data.data?.warehouses.map((item) => <option key={item.id} value={item.id}>{item.code} — {item.name}</option>)}
              </select>
            </label>
            <label className="ops-field"><span>Product</span>
              <select required value={productId} onChange={(event) => setProductId(event.target.value)}>
                <option value="">Select…</option>
                {data.data?.products.map((item) => <option key={item.id} value={item.id}>{item.sku} — {item.name}</option>)}
              </select>
            </label>
            <label className="ops-field"><span>Quantity</span>
              <input required min="0.001" step="0.001" type="number" value={quantity} onChange={(event) => setQuantity(event.target.value)} />
            </label>
            <label className="ops-field"><span>Unit price (USD)</span>
              <input required min="0" step="0.01" type="number" value={unitPrice} onChange={(event) => setUnitPrice(event.target.value)} />
            </label>
          </div>
          <div className="ops-actions" style={{ marginTop: 16 }}>
            <button className="ops-button primary" type="submit" disabled={createOrder.isPending}>Create order</button>
            <button className="ops-button" type="button" onClick={() => setShowCreate(false)}>Cancel</button>
          </div>
        </form>
      )}

      <div className="ops-toolbar">
        <div className="ops-radio-group" aria-label="Order type">
          <button className={`ops-button ${tab === 'purchase' ? 'active' : ''}`} onClick={() => { setTab('purchase'); setSelectedId(null) }}>Purchase orders</button>
          <button className={`ops-button ${tab === 'sales' ? 'active' : ''}`} onClick={() => { setTab('sales'); setSelectedId(null) }}>Sales orders</button>
        </div>
      </div>

      {data.isLoading ? <LoadingState label="Loading order queues…" /> : (
        <div className="ops-split">
          <section className="ops-panel">
            <div className="ops-panel-head"><h2>{tab === 'purchase' ? 'Purchase' : 'Sales'} queue</h2><small>{orders.length} orders</small></div>
            {orders.length ? (
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead><tr><th>Order</th><th>Party</th><th>Warehouse</th><th>State</th><th className="ops-number">Total</th></tr></thead>
                  <tbody>
                    {orders.map((order) => (
                      <tr key={order.id} className={order.id === selectedId ? 'selected' : ''}>
                        <td><button className="ops-row-button" onClick={() => setSelectedId(order.id)}><strong>{order.order_number}</strong></button></td>
                        <td>{(order.kind === 'purchase' ? supplierNames : customerNames).get(order.party_id) ?? shortId(order.party_id)}</td>
                        <td>{warehouseNames.get(order.warehouse_id) ?? shortId(order.warehouse_id)}</td>
                        <td><StatePill value={order.state} /></td>
                        <td className="ops-number">{formatMoney(order.total, order.currency)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState>No {tab} orders.</EmptyState>}
          </section>

          <aside className="ops-panel ops-detail">
            {selected ? (
              <>
                <span className="ops-meta">{selected.kind} · version {selected.version}</span>
                <h2>{selected.order_number}</h2>
                <p>{selected.notes || 'No order notes.'}</p>
                <div className="ops-detail-grid">
                  <div><span>Status</span><strong>{selected.state.replaceAll('_', ' ')}</strong></div>
                  <div><span>Warehouse</span><strong>{warehouseNames.get(selected.warehouse_id) ?? shortId(selected.warehouse_id)}</strong></div>
                  <div><span>Party</span><strong>{(selected.kind === 'purchase' ? supplierNames : customerNames).get(selected.party_id) ?? shortId(selected.party_id)}</strong></div>
                  <div><span>Total</span><strong>{formatMoney(selected.total, selected.currency)}</strong></div>
                </div>
                <div className="ops-section-title"><h3>Lines</h3><span>{selected.lines.length}</span></div>
                <div className="ops-table-wrap">
                  <table className="ops-table">
                    <thead><tr><th>Product</th><th className="ops-number">Ordered</th><th className="ops-number">Open</th></tr></thead>
                    <tbody>{selected.lines.map((line) => (
                      <tr key={line.id}>
                        <td>{productNames.get(line.product_id)?.sku ?? shortId(line.product_id)}</td>
                        <td className="ops-number">{formatQuantity(line.quantity)} {line.uom}</td>
                        <td className="ops-number">{formatQuantity(line.open_quantity)}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                </div>
                <div className="ops-section-title"><h3>Commands</h3><span>Version checked</span></div>
                <div className="ops-actions">
                  {nextCommand && (
                    <button
                      className="ops-button primary"
                      disabled={transition.isPending}
                      onClick={() => transition.mutate({ order: selected, command: nextCommand.command })}
                    >
                      {nextCommand.label}
                    </button>
                  )}
                  {selected.kind === 'sales' && selected.state === 'confirmed' && (
                    <>
                      <input
                        className="ops-input"
                        style={{ width: 105 }}
                        aria-label="Allocation quantity"
                        type="number"
                        min="0.001"
                        step="0.001"
                        placeholder={selected.lines[0]?.open_quantity}
                        value={allocationQuantity}
                        onChange={(event) => setAllocationQuantity(event.target.value)}
                      />
                      <button className="ops-button primary" disabled={allocate.isPending} onClick={() => allocate.mutate(selected)}>Allocate</button>
                    </>
                  )}
                  {selected.kind === 'sales' && selected.state === 'picking' && (
                    <button className="ops-button primary" disabled={ship.isPending} onClick={() => ship.mutate(selected)}>Post shipment</button>
                  )}
                  {!nextCommand && !(selected.kind === 'sales' && ['confirmed', 'picking'].includes(selected.state)) && (
                    <span className="ops-muted">No manual transition is available from this state.</span>
                  )}
                </div>
                {selected.kind === 'purchase' && ['acknowledged', 'partially_received'].includes(selected.state) && (
                  <div className="ops-alert warning" style={{ marginTop: 16 }}>Receive this order from its generated task on the Tasks screen.</div>
                )}
              </>
            ) : <EmptyState>Select an order to inspect its workflow.</EmptyState>}
          </aside>
        </div>
      )}
    </OpsShell>
  )
}
