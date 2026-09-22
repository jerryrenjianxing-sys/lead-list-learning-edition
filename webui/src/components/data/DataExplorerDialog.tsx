import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Database } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { DataExplorer } from './DataExplorer'

type DataExplorerDialogProps = {
  label?: string
  variant?: 'light' | 'terminal'
  children?: ReactNode
}

export function DataExplorerDialog({
  label,
  variant = 'light',
  children,
}: DataExplorerDialogProps) {
  const { t } = useTranslation('data')

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          className={
            variant === 'terminal'
              ? 'terminal-data-button'
              : 'apple-secondary-button'
          }
        >
          {children ?? <Database className="w-3.5 h-3.5" />}
          {label ?? t('dialog.button')}
        </Button>
      </DialogTrigger>
      <DialogContent className="apple-dialog max-w-5xl max-h-[88vh] overflow-hidden">
        <DialogHeader>
          <DialogTitle className="apple-dialog-title">
            {t('dialog.title')}
          </DialogTitle>
        </DialogHeader>
        <div className="overflow-auto max-h-[calc(88vh-96px)] pr-2">
          <DataExplorer />
        </div>
      </DialogContent>
    </Dialog>
  )
}
