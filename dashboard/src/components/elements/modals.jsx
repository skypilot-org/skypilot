import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { CopyIcon } from 'lucide-react';
import { CustomTooltip as Tooltip } from '@/components/utils';

export function SSHInstructionsModal({ isOpen, onClose, cluster }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const sshCommands = [`sky status ${cluster}`, `ssh ${cluster}`];
  const sshCommandText = sshCommands.join('\n');

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect to {cluster}</DialogTitle>
          <DialogDescription>
            Use these instructions to connect to your cluster via SSH.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col space-y-4">
          <div>
            <h3 className="text-sm font-medium mb-2">SSH Command</h3>
            <Card className="p-3 bg-gray-50">
              <div className="flex items-center justify-between">
                <pre className="text-sm w-full whitespace-pre-wrap">
                  {sshCommands.map((cmd, index) => (
                    <code key={index} className="block">
                      {cmd}
                    </code>
                  ))}
                </pre>
                <Tooltip content={copied ? 'Copied!' : 'Copy command'}>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleCopy(sshCommandText)}
                    className="h-8 w-8 rounded-full"
                  >
                    <CopyIcon className="h-4 w-4" />
                  </Button>
                </Tooltip>
              </div>
            </Card>
          </div>

          <div>
            <h3 className="text-sm font-medium mb-2">Additional Information</h3>
            <p className="text-sm text-muted-foreground">
              Make sure run{' '}
              <code className="text-sm">sky status {cluster}</code> first to
              have SkyPilot set up the SSH access.
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function VSCodeInstructionsModal({ isOpen, onClose, cluster }) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = (text) => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const vscodeSteps = [
    '1. Install VSCode and Remote-SSH extension:',
    '   • Open VSCode',
    '   • Go to Extensions (Cmd+Shift+X or Ctrl+Shift+X)',
    "   • Search for 'Remote - SSH'",
    '   • Install if not already installed',
    '',
    '2. Connect to the cluster:',
    '   Method 1 - Using VSCode UI:',
    '   • Press Cmd+Shift+P (Mac) or Ctrl+Shift+P (Windows/Linux)',
    "   • Type 'Remote-SSH: Connect to Host'",
    `   • Enter "${cluster}"`,
    '',
    '   Method 2 - Using Command Line:',
    `   • Open terminal and run: code --remote ssh-remote+${cluster} /home/sky`,
  ].join('\n');

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Connect to {cluster} with VSCode</DialogTitle>
          <DialogDescription>
            Follow these instructions to connect to your cluster using VSCode
            Remote-SSH.
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col space-y-4">
          <div>
            <h3 className="text-sm font-medium mb-2">Setup Instructions</h3>
            <Card className="p-3 bg-gray-50">
              <div className="flex items-center justify-between">
                <pre className="text-sm w-full whitespace-pre-wrap">
                  {vscodeSteps}
                </pre>
                <Tooltip content={copied ? 'Copied!' : 'Copy instructions'}>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleCopy(vscodeSteps)}
                    className="h-8 w-8 rounded-full"
                  >
                    <CopyIcon className="h-4 w-4" />
                  </Button>
                </Tooltip>
              </div>
            </Card>
          </div>

          <div>
            <h3 className="text-sm font-medium mb-2">Troubleshooting</h3>
            <p className="text-sm text-muted-foreground">
              If you have issues connecting:
              <br />• Make sure VSCode is up to date
              <br />• Try restarting VSCode after installing Remote-SSH
              <br />• Enable &quot;Remote.SSH: Enable Dynamic Forwarding&quot;
              in VSCode settings
            </p>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ConfirmationModal({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = 'Confirm',
  confirmVariant = 'destructive',
}) {
  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{message}</DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex justify-end gap-2 pt-4">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant={confirmVariant}
            onClick={() => {
              onConfirm();
              onClose();
            }}
          >
            {confirmText}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
