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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { CircularProgress } from '@mui/material';
import { uploadSSHKey, listSSHKeys } from '@/data/connectors/ssh-node-pools';

export function SSHNodePoolModal({ 
  isOpen, 
  onClose, 
  onSave, 
  poolData = null, // null for new pool, object for editing
  isLoading = false 
}) {
  const [poolName, setPoolName] = useState('');
  const [hosts, setHosts] = useState('');
  const [sshUser, setSshUser] = useState('ubuntu');
  const [selectedKeyFile, setSelectedKeyFile] = useState('');
  const [password, setPassword] = useState('');
  const [availableKeys, setAvailableKeys] = useState([]);
  const [keyUploadFile, setKeyUploadFile] = useState(null);
  const [isUploadingKey, setIsUploadingKey] = useState(false);
  const [errors, setErrors] = useState({});

  const isEditing = poolData !== null;

  // Load available SSH keys when modal opens
  useEffect(() => {
    if (isOpen) {
      loadAvailableKeys();
    }
  }, [isOpen]);

  // Populate form when editing existing pool
  useEffect(() => {
    if (isEditing && poolData) {
      setPoolName(poolData.name || '');
      setHosts((poolData.config?.hosts || []).join('\n'));
      setSshUser(poolData.config?.user || 'ubuntu');
      setSelectedKeyFile(poolData.config?.identity_file || '');
      setPassword(poolData.config?.password || '');
    } else {
      // Reset form for new pool
      setPoolName('');
      setHosts('');
      setSshUser('ubuntu');
      setSelectedKeyFile('');
      setPassword('');
    }
    setErrors({});
  }, [isEditing, poolData]);

  const loadAvailableKeys = async () => {
    try {
      const keys = await listSSHKeys();
      setAvailableKeys(keys);
    } catch (error) {
      console.error('Failed to load SSH keys:', error);
    }
  };

  const handleKeyUpload = async () => {
    if (!keyUploadFile) return;

    setIsUploadingKey(true);
    try {
      const keyName = keyUploadFile.name;
      await uploadSSHKey(keyName, keyUploadFile);
      await loadAvailableKeys(); // Refresh the list
      setSelectedKeyFile(`~/.sky/ssh_keys/${keyName}`);
      setKeyUploadFile(null);
      // Clear file input
      const fileInput = document.querySelector('input[type="file"]');
      if (fileInput) fileInput.value = '';
    } catch (error) {
      console.error('Failed to upload SSH key:', error);
      setErrors({ ...errors, keyUpload: 'Failed to upload SSH key' });
    } finally {
      setIsUploadingKey(false);
    }
  };

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

    if (!selectedKeyFile && !password) {
      newErrors.auth = 'Either SSH key or password is required';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSave = () => {
    if (!validateForm()) return;

    const hostList = hosts
      .split('\n')
      .map(host => host.trim())
      .filter(host => host.length > 0);

    const poolConfig = {
      hosts: hostList,
      user: sshUser,
    };

    if (selectedKeyFile) {
      poolConfig.identity_file = selectedKeyFile;
    }

    if (password) {
      poolConfig.password = password;
    }

    onSave(poolName, poolConfig);
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
            {isEditing ? `Edit SSH Node Pool: ${poolData?.name}` : 'Add SSH Node Pool'}
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
            <Label>SSH Private Key</Label>
            <div className="space-y-3">
              <Select value={selectedKeyFile} onValueChange={setSelectedKeyFile}>
                <SelectTrigger>
                  <SelectValue placeholder="Select an SSH key..." />
                </SelectTrigger>
                <SelectContent>
                  {availableKeys.map((key) => (
                    <SelectItem key={key} value={`~/.sky/ssh_keys/${key}`}>
                      {key}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              {/* Upload new key */}
              <div className="flex items-center space-x-2">
                <Input
                  type="file"
                  accept=".pem,.key,id_rsa,id_ed25519"
                  onChange={(e) => setKeyUploadFile(e.target.files?.[0] || null)}
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleKeyUpload}
                  disabled={!keyUploadFile || isUploadingKey}
                >
                  {isUploadingKey ? (
                    <CircularProgress size={16} className="mr-2" />
                  ) : null}
                  Upload
                </Button>
              </div>

              {errors.keyUpload && (
                <p className="text-sm text-red-500">{errors.keyUpload}</p>
              )}
            </div>
          </div>

          {/* Optional Password */}
          <div className="space-y-2">
            <Label htmlFor="password">Password (optional)</Label>
            <Input
              id="password"
              type="password"
              placeholder="Leave empty if using SSH key"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="placeholder:text-gray-500"
            />
          </div>

          {errors.auth && (
            <p className="text-sm text-red-500">{errors.auth}</p>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose} disabled={isLoading}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isLoading} className="bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 disabled:text-gray-500">
            {isLoading ? (
              <>
                <CircularProgress size={16} className="mr-2" />
                Saving...
              </>
            ) : (
              isEditing ? 'Update Pool' : 'Create Pool'
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
} 