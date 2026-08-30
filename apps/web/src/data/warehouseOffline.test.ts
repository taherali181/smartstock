import 'fake-indexeddb/auto'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import type { WarehouseTask } from './warehouseOffline'

const post = vi.fn()
const get = vi.fn()

vi.mock('../api/client', () => ({
  apiClient: {
    POST: post,
    GET: get,
  },
}))

const eventTarget = new EventTarget()
Object.defineProperty(globalThis, 'window', { value: eventTarget, configurable: true })
const localStorageValues = new Map<string, string>()
Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: (key: string) => localStorageValues.get(key) ?? null,
    setItem: (key: string, value: string) => localStorageValues.set(key, value),
    removeItem: (key: string) => localStorageValues.delete(key),
  },
  configurable: true,
})
Object.defineProperty(globalThis, 'navigator', {
  value: { onLine: false },
  configurable: true,
})

const task: WarehouseTask = {
  id: '11111111-1111-4111-8111-111111111111',
  task_number: 'PICK-0001',
  task_type: 'pick',
  warehouse_id: '22222222-2222-4222-8222-222222222222',
  state: 'open',
  source_location_id: '33333333-3333-4333-8333-333333333333',
  destination_location_id: null,
  product_id: '44444444-4444-4444-8444-444444444444',
  quantity: '2',
  uom: 'each',
  condition: 'sellable',
  ownership: 'owned',
  lot_id: null,
  serial_id: null,
  expected_position_version: null,
  reference_type: 'sales_order',
  reference_id: '55555555-5555-4555-8555-555555555555',
  assigned_to: null,
  priority: 10,
  version: 1,
  created_at: '2026-08-30T12:00:00Z',
  updated_at: '2026-08-30T12:00:00Z',
  replayed: false,
}

describe('warehouse offline execution', () => {
  let warehouse: typeof import('./warehouseOffline')

  beforeAll(async () => {
    warehouse = await import('./warehouseOffline')
  })

  it('recognizes task, product, and location scans without accepting unrelated labels', () => {
    expect(warehouse.scanMatchesWarehouseTask(task, 'pick-0001')).toBe(true)
    expect(warehouse.scanMatchesWarehouseTask(task, task.product_id!)).toBe(true)
    expect(warehouse.scanMatchesWarehouseTask(task, task.source_location_id!)).toBe(true)
    expect(warehouse.scanMatchesWarehouseTask(task, 'WRONG-LABEL')).toBe(false)
  })

  it('queues ordered task transitions with consecutive entity versions and replays them once', async () => {
    await warehouse.configureWarehouseCacheIdentity('operator-a:organization-a')
    const started = await warehouse.enqueueWarehouseCommand(task, 'start')
    expect(started.state).toBe('in_progress')
    expect(started.version).toBe(2)

    const completed = await warehouse.enqueueWarehouseCommand(started, 'complete')
    expect(completed.state).toBe('completed')
    expect(completed.version).toBe(3)

    const queued = await warehouse.readQueuedCommands()
    expect(queued.map((item) => item.expectedVersion)).toEqual([1, 2])
    expect(new Set(queued.map((item) => item.idempotencyKey)).size).toBe(2)

    expect(await warehouse.syncWarehouseQueue()).toEqual({ completed: 0, blocked: 0, remaining: 2 })

    Object.defineProperty(globalThis, 'navigator', {
      value: { onLine: true },
      configurable: true,
    })
    post
      .mockResolvedValueOnce({ data: started, response: new Response(null, { status: 200 }) })
      .mockResolvedValueOnce({ data: completed, response: new Response(null, { status: 200 }) })
    get.mockResolvedValue({ data: { items: [completed], next_cursor: null } })

    const result = await warehouse.syncWarehouseQueue()
    expect(result).toEqual({ completed: 2, blocked: 0, remaining: 0 })
    expect(post).toHaveBeenCalledTimes(2)
    expect(post.mock.calls[0][1].body.expected_version).toBe(1)
    expect(post.mock.calls[1][1].body.expected_version).toBe(2)
    expect(await warehouse.readQueuedCommands()).toEqual([])
    expect((await warehouse.readCachedTasks())[0]).toMatchObject({ state: 'completed', version: 3 })
  })

  it('removes cached tasks and commands when the authenticated cache identity changes', async () => {
    await warehouse.configureWarehouseCacheIdentity('operator-b:organization-b')
    expect(await warehouse.readCachedTasks()).toEqual([])
    expect(await warehouse.readQueuedCommands()).toEqual([])
  })

  it('replays a blind count through the atomic count endpoint', async () => {
    post.mockReset()
    get.mockReset()
    Object.defineProperty(globalThis, 'navigator', {
      value: { onLine: true },
      configurable: true,
    })
    await warehouse.configureWarehouseCacheIdentity('counter-a:organization-a')
    const countTask: WarehouseTask = {
      ...task,
      id: '66666666-6666-4666-8666-666666666666',
      task_number: 'COUNT-0001',
      task_type: 'count',
      quantity: null,
      expected_position_version: 7,
    }
    const started = await warehouse.enqueueWarehouseCommand(countTask, 'start')
    const completed = await warehouse.enqueueWarehouseCommand(started, 'complete', {
      countedQuantity: '8.5',
    })
    post
      .mockResolvedValueOnce({ data: started, response: new Response(null, { status: 200 }) })
      .mockResolvedValueOnce({
        data: {
          task: completed,
          count: {},
          replayed: false,
        },
        response: new Response(null, { status: 201 }),
      })
    get.mockResolvedValue({ data: { items: [completed], next_cursor: null } })

    expect(await warehouse.syncWarehouseQueue()).toEqual({ completed: 2, blocked: 0, remaining: 0 })
    expect(post.mock.calls.at(-1)?.[0]).toBe('/v1/warehouse-tasks/{task_id}/count')
    expect(post.mock.calls.at(-1)?.[1].body).toEqual({
      expected_task_version: 2,
      counted_quantity: '8.5',
    })
  })
})
