import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../api/client'

export function useOperationalQueues() {
  return useQuery({
    queryKey: ['phase-3-operational-queues'],
    queryFn: async () => {
      const [purchaseResult, salesResult, taskResult] = await Promise.all([
        apiClient.GET('/v1/purchase-orders', { params: { query: { limit: 100 } } }),
        apiClient.GET('/v1/sales-orders', { params: { query: { limit: 100 } } }),
        apiClient.GET('/v1/warehouse-tasks', { params: { query: { limit: 100 } } }),
      ])
      if (!purchaseResult.data || !salesResult.data || !taskResult.data) {
        throw new Error('Operational queues could not be loaded')
      }
      return {
        purchaseOrders: purchaseResult.data.items,
        salesOrders: salesResult.data.items,
        tasks: taskResult.data.items,
      }
    },
  })
}
