import { HashRouter, Routes, Route, Navigate } from 'react-router-dom'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { LTIProvider } from '@/contexts/LTIContext'
import { AppLayout } from '@/components/layout/AppLayout'
import { SelectionProviderRoute } from '@/components/layout/SelectionProviderRoute'
import { LaunchPage } from '@/pages/LaunchPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { RemediatePage } from '@/pages/RemediatePage'
import { PreviewPage } from '@/pages/PreviewPage'
import { AutoRemedyPage } from '@/pages/AutoRemedyPage'
import { ContentTypeIssuePage } from '@/pages/ContentTypeIssuePage'
import { FilesContentView } from '@/pages/FilesContentView'
import { ConfirmRemediationModal } from '@/pages/ConfirmRemediationModal'
import { ChangesPage } from '@/pages/ChangesPage'
import { ACRDashboardPage } from '@/pages/ACRDashboardPage'
import { ACRReportViewerPage } from '@/pages/ACRReportViewerPage'
import { ExclusionsPanel } from '@/pages/ExclusionsPanel'

export default function App() {
  return (
    <ThemeProvider>
    <HashRouter>
      <LTIProvider>
        <Routes>
          <Route path="/" element={<LaunchPage />} />
          <Route element={<AppLayout />}>
            <Route path="/dashboard" element={<DashboardPage />} />
            <Route path="/autoremedy" element={<AutoRemedyPage />} />
            {/* CLU-85 routes wrapped in SelectionProvider. Order matters:
                /content/files must precede /content/:contentType so the
                dedicated FilesContentView wins the match. */}
            <Route element={<SelectionProviderRoute />}>
              <Route path="/content/files" element={<FilesContentView />} />
              <Route path="/content/:contentType" element={<ContentTypeIssuePage />} />
              <Route path="/autoremedy/confirm" element={<ConfirmRemediationModal />} />
            </Route>
            <Route path="/remediate" element={<RemediatePage />} />
            <Route path="/remediate/:jobId/preview" element={<PreviewPage />} />
            <Route path="/remediate/:jobId/changes" element={<ChangesPage />} />
            <Route path="/acr" element={<ACRDashboardPage />} />
            <Route path="/acr/:acrId" element={<ACRReportViewerPage />} />
            <Route path="/exclusions" element={<ExclusionsPanel />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </LTIProvider>
    </HashRouter>
    </ThemeProvider>
  )
}
