import { openDB, type DBSchema } from 'idb'
import type { components } from '../api/schema'
import { apiClient } from '../api/client'

export type WarehouseTask = components['schemas']['WarehouseTaskResponse']
export type WarehouseTaskCommand = 'start' | 'complete' | 'report-exception' | 'reopen'
export type QueueStatus = 'pending' | 'syncing' | 'conflict' | 'failed'

export interface QueuedWarehouseCommand {
  id: string
  taskId: string
  command: WarehouseTaskCommand
  expectedVersion: number
  idempotencyKey: string
  countedQuantity?: string
  createdAt: string
  status: QueueStatus
  error?: string
}

interface WarehouseDatabase extends DBSchema {
  tasks: {
    key: string
    value: WarehouseTask
  }
  commands: {
    key: string
    value: QueuedWarehouseCommand
    indexes: { 'by-created-at': string; 'by-task': string }
  }
}

const CACHE_EVENT = 'smartstock:warehouse-cache'
const CACHE_IDENTITY_KEY = 'smartstock-warehouse-cache-identity'
const dbPromise = openDB<WarehouseDatabase>('smartstock-warehouse', 1, {
  upgrade(database) {
    database.createObjectStore('tasks', { keyPath: 'id' })
    const commands = database.createObjectStore('commands', { keyPath: 'id' })
    commands.createIndex('by-created-at', 'createdAt')
    commands.createIndex('by-task', 'taskId')
  },
})

let activeSync: Promise<SyncSummary> | null = null

export interface SyncSummary {
  completed: number
  blocked: number
  remaining: number
}

function announceCacheChange() {
  window.dispatchEvent(new Event(CACHE_EVENT))
}

function commandError(error: unknown) {
  if (!error || typeof error !== 'object') return 'The command could not be synchronized.'
  const problem = error as { detail?: string; title?: string }
  return problem.detail || problem.title || 'The server rejected this warehouse command.'
}

export function nextWarehouseTaskState(task: WarehouseTask, command: WarehouseTaskCommand) {
  const valid: Record<WarehouseTaskCommand, Partial<Record<WarehouseTask['state'], WarehouseTask['state']>>> = {
    start: { open: 'in_progress', assigned: 'in_progress', exception: 'in_progress' },
    complete: { in_progress: 'completed' },
    'report-exception': { in_progress: 'exception' },
    reopen: { assigned: 'open', exception: 'open' },
  }
  return valid[command][task.state]
}

export function scanMatchesWarehouseTask(task: WarehouseTask, rawValue: string) {
  const value = rawValue.trim().toLowerCase()
  if (!value) return false
  return [task.id, task.task_number, task.product_id, task.source_location_id, task.destination_location_id]
    .filter(Boolean)
    .some((item) => item!.toLowerCase() === value)
}

export function warehouseCacheEvent() {
  return CACHE_EVENT
}

export async function configureWarehouseCacheIdentity(identity: string | null) {
  const priorIdentity = localStorage.getItem(CACHE_IDENTITY_KEY)
  if (priorIdentity === identity) return

  const database = await dbPromise
  const transaction = database.transaction(['tasks', 'commands'], 'readwrite')
  await Promise.all([
    transaction.objectStore('tasks').clear(),
    transaction.objectStore('commands').clear(),
    transaction.done,
  ])
  if (identity) localStorage.setItem(CACHE_IDENTITY_KEY, identity)
  else localStorage.removeItem(CACHE_IDENTITY_KEY)
  announceCacheChange()
}

export async function readCachedTasks() {
  const database = await dbPromise
  return (await database.getAll('tasks')).sort((left, right) =>
    left.priority - right.priority || left.created_at.localeCompare(right.created_at),
  )
}

export async function readQueuedCommands() {
  const database = await dbPromise
  return database.getAllFromIndex('commands', 'by-created-at')
}

export async function refreshWarehouseTasks() {
  const result = await apiClient.GET('/v1/warehouse-tasks', {
    params: { query: { limit: 250 } },
  })
  if (!result.data) throw new Error(commandError(result.error))

  const database = await dbPromise
  const transaction = database.transaction('tasks', 'readwrite')
  const requests = [
    transaction.store.clear(),
    ...result.data.items.map((task) => transaction.store.put(task)),
  ]
  await Promise.all([...requests, transaction.done])
  announceCacheChange()
  return result.data.items
}

