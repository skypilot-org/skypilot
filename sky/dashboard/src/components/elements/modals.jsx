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
import { BASE_PATH } from '@/data/connectors/constants';
import { useMobile } from '@/hooks/useMobile';

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
          <DialogTitle>
            Connect to: <span className="font-light">{cluster}</span>
          </DialogTitle>
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
            <p className="text-sm text-secondary-foreground">
              Make sure to run{' '}
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
  const isMobile = useMobile();

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            Connect to: <span className="font-light">{cluster}</span>
          </DialogTitle>
          <DialogDescription>
            <div className="flex flex-col space-y-4">
              <div>
                <h3 className="text-sm font-medium mb-2 my-2">
                  Setup SSH access
                </h3>
                <Card className="p-3 bg-gray-50">
                  <div className="flex items-center justify-between">
                    <pre className="text-sm">
                      <code>sky status {cluster}</code>
                    </pre>
                    <Tooltip content="Copy command">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          navigator.clipboard.writeText(`sky status ${cluster}`)
                        }
                        className="h-8 w-8 rounded-full"
                      >
                        <CopyIcon className="h-4 w-4" />
                      </Button>
                    </Tooltip>
                  </div>
                </Card>
              </div>
              <div>
                <h3 className="text-sm font-medium mb-2 my-2">
                  Connect with VSCode/Cursor
                </h3>
                <Card className="p-3 bg-gray-50">
                  <div className="flex items-center justify-between">
                    <pre className="text-sm">
                      <code>
                        code --remote ssh-remote+{cluster} &quot;/home&quot;
                      </code>
                    </pre>
                    <Tooltip content="Copy command">
                      <Button
                        variant="ghost"
                        size="icon"
                        onClick={() =>
                          navigator.clipboard.writeText(
                            `code --remote ssh-remote+${cluster} "/home"`
                          )
                        }
                        className="h-8 w-8 rounded-full"
                      >
                        <CopyIcon className="h-4 w-4" />
                      </Button>
                    </Tooltip>
                  </div>
                </Card>
              </div>
              <div>
                <h3 className="text-sm font-medium">
                  Or use the GUI to connect
                </h3>
                <div
                  className={`relative ${isMobile ? '-mt-5' : '-mt-10'}`}
                  style={{ paddingBottom: '70%' }}
                >
                  <video
                    className="absolute top-0 left-0 w-full h-full rounded-lg"
                    controls
                    autoPlay
                    muted
                    preload="metadata"
                  >
                    <source
                      src={`${BASE_PATH}/videos/cursor-small.mp4`}
                      type="video/mp4"
                    />
                    Your browser does not support the video tag.
                  </video>
                </div>
              </div>
            </div>
          </DialogDescription>
        </DialogHeader>
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
  confirmClassName = null,
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
            variant={confirmClassName ? undefined : confirmVariant}
            className={confirmClassName}
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
