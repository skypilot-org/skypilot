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
import { CircularProgress } from '@mui/material';
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
  const [status, setStatus] = React.useState('idle'); // idle, loading, success, error, fallback
  const [error, setError] = React.useState(null);
  const [vscodeUrl, setVscodeUrl] = React.useState(null);
  const isMobile = useMobile();

  // Reset state when modal opens
  React.useEffect(() => {
    if (isOpen && cluster) {
      setStatus('idle');
      setError(null);
      setVscodeUrl(null);
    }
  }, [isOpen, cluster]);

  const handleConnectVSCode = async () => {
    if (!cluster) return;

    setStatus('loading');
    setError(null);

    try {
      // Import the VSCode connector dynamically
      const { openVSCode } = await import('@/data/connectors/vscode');
      
      // Set up VSCode connection
      const url = await openVSCode(cluster);
      setVscodeUrl(url);
      setStatus('success');
      
      // Auto-close modal after successful connection
      setTimeout(() => {
        onClose();
        setStatus('idle');
      }, 2000);
      
    } catch (err) {
      console.error('VSCode connection failed:', err);
      setError(err.message || 'Failed to connect to VSCode');
      setStatus('error');
    }
  };

  const handleFallbackToInstructions = () => {
    setStatus('fallback');
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>
            Connect to VSCode: <span className="font-light">{cluster}</span>
          </DialogTitle>
          <DialogDescription>
            <div className="flex flex-col space-y-4">
              
              {/* Automated Connection */}
              {status !== 'fallback' && (
                <div>
                  <h3 className="text-sm font-medium mb-2">
                    One-Click VSCode Connection
                  </h3>
                  <Card className="p-4 bg-gray-50">
                    {status === 'idle' && (
                      <div className="flex flex-col items-center space-y-3">
                        <p className="text-sm text-gray-600 text-center">
                          Click the button below to automatically set up code-server and open VSCode in your browser.
                        </p>
                        <Button 
                          onClick={handleConnectVSCode}
                          className="flex items-center space-x-2"
                        >
                          <span>🚀 Connect to VSCode</span>
                        </Button>
                      </div>
                    )}

                    {status === 'loading' && (
                      <div className="flex flex-col items-center space-y-3">
                        <CircularProgress size={24} />
                        <div className="text-sm text-gray-600 text-center">
                          <p className="font-medium">Setting up VSCode connection...</p>
                          <p className="text-xs mt-1">
                            This may take a moment. We&apos;re installing code-server and creating a secure tunnel.
                          </p>
                        </div>
                      </div>
                    )}

                    {status === 'success' && (
                      <div className="flex flex-col items-center space-y-3">
                        <div className="text-green-500 text-2xl">✅</div>
                        <div className="text-sm text-center">
                          <p className="font-medium text-green-700">VSCode connection established!</p>
                          <p className="text-xs text-gray-600 mt-1">
                            Opening VSCode in a new window...
                          </p>
                          {vscodeUrl && (
                            <p className="text-xs text-blue-600 mt-2">
                              <a href={vscodeUrl} target="_blank" rel="noopener noreferrer">
                                {vscodeUrl}
                              </a>
                            </p>
                          )}
                        </div>
                      </div>
                    )}

                    {status === 'error' && (
                      <div className="flex flex-col items-center space-y-3">
                        <div className="text-red-500 text-2xl">❌</div>
                        <div className="text-sm text-center">
                          <p className="font-medium text-red-700">Connection failed</p>
                          <p className="text-xs text-gray-600 mt-1">
                            {error}
                          </p>
                        </div>
                        <div className="flex space-x-2">
                          <Button 
                            onClick={handleConnectVSCode}
                            variant="outline"
                            size="sm"
                          >
                            Retry
                          </Button>
                          <Button 
                            onClick={handleFallbackToInstructions}
                            variant="outline"
                            size="sm"
                          >
                            Manual Setup
                          </Button>
                        </div>
                      </div>
                    )}
                  </Card>
                </div>
              )}

              {/* Manual Instructions Fallback */}
              {status === 'fallback' && (
                <>
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

                  <div className="flex justify-center">
                    <Button 
                      onClick={() => setStatus('idle')}
                      variant="outline"
                    >
                      Back to Auto-Connect
                    </Button>
                  </div>
                </>
              )}
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
