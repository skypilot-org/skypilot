import React, { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { CircularProgress } from '@mui/material';
import { uploadSSHKey } from '@/data/connectors/ssh-node-pools';

export function SSHNodePoolModal({
  isOpen,
  onClose,
  onSave,
  poolData = null, // null for new pool, object for editing
  isLoading = false,
}) {
  const [poolName, setPoolName] = useState('');
  const [hosts, setHosts] = useState('');
  const [sshUser, setSshUser] = useState('ubuntu');
  const [keyUploadFile, setKeyUploadFile] = useState(null);
  const [password, setPassword] = useState('');
  const [errors, setErrors] = useState({});

  const isEditing = poolData !== null;

  // Populate form when editing existing pool
  useEffect(() => {
    if (isEditing && poolData) {
      setPoolName(poolData.name || '');
      setHosts((poolData.config?.hosts || []).join('\n'));
      setSshUser(poolData.config?.user || 'ubuntu');
      setPassword(poolData.config?.password || '');
    } else {
      // Reset form for new pool
      setPoolName('');
      setHosts('');
      setSshUser('ubuntu');
      setKeyUploadFile(null);
      setPassword('');
    }
    setErrors({});
  }, [isEditing, poolData]);

  const validateForm = () => {
    const newErrors = {};

    if (!poolName.trim()) {
      newErrors.poolName = 'Pool name is required';
    }

    if (!hosts.trim()) {
      newErrors.hosts = 'At least one host is required';
    }

    if (!sshUser.trim()) {
      newErrors.sshUser = 'SSH user is required';
    }

    if (!keyUploadFile && !password) {
      newErrors.auth = 'Either SSH key file or password is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = async () => {
    if (!validateForm()) return;

    const hostList = hosts
      .split('\n')
      .map((host) => host.trim())
      .filter((host) => host.length > 0);

    const poolConfig = {
      hosts: hostList,
      user: sshUser,
    };

    try {
      // Upload SSH key if provided
      if (keyUploadFile) {
        const keyName = keyUploadFile.name;
        await uploadSSHKey(keyName, keyUploadFile);
        poolConfig.identity_file = `~/.sky/ssh_keys/${keyName}`;
      }

      if (password) {
        poolConfig.password = password;
      }

      onSave(poolName, poolConfig);
    } catch (error) {
      console.error('Failed to upload SSH key:', error);
      setErrors({ ...errors, keyUpload: 'Failed to upload SSH key' });
    }
  };

  const handleClose = () => {
    if (!isLoading) {
      onClose();
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEditing
              ? `Edit SSH Node Pool: ${poolData?.name}`
              : 'Add SSH Node Pool'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-6">
          {/* Pool Name */}
          <div className="space-y-2">
            <Label htmlFor="poolName">Pool Name</Label>
            <Input
              id="poolName"
              placeholder="my-ssh-cluster"
              value={poolName}
              onChange={(e) => setPoolName(e.target.value)}
              disabled={isEditing}
              className={`placeholder:text-gray-500 ${errors.poolName ? 'border-red-500' : ''}`}
            />
            {errors.poolName && (
              <p className="text-sm text-red-500">{errors.poolName}</p>
            )}
          </div>

          {/* Hosts */}
          <div className="space-y-2">
            <Label htmlFor="hosts">Hosts (one per line)</Label>
            <Textarea
              id="hosts"
              placeholder={`192.168.1.10\n192.168.1.11\nhostname.example.com`}
              value={hosts}
              onChange={(e) => setHosts(e.target.value)}
              rows={6}
              className={`placeholder:text-gray-500 ${errors.hosts ? 'border-red-500' : ''}`}
            />
            {errors.hosts && (
              <p className="text-sm text-red-500">{errors.hosts}</p>
            )}
          </div>

          {/* SSH User */}
          <div className="space-y-2">
            <Label htmlFor="sshUser">SSH User</Label>
            <Input
              id="sshUser"
              placeholder="ubuntu"
              value={sshUser}
              onChange={(e) => setSshUser(e.target.value)}
              className={`placeholder:text-gray-500 ${errors.sshUser ? 'border-red-500' : ''}`}
            />
            {errors.sshUser && (
              <p className="text-sm text-red-500">{errors.sshUser}</p>
            )}
          </div>

          {/* SSH Key Selection */}
          <div className="space-y-2">
            <Label htmlFor="keyFile">SSH Private Key File</Label>
            <Input
              id="keyFile"
              type="file"
              accept=".pem,.key,id_rsa,id_ed25519"
              onChange={(e) => setKeyUploadFile(e.target.files?.[0] || null)}
              className="border-0 bg-transparent p-0 shadow-none focus:ring-0 file:mr-2 file:text-sm file:py-1 file:px-3 file:border file:border-gray-300 file:rounded file:bg-gray-50 hover:file:bg-gray-100 file:cursor-pointer"
            />
            {errors.keyUpload && (
              <p className="text-sm text-red-500">{errors.keyUpload}</p>
            )}
          </div>

          {/* Optional Password */}
          <div className="space-y-2">
            <Label htmlFor="password">
              Password (optional, if sudo requires a password)
            </Label>
            <Input
              id="password"
              type="password"
              placeholder="Leave empty if using passwordless sudo"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="placeholder:text-gray-500"
            />
          </div>

          {errors.auth && <p className="text-sm text-red-500">{errors.auth}</p>}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={isLoading}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={isLoading}
            className="bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 disabled:text-gray-500"
          >
            {isLoading ? (
              <>
                <CircularProgress size={16} className="mr-2" />
                Saving...
              </>
            ) : isEditing ? (
              'Update Pool'
            ) : (
              'Create Pool'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