export async function enqueueWarehouseCommand(
  task: WarehouseTask,
  command: WarehouseTaskCommand,
  options?: { countedQuantity?: string },
) {
  const nextState = nextWarehouseTaskState(task, command)
  if (!nextState) throw new Error(`Cannot ${command} a task in ${task.state} state.`)
  if (task.task_type === 'count' && command === 'complete') {
    const quantity = options?.countedQuantity?.trim()
    if (!quantity || !/^\d+(\.\d+)?$/.test(quantity)) {
      throw new Error('Enter a nonnegative counted quantity before completing the task.')
    }
  }

  const database = await dbPromise
  const existing = await database.getAllFromIndex('commands', 'by-task', task.id)
  if (existing.some((item) => item.status === 'conflict' || item.status === 'failed')) {
    throw new Error('Resolve the blocked command for this task before adding another action.')
  }

  const id = crypto.randomUUID()
  const latestTaskCommandTime = existing.reduce(
    (latest, item) => Math.max(latest, Date.parse(item.createdAt)),
    0,
  )
  const createdAt = new Date(Math.max(Date.now(), latestTaskCommandTime + 1)).toISOString()
  const record: QueuedWarehouseCommand = {
    id,
    taskId: task.id,
    command,
    expectedVersion: task.version,
    idempotencyKey: `warehouse-${id}`,
    countedQuantity: options?.countedQuantity?.trim(),
    createdAt,
    status: 'pending',
  }
  const optimisticTask: WarehouseTask = {
    ...task,
    state: nextState,
    version: task.version + 1,
    updated_at: record.createdAt,
  }

  const transaction = database.transaction(['tasks', 'commands'], 'readwrite')
  await Promise.all([
    transaction.objectStore('tasks').put(optimisticTask),
    transaction.objectStore('commands').put(record),
  ])
  await transaction.done
  announceCacheChange()
  return optimisticTask
}

export async function discardWarehouseCommand(commandId: string) {
  const database = await dbPromise
  await database.delete('commands', commandId)
  announceCacheChange()
  if (navigator.onLine) await refreshWarehouseTasks()
}

export async function retryWarehouseCommand(commandId: string) {
  const database = await dbPromise
  const record = await database.get('commands', commandId)
  if (!record) return
  await database.put('commands', { ...record, status: 'pending', error: undefined })
  announceCacheChange()
  return syncWarehouseQueue()
}

async function performSync(): Promise<SyncSummary> {
  if (!navigator.onLine) {
    const remaining = (await readQueuedCommands()).length
    return { completed: 0, blocked: 0, remaining }
  }

  const database = await dbPromise
  const commands = await readQueuedCommands()
  const blockedTasks = new Set(
    commands.filter((item) => item.status === 'conflict' || item.status === 'failed')
      .map((item) => item.taskId),
  )
  let completed = 0
  let blocked = blockedTasks.size

  for (const record of commands) {
    if (record.status === 'conflict' || record.status === 'failed' || blockedTasks.has(record.taskId)) {
      continue
    }
    await database.put('commands', { ...record, status: 'syncing', error: undefined })
    announceCacheChange()
    try {
      let synchronizedTask: WarehouseTask | undefined
      let responseStatus = 0
      let error: unknown
      if (record.command === 'complete' && record.countedQuantity !== undefined) {
        const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/count', {
          params: {
            path: { task_id: record.taskId },
            header: { 'Idempotency-Key': record.idempotencyKey },
          },
          body: {
            expected_task_version: record.expectedVersion,
            counted_quantity: record.countedQuantity,
          },
        })
        synchronizedTask = result.data?.task
        responseStatus = result.response.status
        error = result.error
      } else {
        const result = await apiClient.POST('/v1/warehouse-tasks/{task_id}/commands/{command}', {
          params: {
            path: { task_id: record.taskId, command: record.command },
            header: { 'Idempotency-Key': record.idempotencyKey },
          },
          body: { expected_version: record.expectedVersion, assigned_to: null },
        })
        synchronizedTask = result.data
        responseStatus = result.response.status
        error = result.error
      }
      if (!synchronizedTask) {
        const status = responseStatus === 409 ? 'conflict' : 'failed'
        await database.put('commands', {
          ...record,
          status,
          error: commandError(error),
        })
        blockedTasks.add(record.taskId)
        blocked += 1
        continue
      }
      const transaction = database.transaction(['tasks', 'commands'], 'readwrite')
      await Promise.all([
        transaction.objectStore('tasks').put(synchronizedTask),
        transaction.objectStore('commands').delete(record.id),
      ])
      await transaction.done
      completed += 1
    } catch {
      await database.put('commands', { ...record, status: 'pending' })
      break
    } finally {
      announceCacheChange()
    }
  }

  const remaining = (await readQueuedCommands()).length
  if (remaining === 0) {
    try {
      await refreshWarehouseTasks()
    } catch {
      // Successfully replayed writes remain authoritative even when refresh is unavailable.
    }
  }
  return { completed, blocked, remaining }
}

export function syncWarehouseQueue() {
  if (!activeSync) {
    activeSync = performSync().finally(() => {
      activeSync = null
    })
  }
  return activeSync
}
