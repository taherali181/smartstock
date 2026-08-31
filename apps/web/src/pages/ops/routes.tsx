import type { ReactNode } from 'react'

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

export const OPS_ROUTES: OpsRoute[] = []
