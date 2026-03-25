'use client';

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { CircularProgress } from '@mui/material';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { Card } from '@/components/ui/card';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  Select,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from '@/components/ui/select';
import { ErrorDisplay } from '@/components/elements/ErrorDisplay';
import { LastUpdatedTimestamp } from '@/components/utils';
import {
  RotateCwIcon,
  Trash2Icon,
  PenIcon,
  PlusIcon,
  XIcon,
} from 'lucide-react';
import { useMobile } from '@/hooks/useMobile';
import {
  getSharedCaches,
  upsertSharedCache,
  deleteSharedCache,
  getK8sContexts,
  getStorageClasses,
  getRwmVolumes,
  applyVolume,
} from '@/data/connectors/shared-caches';

export function CachesPanel() {
  const isMobile = useMobile();
  const [data, setData] = useState([]);
  const [k8sContexts, setK8sContexts] = useState([]);
  const [loading, setLoading] = useState(false);
  const [lastFetchedTime, setLastFetchedTime] = useState(null);
  const [showFormDialog, setShowFormDialog] = useState(false);
  const [prefilledContext, setPrefilledContext] = useState('');
  const [editingCache, setEditingCache] = useState(null);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [cacheToDelete, setCacheToDelete] = useState(null);
  const [deleteLoading, setDeleteLoading] = useState(false);
  const [deleteError, setDeleteError] = useState(null);

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [caches, contexts] = await Promise.all([
        getSharedCaches(),
        getK8sContexts(),
      ]);
      setData(caches);
      setK8sContexts(contexts);
      setLastFetchedTime(new Date());
    } catch (error) {
      console.error('Failed to fetch shared caches:', error);
      setData([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  // Group caches by context
  const groupedByContext = useMemo(() => {
    const groups = {};
    for (const cache of data) {
      const ctx = cache.context || '_unscoped';
      if (!groups[ctx]) groups[ctx] = [];
      groups[ctx].push(cache);
    }
    return groups;
  }, [data]);

  // All contexts = union of fetched K8s contexts + contexts from existing caches
  const allContexts = useMemo(() => {
    const ctxSet = new Set(k8sContexts);
    for (const cache of data) {
      if (cache.context) ctxSet.add(cache.context);
    }
    return Array.from(ctxSet).sort();
  }, [k8sContexts, data]);

  const handleRefresh = () => {
    fetchData();
  };

  const handleAddCache = (ctx) => {
    setEditingCache(null);
    setPrefilledContext(ctx || '');
    setShowFormDialog(true);
  };

  const handleEditCache = (cache) => {
    setEditingCache(cache);
    setPrefilledContext('');
    setShowFormDialog(true);
  };

  const handleDeleteClick = (cache) => {
    setCacheToDelete(cache);
    setShowDeleteDialog(true);
    setDeleteError(null);
  };

  const handleDeleteConfirm = async () => {
    if (!cacheToDelete) return;
    setDeleteLoading(true);
    setDeleteError(null);
    try {
      const result = await deleteSharedCache({
        context: cacheToDelete.context,
        volume_name: cacheToDelete.spec.name,
      });
      if (!result.success) {
        throw new Error(result.msg);
      }
      setShowDeleteDialog(false);
      setCacheToDelete(null);
      fetchData();
    } catch (error) {
      setDeleteError(error);
    } finally {
      setDeleteLoading(false);
    }
  };

  const handleCancelDelete = () => {
    setShowDeleteDialog(false);
    setCacheToDelete(null);
    setDeleteError(null);
  };

  const handleFormSuccess = () => {
    setShowFormDialog(false);
    setEditingCache(null);
    setPrefilledContext('');
    fetchData();
  };

  return (
    <>
      {/* Header with Refresh */}
      <div className="flex items-center justify-end mb-4">
        <div className="flex items-center gap-3">
          {loading && (
            <div className="flex items-center">
              <CircularProgress size={15} className="mt-0" />
              <span className="ml-2 text-gray-500 text-sm">Loading...</span>
            </div>
          )}
          {!loading && lastFetchedTime && (
            <LastUpdatedTimestamp timestamp={lastFetchedTime} />
          )}
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            {!isMobile && <span>Refresh</span>}
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && allContexts.length === 0 && (
        <Card>
          <div className="flex justify-center items-center py-6 text-gray-500">
            <CircularProgress size={20} className="mr-2" />
            <span>Loading...</span>
          </div>
        </Card>
      )}

      {/* No K8s contexts found */}
      {!loading && allContexts.length === 0 && (
        <Card>
          <div className="text-center py-6 text-gray-500">
            No Kubernetes contexts found.
          </div>
        </Card>
      )}

      {/* One section per context */}
      {allContexts.map((ctx) => {
        const caches = groupedByContext[ctx] || [];
        return (
          <div key={ctx} className="mb-6">
            <div className="flex items-center justify-between mb-2">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-gray-700">
                  Kubernetes
                </span>
                <span className="text-sm text-gray-400">/</span>
                <span className="text-sm font-medium text-gray-700">{ctx}</span>
              </div>
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleAddCache(ctx)}
                className="text-sky-blue border-sky-blue hover:bg-sky-blue hover:text-white"
              >
                <PlusIcon className="w-4 h-4 mr-1" />
                Add Cache
              </Button>
            </div>
            <Card>
              {caches.length > 0 ? (
                <div className="overflow-x-auto rounded-lg">
                  <Table className="min-w-full">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="whitespace-nowrap">
                          Volume Name
                        </TableHead>
                        <TableHead className="whitespace-nowrap">
                          Type
                        </TableHead>
                        <TableHead className="whitespace-nowrap">
                          Size
                        </TableHead>
                        <TableHead className="whitespace-nowrap">
                          Cache Paths
                        </TableHead>
                        <TableHead>Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {caches.map((cache, idx) => (
                        <TableRow key={`${cache.spec.name}-${idx}`}>
                          <TableCell className="font-medium">
                            {cache.spec.name}
                          </TableCell>
                          <TableCell>{cache.volume_type || '-'}</TableCell>
                          <TableCell>{cache.volume_size || '-'}</TableCell>
                          <TableCell>
                            <div className="flex flex-wrap gap-1">
                              {cache.cache_paths.map((path) => (
                                <span
                                  key={path}
                                  className="inline-flex items-center px-2 py-0.5 rounded text-xs bg-gray-100 text-gray-700 font-mono"
                                >
                                  {path}
                                </span>
                              ))}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleEditCache(cache)}
                                className="text-gray-600 hover:text-gray-800 hover:bg-gray-50"
                                title="Edit cache"
                              >
                                <PenIcon className="w-4 h-4" />
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => handleDeleteClick(cache)}
                                className="text-red-600 hover:text-red-700 hover:bg-red-50"
                                title="Delete cache"
                              >
                                <Trash2Icon className="w-4 h-4" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              ) : (
                <div className="text-center py-4 text-gray-400 text-sm">
                  No shared caches configured for this context.
                </div>
              )}
            </Card>
          </div>
        );
      })}

      {/* Add/Edit Dialog */}
      {showFormDialog && (
        <CacheFormDialog
          open={showFormDialog}
          onClose={() => {
            setShowFormDialog(false);
            setEditingCache(null);
            setPrefilledContext('');
          }}
          onSuccess={handleFormSuccess}
          editingCache={editingCache}
          prefilledContext={prefilledContext}
          existingCaches={data}
        />
      )}

      {/* Delete Confirmation Dialog */}
      <Dialog open={showDeleteDialog} onOpenChange={handleCancelDelete}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete Shared Cache</DialogTitle>
            <DialogDescription>
              Are you sure you want to remove the shared cache for volume &quot;
              {cacheToDelete?.spec?.name || 'this cache'}&quot;? This will not
              delete the underlying volume.
            </DialogDescription>
          </DialogHeader>

          <ErrorDisplay
            error={deleteError}
            title="Deletion Failed"
            onDismiss={() => setDeleteError(null)}
          />

          <DialogFooter>
            <Button
              variant="outline"
              onClick={handleCancelDelete}
              disabled={deleteLoading}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={handleDeleteConfirm}
              disabled={deleteLoading}
            >
              {deleteLoading ? 'Removing...' : 'Remove'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function CacheFormDialog({
  open,
  onClose,
  onSuccess,
  editingCache,
  prefilledContext,
  existingCaches,
}) {
  const isEditing = !!editingCache;

  // Form state
  const [context, setContext] = useState(
    editingCache?.context || prefilledContext || ''
  );
  const [volumeMode, setVolumeMode] = useState(
    editingCache?.spec?.use_existing ? 'existing' : 'config'
  );
  const [volumeName, setVolumeName] = useState(editingCache?.spec?.name || '');
  const [storageClass, setStorageClass] = useState('');
  const [volumeSize, setVolumeSize] = useState('100');
  const [existingVolumeName, setExistingVolumeName] = useState(
    editingCache?.spec?.use_existing ? editingCache?.spec?.name || '' : ''
  );
  const [cachePaths, setCachePaths] = useState(
    editingCache?.cache_paths?.length > 0 ? [...editingCache.cache_paths] : ['']
  );

  // Dropdown data
  const [contexts, setContexts] = useState([]);
  const [storageClasses, setStorageClasses] = useState([]);
  const [rwmVolumes, setRwmVolumes] = useState([]);

  // Loading/error state
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const [loadingContexts, setLoadingContexts] = useState(false);
  const [loadingStorageClasses, setLoadingStorageClasses] = useState(false);
  const [loadingVolumes, setLoadingVolumes] = useState(false);

  // Fetch K8s contexts on mount
  useEffect(() => {
    const fetchContexts = async () => {
      setLoadingContexts(true);
      try {
        const data = await getK8sContexts();
        setContexts(data);
      } finally {
        setLoadingContexts(false);
      }
    };
    fetchContexts();
  }, []);

  // Fetch storage classes when context changes (config mode)
  useEffect(() => {
    if (!context || volumeMode !== 'config') return;
    setStorageClass('');
    setStorageClasses([]);
    setLoadingStorageClasses(true);
    const fetchSC = async () => {
      try {
        const data = await getStorageClasses(context);
        setStorageClasses(data);
        if (data.length > 0) {
          setStorageClass(data[0].name);
        }
      } finally {
        setLoadingStorageClasses(false);
      }
    };
    fetchSC();
  }, [context, volumeMode]);

  // Fetch and filter RWM volumes when context changes (existing mode)
  useEffect(() => {
    if (!context || volumeMode !== 'existing') return;
    setExistingVolumeName('');
    setRwmVolumes([]);
    setLoadingVolumes(true);
    const fetchVols = async () => {
      try {
        const data = await getRwmVolumes();
        // Filter volumes to those matching the selected context
        const filtered = data.filter((vol) => vol.region === context);
        setRwmVolumes(filtered);
      } finally {
        setLoadingVolumes(false);
      }
    };
    fetchVols();
  }, [context, volumeMode]);

  // Cache path handlers
  const addCachePath = () => {
    setCachePaths([...cachePaths, '']);
  };

  const removeCachePath = (index) => {
    if (cachePaths.length <= 1) return;
    setCachePaths(cachePaths.filter((_, i) => i !== index));
  };

  const updateCachePath = (index, value) => {
    const updated = [...cachePaths];
    updated[index] = value;
    setCachePaths(updated);
  };

  // Validate cache paths
  const validatePaths = () => {
    const nonEmpty = cachePaths.filter((p) => p.trim());
    if (nonEmpty.length === 0) {
      return 'At least one cache path is required.';
    }
    // Check for overlap with existing caches (not the one being edited)
    const currentName =
      volumeMode === 'existing' ? existingVolumeName : volumeName;
    const existingPaths = new Set();
    for (const cache of existingCaches) {
      if (
        isEditing &&
        cache.spec.name === editingCache.spec.name &&
        cache.context === editingCache.context
      ) {
        continue;
      }
      if (context === cache.context) {
        for (const p of cache.cache_paths) {
          existingPaths.add(p);
        }
      }
    }
    for (const path of nonEmpty) {
      if (existingPaths.has(path)) {
        return `Cache path "${path}" overlaps with an existing shared cache in the same scope.`;
      }
    }

    return null;
  };

  const handleSave = async () => {
    setError(null);

    if (!context) {
      setError(new Error('Please select a Kubernetes context.'));
      return;
    }

    // Validate
    const pathError = validatePaths();
    if (pathError) {
      setError(new Error(pathError));
      return;
    }

    const filteredPaths = cachePaths
      .map((p) => p.trim())
      .filter((p) => p.length > 0);
    const targetName =
      volumeMode === 'existing' ? existingVolumeName : volumeName;

    if (!targetName) {
      setError(new Error('Volume name is required.'));
      return;
    }

    setSaving(true);

    try {
      // If Volume Config mode, create the volume first
      if (volumeMode === 'config' && !isEditing) {
        const createResult = await applyVolume({
          name: targetName,
          size: volumeSize,
          storageClass: storageClass,
          region: context,
        });
        if (!createResult.success) {
          throw new Error(createResult.msg);
        }
      }

      // Save the shared cache config
      const result = await upsertSharedCache({
        context: context,
        spec: {
          name: targetName,
          ...(volumeMode === 'existing' ? { use_existing: true } : {}),
        },
        cache_paths: filteredPaths,
      });
      if (!result.success) {
        throw new Error(result.msg);
      }
      onSuccess();
    } catch (err) {
      setError(err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEditing ? 'Edit Shared Cache' : 'Add Shared Cache'}
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-2">
          {/* Infra (fixed) */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Infra
            </label>
            <input
              type="text"
              value="kubernetes"
              disabled
              className="w-full px-3 py-2 border border-gray-300 rounded-md bg-gray-50 text-gray-500 text-sm"
            />
          </div>

          {/* Context */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Context
            </label>
            <Select
              value={context}
              onValueChange={setContext}
              disabled={isEditing}
            >
              <SelectTrigger className="w-full h-10 text-sm">
                <SelectValue placeholder="Select a context..." />
              </SelectTrigger>
              <SelectContent>
                {loadingContexts ? (
                  <SelectItem value="_loading" disabled>
                    Loading...
                  </SelectItem>
                ) : contexts.length === 0 ? (
                  <SelectItem value="_none" disabled>
                    No contexts found
                  </SelectItem>
                ) : (
                  contexts.map((ctx) => (
                    <SelectItem key={ctx} value={ctx}>
                      {ctx}
                    </SelectItem>
                  ))
                )}
              </SelectContent>
            </Select>
          </div>

          {/* Volume Mode Toggle */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Volume
            </label>
            <div className="flex border border-gray-300 rounded-md overflow-hidden">
              <button
                type="button"
                className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
                  volumeMode === 'config'
                    ? 'bg-sky-blue text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-50'
                }`}
                onClick={() => setVolumeMode('config')}
                disabled={isEditing}
              >
                Volume Config
              </button>
              <button
                type="button"
                className={`flex-1 px-3 py-2 text-sm font-medium transition-colors ${
                  volumeMode === 'existing'
                    ? 'bg-sky-blue text-white'
                    : 'bg-white text-gray-700 hover:bg-gray-50'
                }`}
                onClick={() => setVolumeMode('existing')}
                disabled={isEditing}
              >
                Use Existing Volume
              </button>
            </div>
          </div>

          {/* Volume Config Fields */}
          {volumeMode === 'config' && (
            <div className="space-y-3 pl-2 border-l-2 border-gray-200">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Volume Name
                </label>
                <input
                  type="text"
                  value={volumeName}
                  onChange={(e) => setVolumeName(e.target.value)}
                  disabled={isEditing}
                  placeholder="my-shared-cache"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm disabled:bg-gray-50 disabled:text-gray-500"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Type
                </label>
                <input
                  type="text"
                  value="k8s-pvc"
                  disabled
                  className="w-full px-3 py-2 border border-gray-300 rounded-md bg-gray-50 text-gray-500 text-sm"
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Storage Class
                </label>
                {!context ? (
                  <p className="text-sm text-gray-400 italic">
                    Select a context first
                  </p>
                ) : (
                  <Select
                    value={storageClass}
                    onValueChange={setStorageClass}
                    disabled={isEditing || loadingStorageClasses}
                  >
                    <SelectTrigger className="w-full h-10 text-sm">
                      <SelectValue
                        placeholder={
                          loadingStorageClasses
                            ? 'Loading...'
                            : 'Select a storage class...'
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {storageClasses.length === 0 ? (
                        <SelectItem value="_none" disabled>
                          No storage classes found
                        </SelectItem>
                      ) : (
                        storageClasses.map((sc) => (
                          <SelectItem key={sc.name} value={sc.name}>
                            {sc.name} ({sc.provisioner})
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                )}
                <p className="mt-1 text-xs text-amber-600">
                  Shared caches require a storage class that supports
                  ReadWriteMany (RWX) access mode. Verify your storage class
                  provisioner supports RWX.
                </p>
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Volume Size (GiB)
                </label>
                <input
                  type="number"
                  value={volumeSize}
                  onChange={(e) => setVolumeSize(e.target.value)}
                  disabled={isEditing}
                  min="1"
                  className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm disabled:bg-gray-50 disabled:text-gray-500"
                />
              </div>
            </div>
          )}

          {/* Use Existing Volume Fields */}
          {volumeMode === 'existing' && (
            <div className="space-y-3 pl-2 border-l-2 border-gray-200">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">
                  Volume Name
                </label>
                {!context ? (
                  <p className="text-sm text-gray-400 italic">
                    Select a context first
                  </p>
                ) : (
                  <Select
                    value={existingVolumeName}
                    onValueChange={setExistingVolumeName}
                    disabled={isEditing || loadingVolumes}
                  >
                    <SelectTrigger className="w-full h-10 text-sm">
                      <SelectValue
                        placeholder={
                          loadingVolumes ? 'Loading...' : 'Select a volume...'
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {rwmVolumes.length === 0 ? (
                        <SelectItem value="_none" disabled>
                          No ReadWriteMany volumes found
                        </SelectItem>
                      ) : (
                        rwmVolumes.map((vol) => (
                          <SelectItem key={vol.name} value={vol.name}>
                            {vol.name}
                            {vol.size ? ` (${vol.size}Gi)` : ''}
                          </SelectItem>
                        ))
                      )}
                    </SelectContent>
                  </Select>
                )}
              </div>
            </div>
          )}

          {/* Cache Paths */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Cache Paths
            </label>
            <div className="space-y-2">
              {cachePaths.map((path, index) => (
                <div key={index} className="flex items-center gap-2">
                  <input
                    type="text"
                    value={path}
                    onChange={(e) => updateCachePath(index, e.target.value)}
                    placeholder="~/.cache/uv"
                    className="flex-1 px-3 py-2 border border-gray-300 rounded-md text-sm font-mono"
                  />
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeCachePath(index)}
                    disabled={cachePaths.length <= 1}
                    className="text-gray-400 hover:text-red-500 px-2"
                    title="Remove path"
                  >
                    <XIcon className="w-4 h-4" />
                  </Button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={addCachePath}
              className="mt-2 text-sm text-sky-blue hover:text-sky-blue-bright flex items-center"
            >
              <PlusIcon className="w-3 h-3 mr-1" />
              Add path
            </button>
            <p className="mt-1 text-xs text-gray-500">
              Paths relative to home directory (e.g., ~/.cache/uv) or absolute
              paths (e.g., /data/cache).
            </p>
          </div>

          {/* Error */}
          <ErrorDisplay
            error={error}
            title="Error"
            onDismiss={() => setError(null)}
          />
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button
            onClick={handleSave}
            disabled={saving}
            className="bg-sky-blue hover:bg-sky-blue-bright text-white"
          >
            {saving
              ? isEditing
                ? 'Saving...'
                : 'Creating...'
              : isEditing
                ? 'Save'
                : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
