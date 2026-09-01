import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
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
  CURRENT_USER,
  errorMessage,
  formatQuantity,
  requireData,
  shortId,
} from './utils'

type Task = components['schemas']['WarehouseTaskResponse']

export function TasksPage() {
  const queryClient = useQueryClient()
  const [warehouseId, setWarehouseId] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [receiptQuantity, setReceiptQuantity] = useState('')
  const [countedQuantity, setCountedQuantity] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  const data = useQuery({
    queryKey: ['ops', 'tasks', warehouseId],
    queryFn: async () => {
      const [tasks, warehouses, products, purchase, positions] = await Promise.all([
        apiClient.GET('/v1/warehouse-tasks', {
          params: { query: { warehouse_id: warehouseId || undefined, limit: 250 } },
        }),
        apiClient.GET('/v1/warehouses', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/products', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/purchase-orders', { params: { query: { limit: 250 } } }),
        apiClient.GET('/v1/inventory/positions', {
          params: { query: { warehouse_id: warehouseId || undefined, limit: 250 } },
        }),
      ])
      return {
        tasks: requireData(tasks, 'Warehouse tasks could not be loaded').items,
        warehouses: requireData(warehouses, 'Warehouses could not be loaded').items,
        products: requireData(products, 'Products could not be loaded').items,
        purchaseOrders: requireData(purchase, 'Purchase orders could not be loaded').items,
        positions: requireData(positions, 'Inventory positions could not be loaded').items,
      }
    },
  })

  const selected =
    data.data?.tasks.find((item) => item.id === selectedId) ?? data.data?.tasks[0]
  const warehouses = useMemo(
    () => new Map(data.data?.warehouses.map((item) => [item.id, item]) ?? []),
    [data.data],
  )
  const products = useMemo(
    () => new Map(data.data?.products.map((item) => [item.id, item]) ?? []),
    [data.data],
  )
  const activeCount = data.data?.tasks.filter(
    (item) => !['completed', 'cancelled'].includes(item.state),
  ).length ?? 0

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['ops', 'tasks'] }),
      queryClient.invalidateQueries({ queryKey: ['ops', 'orders'] }),
      queryClient.invalidateQueries({ queryKey: ['ops', 'inventory'] }),
    ])
  }

  const transition = useMutation({
    mutationFn: async ({
      task,
      command,
      assignedTo,
    }: {
      task: Task
      command: string
      assignedTo?: string | null
    }) => {
      const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/commands/{command}', {
        params: {
          path: { task_id: task.id, command },
          header: commandHeaders(),
        },
        body: { expected_version: task.version, assigned_to: assignedTo },
      })
      return requireData(result, `Task command ${command} failed`)
    },
    onSuccess: async (task) => {
      setNotice(`${task.task_number} is now ${task.state.replaceAll('_', ' ')}.`)
      await refresh()
    },
  })

  const count = useMutation({
    mutationFn: async (task: Task) => {
      const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/count', {
        params: {
          path: { task_id: task.id },
          header: commandHeaders(),
        },
        body: {
          expected_task_version: task.version,
          counted_quantity: countedQuantity,
        },
      })
      return requireData(result, 'Count posting failed')
    },
    onSuccess: async (result) => {
      setNotice(
        `${result.task.task_number} posted a variance of ${formatQuantity(result.count.variance_quantity)}.`,
      )
      setCountedQuantity('')
      await refresh()
    },
  })

  const receive = useMutation({
    mutationFn: async (task: Task) => {
      const order = data.data?.purchaseOrders.find((item) => item.id === task.reference_id)
      if (!order) throw new Error('The purchase order referenced by this task is not available.')
      const line = order.lines[0]
      const position = data.data?.positions.find(
        (item) =>
          item.product_id === line.product_id
          && item.warehouse_id === task.warehouse_id
          && item.condition === 'sellable'
          && (!task.source_location_id || item.location_id === task.source_location_id),
      ) ?? data.data?.positions.find(
        (item) =>
          item.product_id === line.product_id
          && item.warehouse_id === task.warehouse_id
          && item.condition === 'sellable',
      )
      if (!position) throw new Error('No receiving bin with an inventory position was found.')
      const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/receipt', {
        params: {
          path: { task_id: task.id },
          header: commandHeaders(),
        },
        body: {
          receipt_number: `RCPT-${Date.now()}`,
          expected_order_version: order.version,
          expected_task_version: task.version,
          over_receipt_tolerance_percent: 0,
          lines: [{
            order_line_id: line.id,
            location_id: position.location_id,
            accepted_quantity: receiptQuantity || line.open_quantity,
            rejected_quantity: 0,
            expected_sellable_version: position.version,
            expected_quarantine_version: 0,
          }],
        },
      })
      return requireData(result, 'Purchase receipt posting failed')
    },
    onSuccess: async (result) => {
      const remainder = result.follow_up_task?.quantity
      setNotice(
        remainder
          ? `${result.receipt.receipt_number} posted. Follow-up receive work remains.`
          : `${result.receipt.receipt_number} posted and the order is fully received.`,
      )
      setReceiptQuantity('')
      await refresh()
    },
  })

  const transferShip = useMutation({
    mutationFn: async (task: Task) => {
      const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/transfer/ship', {
        params: {
          path: { task_id: task.id },
          header: commandHeaders(),
        },
        body: { expected_task_version: task.version },
      })
      return requireData(result, 'Transfer shipment failed')
    },
    onSuccess: async () => {
      setNotice('Transfer shipped and destination receipt work created.')
      await refresh()
    },
  })

  const mutationError = transition.error || count.error || receive.error || transferShip.error
  const pending = transition.isPending || count.isPending || receive.isPending || transferShip.isPending

  return (
    <OpsShell
      eyebrow="Warehouse execution"
      title="Task queue"
      actions={
        <button className="ops-button" onClick={() => void data.refetch()} disabled={data.isFetching}>
          <RefreshCw size={15} /> Refresh
        </button>
      }
    >
      {notice && <div className="ops-alert success">{notice}</div>}
      {mutationError && <div className="ops-alert danger" role="alert">{errorMessage(mutationError)}</div>}
      {data.error && <ErrorState error={data.error} />}
      <div className="ops-toolbar">
        <label className="ops-field">
          <span>Warehouse</span>
          <select value={warehouseId} onChange={(event) => setWarehouseId(event.target.value)}>
            <option value="">All warehouses</option>
            {data.data?.warehouses.map((item) => (
              <option key={item.id} value={item.id}>{item.code} — {item.name}</option>
            ))}
          </select>
        </label>
      </div>

      <div className="ops-grid">
        <div className="ops-card"><span>Active tasks</span><strong>{activeCount}</strong></div>
        <div className="ops-card"><span>Receive</span><strong>{data.data?.tasks.filter((item) => item.task_type === 'receive').length ?? 0}</strong></div>
        <div className="ops-card"><span>Count</span><strong>{data.data?.tasks.filter((item) => item.task_type === 'count').length ?? 0}</strong></div>
        <div className="ops-card"><span>Transfer</span><strong>{data.data?.tasks.filter((item) => item.task_type === 'transfer').length ?? 0}</strong></div>
      </div>

      {data.isLoading ? <LoadingState /> : (
        <div className="ops-split">
          <section className="ops-panel">
            <div className="ops-panel-head"><h2>Warehouse work</h2><small>{data.data?.tasks.length ?? 0} tasks</small></div>
            {data.data?.tasks.length ? (
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead><tr><th>Task</th><th>Type</th><th>Warehouse</th><th>Product</th><th className="ops-number">Qty</th><th>State</th><th>Assigned</th></tr></thead>
                  <tbody>{data.data.tasks.map((task) => (
                    <tr key={task.id} className={task.id === selectedId ? 'selected' : ''}>
                      <td><button className="ops-row-button" onClick={() => setSelectedId(task.id)}><strong>{task.task_number}</strong></button></td>
                      <td>{task.task_type}</td>
                      <td>{warehouses.get(task.warehouse_id)?.code ?? shortId(task.warehouse_id)}</td>
                      <td>{task.product_id ? products.get(task.product_id)?.sku ?? shortId(task.product_id) : '—'}</td>
                      <td className="ops-number">{task.quantity ? `${formatQuantity(task.quantity)} ${task.uom ?? ''}` : '—'}</td>
                      <td><StatePill value={task.state} /></td>
                      <td>{task.assigned_to ? shortId(task.assigned_to) : 'Unassigned'}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <EmptyState>No warehouse tasks match this filter.</EmptyState>}
          </section>

          <aside className="ops-panel ops-detail">
            {selected ? (
              <>
                <span className="ops-meta">{selected.task_type} · priority {selected.priority} · v{selected.version}</span>
                <h2>{selected.task_number}</h2>
                <p>{selected.reference_type ? `${selected.reference_type} ${shortId(selected.reference_id)}` : 'Ad hoc warehouse task'}</p>
                <div className="ops-detail-grid">
                  <div><span>State</span><strong>{selected.state.replaceAll('_', ' ')}</strong></div>
                  <div><span>Assigned to</span><strong>{selected.assigned_to ? shortId(selected.assigned_to) : 'Unassigned'}</strong></div>
                  <div><span>Product</span><strong>{selected.product_id ? products.get(selected.product_id)?.sku ?? shortId(selected.product_id) : '—'}</strong></div>
                  <div><span>Quantity</span><strong>{selected.quantity ? `${formatQuantity(selected.quantity)} ${selected.uom ?? ''}` : '—'}</strong></div>
                  <div><span>Source bin</span><strong>{shortId(selected.source_location_id)}</strong></div>
                  <div><span>Destination bin</span><strong>{shortId(selected.destination_location_id)}</strong></div>
                </div>

                <div className="ops-section-title"><h3>Task commands</h3><span>Concurrency checked</span></div>
                <div className="ops-actions">
                  {selected.state === 'open' && (
                    <button className="ops-button" disabled={pending} onClick={() => transition.mutate({ task: selected, command: 'assign', assignedTo: CURRENT_USER })}>Assign to me</button>
                  )}
                  {['open', 'assigned'].includes(selected.state) && (
                    <button className="ops-button primary" disabled={pending} onClick={() => transition.mutate({ task: selected, command: 'start', assignedTo: selected.assigned_to })}>Start task</button>
                  )}
                  {selected.state === 'exception' && (
                    <button className="ops-button" disabled={pending} onClick={() => transition.mutate({ task: selected, command: 'reopen' })}>Reopen</button>
                  )}
                  {!['completed', 'cancelled'].includes(selected.state) && (
                    <button className="ops-button danger" disabled={pending} onClick={() => transition.mutate({ task: selected, command: 'cancel' })}>Cancel</button>
                  )}
                </div>

                {selected.state === 'in_progress' && selected.task_type === 'receive' && (
                  <div className="ops-inline-form">
                    <label className="ops-field"><span>Accepted quantity</span>
                      <input type="number" min="0.001" step="0.001" value={receiptQuantity} onChange={(event) => setReceiptQuantity(event.target.value)} placeholder={selected.quantity ?? '0'} />
                    </label>
                    <div className="ops-actions">
                      <button className="ops-button primary" disabled={pending} onClick={() => receive.mutate(selected)}>Post receipt</button>
                    </div>
                  </div>
                )}

                {selected.state === 'in_progress' && selected.task_type === 'count' && (
                  <div className="ops-inline-form">
                    <label className="ops-field"><span>Blind counted quantity</span>
                      <input type="number" min="0" step="0.001" value={countedQuantity} onChange={(event) => setCountedQuantity(event.target.value)} placeholder="Enter physical count" />
                    </label>
                    <div className="ops-actions">
                      <button className="ops-button primary" disabled={pending || !countedQuantity} onClick={() => count.mutate(selected)}>Post variance</button>
                    </div>
                  </div>
                )}

                {selected.state === 'in_progress' && selected.task_type === 'transfer' && (
                  <div className="ops-inline-form">
                    <button className="ops-button primary" disabled={pending} onClick={() => transferShip.mutate(selected)}>Ship transfer</button>
                  </div>
                )}
              </>
            ) : <EmptyState>Select a task to work it.</EmptyState>}
          </aside>
        </div>
      )}
    </OpsShell>
  )
}
