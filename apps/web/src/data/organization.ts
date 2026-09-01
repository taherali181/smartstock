import { useQuery } from '@tanstack/react-query'
import { apiClient } from '../api/client'

/** The signed-in organization. Its name is shown in the header, so it must come
 *  from the record rather than a literal that can drift away from the tenant. */
export function useCurrentOrganization() {
  return useQuery({
    queryKey: ['current-organization'],
    staleTime: 5 * 60_000,
    queryFn: async () => {
      const result = await apiClient.GET('/v1/organizations/current')
      if (!result.data) throw new Error('the current organization could not be read')
      return result.data
    },
  })
}
