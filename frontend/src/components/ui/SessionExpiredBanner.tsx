/**
 * CLU-84: Banner shown when the LTI session has expired (401 from API).
 *
 * Displayed on polling pages (Dashboard, AutoRemedy, ACR) to tell the
 * user their session timed out. The background job is still running;
 * they just need to re-launch from Canvas to see updated progress.
 */

import { AlertTriangle } from 'lucide-react'

export function SessionExpiredBanner() {
  return (
    <div
      role="alert"
      className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-4"
    >
      <div className="flex items-start gap-3">
        <AlertTriangle
          className="mt-0.5 h-5 w-5 shrink-0 text-amber-600"
          aria-hidden="true"
        />
        <div>
          <p className="text-sm font-semibold text-amber-900">
            Your session has expired.
          </p>
          <p className="mt-1 text-sm text-amber-800">
            Re-launch Remedy Canvas LTI from Canvas to see the latest progress.
            Your AutoRemedy job is still running in the background — you
            will not lose any work.
          </p>
        </div>
      </div>
    </div>
  )
}
