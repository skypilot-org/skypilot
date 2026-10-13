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

  // urun: this dashboard fronts a CONTROLLER-managed fleet — the user's
  // machine has no ~/.sky state for these clusters, so upstream's
  // `ssh <cluster>` hint cannot work. The fleet's credentials live on the
  // controller pod (per-cluster key + HostName under the runner's HOME);
  // this one paste-able kubectl exec runs ssh INSIDE the manager container
  // and resolves the IP from sky's own generated per-cluster config.
  const home = '/var/lib/urun-skypilot-runner/home';
  const key = `${home}/.sky/generated/ssh-keys/${cluster}.key`;
  const cfg = `${home}/.sky/generated/ssh/${cluster}`;
  const sshCommand =
    `kubectl -n urun exec deploy/skypilot-controller -c manager -- sh -c ` +
    `'ssh -i ${key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=no ` +
    `-o UserKnownHostsFile=/dev/null gcpuser@$(grep -oE "HostName [0-9.]+" ` +
    `${cfg} | cut -d" " -f2)'`;

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            Connect to: <span className="font-light">{cluster}</span>
          </DialogTitle>
          <DialogDescription>
            SSH into this cluster from your machine (kubectl exec into the
            controller, which holds the fleet's per-cluster credentials).
          </DialogDescription>
        </DialogHeader>
        <div className="flex flex-col space-y-4">
          <div>
            <h3 className="text-sm font-medium mb-2">SSH Command</h3>
            <Card className="p-3 bg-gray-50">
              <div className="flex items-center justify-between">
                <pre className="text-sm w-full whitespace-pre-wrap break-all">
                  <code className="block">{sshCommand}</code>
                </pre>
                <Tooltip content={copied ? 'Copied!' : 'Copy command'}>
                  <Button
                    variant="ghost"
                    size="icon"
                    onClick={() => handleCopy(sshCommand)}
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
              Requires kubectl access to the prod cluster. The command runs ssh
              inside the skypilot-controller manager container, which holds the
              per-cluster key, and resolves the cluster's IP from sky's
              generated ssh config.
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
