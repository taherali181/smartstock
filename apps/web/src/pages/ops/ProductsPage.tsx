import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw, Search } from 'lucide-react'
import { apiClient } from '../../api/client'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  OpsShell,
  StatePill,
} from './shared'
import { requireData } from './utils'

export function ProductsPage() {
  const [search, setSearch] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const products = useQuery({
    queryKey: ['ops', 'products', search.trim()],
    queryFn: async () => {
      const result = await apiClient.GET('/v1/products', {
        params: { query: { search: search.trim() || undefined, limit: 250 } },
      })
      return requireData(result, 'Products could not be loaded').items
    },
  })
  const suppliers = useQuery({
    queryKey: ['ops', 'suppliers'],
    queryFn: async () => {
      const result = await apiClient.GET('/v1/suppliers', {
        params: { query: { limit: 250 } },
      })
      return requireData(result, 'Suppliers could not be loaded').items
    },
  })

  const selected =
    products.data?.find((item) => item.id === selectedId) ?? products.data?.[0]
  const customFields = useMemo(
    () => Object.entries(selected?.custom_fields ?? {}).sort(([left], [right]) => left.localeCompare(right)),
    [selected],
  )
  const kitComponents = Array.isArray(selected?.custom_fields.kit_components)
    ? selected.custom_fields.kit_components
    : []

  return (
    <OpsShell
      eyebrow="Catalog"
      title="Products"
      actions={
        <button
          className="ops-button"
          onClick={() => void Promise.all([products.refetch(), suppliers.refetch()])}
          disabled={products.isFetching || suppliers.isFetching}
        >
          <RefreshCw size={15} /> Refresh
        </button>
      }
    >
      <div className="ops-toolbar">
        <label className="ops-field ops-search">
          <span>Search SKU, name, or description</span>
          <div style={{ position: 'relative' }}>
            <Search size={15} style={{ position: 'absolute', left: 12, top: 14, color: 'var(--faint)' }} />
            <input
              style={{ width: '100%', paddingLeft: 36 }}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Try SKU-1017"
            />
          </div>
        </label>
      </div>

      {(products.error || suppliers.error) && <ErrorState error={products.error || suppliers.error} />}
      {products.isLoading ? <LoadingState label="Loading catalog…" /> : (
        <div className="ops-split">
          <section className="ops-panel">
            <div className="ops-panel-head">
              <h2>Product catalog</h2>
              <small>{products.data?.length ?? 0} records</small>
            </div>
            {products.data?.length ? (
              <div className="ops-table-wrap">
                <table className="ops-table">
                  <thead><tr><th>SKU</th><th>Name</th><th>Tracking</th><th>UOM</th><th>State</th></tr></thead>
                  <tbody>
                    {products.data.map((item) => (
                      <tr key={item.id} className={item.id === selectedId ? 'selected' : ''}>
                        <td>
                          <button className="ops-row-button" onClick={() => setSelectedId(item.id)}>
                            <strong>{item.sku}</strong>
                          </button>
                        </td>
                        <td>{item.name}</td>
                        <td>{item.tracking_mode}</td>
                        <td>{item.base_uom}</td>
                        <td><StatePill value={item.lifecycle_state} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <EmptyState>No products match “{search}”.</EmptyState>}
          </section>

          <aside className="ops-panel ops-detail">
            {selected ? (
              <>
                <span className="ops-meta">Product detail · version {selected.version}</span>
                <h2>{selected.name}</h2>
                <p>{selected.description || 'No product description has been recorded.'}</p>
                <div className="ops-detail-grid">
                  <div><span>SKU</span><strong>{selected.sku}</strong></div>
                  <div><span>Base UOM</span><strong>{selected.base_uom}</strong></div>
                  <div><span>Tracking</span><strong>{selected.tracking_mode}</strong></div>
                  <div><span>Lifecycle</span><strong>{selected.lifecycle_state}</strong></div>
                </div>

                <div className="ops-section-title"><h3>Planning and custom fields</h3><span>{customFields.length}</span></div>
                {customFields.length ? (
                  <div className="ops-detail-grid">
                    {customFields.map(([key, value]) => (
                      <div key={key}>
                        <span>{key.replaceAll('_', ' ')}</span>
                        <strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong>
                      </div>
                    ))}
                  </div>
                ) : <p className="ops-muted">No custom planning fields.</p>}

                <div className="ops-section-title"><h3>Kit components</h3><span>{kitComponents.length}</span></div>
                {kitComponents.length ? (
                  <ul>{kitComponents.map((item, index) => <li key={index}>{JSON.stringify(item)}</li>)}</ul>
                ) : <p className="ops-muted">This item has no kit components.</p>}

                <div className="ops-section-title"><h3>Supplier directory</h3><span>{suppliers.data?.length ?? 0}</span></div>
                {suppliers.isLoading ? <LoadingState label="Loading suppliers…" /> : (
                  <div className="ops-table-wrap">
                    <table className="ops-table">
                      <thead><tr><th>Code</th><th>Supplier</th><th>Currency</th></tr></thead>
                      <tbody>
                        {suppliers.data?.map((supplier) => (
                          <tr key={supplier.id}>
                            <td><strong>{supplier.code}</strong></td>
                            <td>{supplier.name}</td>
                            <td>{supplier.currency}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : <EmptyState>Select a product to inspect it.</EmptyState>}
          </aside>
        </div>
      )}
    </OpsShell>
  )
}
