import type { ReactNode } from 'react'
import { InventoryPage } from './InventoryPage'
import { OrdersPage } from './OrdersPage'
import { ProductsPage } from './ProductsPage'
import { TasksPage } from './TasksPage'

/**
 * Operational screens, owned by the `core` lane.
 *
 * Append entries here; `App.tsx` mounts whatever this array contains, so no
 * routing file has to be edited by two agents at once. Each page must be a
 * self-contained component that fetches through the generated client and takes
 * no props.
 *
 * Example:
 *   import { InventoryPage } from './InventoryPage'
 *   { path: '/inventory', label: 'Inventory', element: <InventoryPage /> },
 */
export interface OpsRoute {
  path: string
  label: string
  element: ReactNode
}

export const OPS_ROUTES: OpsRoute[] = [
  { path: '/inventory', label: 'Inventory', element: <InventoryPage /> },
  { path: '/products', label: 'Products', element: <ProductsPage /> },
  { path: '/orders', label: 'Orders', element: <OrdersPage /> },
  { path: '/tasks', label: 'Tasks', element: <TasksPage /> },
]
