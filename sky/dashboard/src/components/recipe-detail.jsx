'use client';

import React, { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
import yaml from 'js-yaml';
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  CopyIcon,
  PinIcon,
  PinOffIcon,
  Trash2Icon,
  EditIcon,
  ShareIcon,
  CheckIcon,
} from 'lucide-react';

import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import { YamlEditor } from '@/components/ui/yaml-editor';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { YamlHighlighter } from '@/components/YamlHighlighter';
import { showToast } from '@/data/connectors/toast';

import {
  getRecipe,
  updateRecipe,
  deleteRecipe,
  togglePinRecipe,
} from '@/data/connectors/recipes';
import {
  getRecipeTypeInfo,
  getLaunchCommand,
} from '@/data/constants/recipeTypes';
import { TimestampWithTooltip } from '@/components/utils';

// Parse recipe name from URL slug
// Names are the unique identifiers for recipes (no UUID parsing needed)
function parseRecipeSlug(slug) {
  if (!slug) return null;
  // The slug IS the recipe name now
  return slug;
}

// Edit Modal Component
// Note: Recipe names cannot be changed as they are the unique identifier
function EditModal({ isOpen, onClose, template, onSave }) {
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);

  useEffect(() => {
    if (template && isOpen) {
      setDescription(template.description || '');
      setContent(template.content || '');
      setFormError(null);
    }
  }, [template, isOpen]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSubmitting(true);
    setFormError(null);

    // Validate YAML syntax
    try {
      yaml.load(content);
    } catch (yamlError) {
      setFormError(`Invalid YAML: ${yamlError.message}`);
      setIsSubmitting(false);
      return;
    }

    try {
      await onSave({
        description: description || null,
        content,
      });
      onClose();
    } catch (error) {
      setFormError(error.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!template) return null;

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto px-8">
        <DialogHeader>
          <DialogTitle className="text-xl text-gray-900">
            Edit Recipe: {template.name}
          </DialogTitle>
          <DialogDescription>
            Update your recipe description and content.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description..."
              className="placeholder:text-gray-500"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="content">YAML Content *</Label>
            <YamlEditor
              value={content}
              onChange={(val) => {
                setContent(val);
                setFormError(null);
              }}
              maxHeight="400px"
            />
          </div>

          {formError && (
            <div className="rounded-md border border-red-200 bg-red-50 p-3 flex items-start gap-2">
              <AlertTriangleIcon className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
              <p className="text-sm text-red-800">{formError}</p>
            </div>
          )}

          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isSubmitting}
              className="bg-sky-600 hover:bg-sky-700 text-white"
            >
              {isSubmitting ? (
                <>
                  <CircularProgress size={16} className="mr-2" />
                  Saving...
                </>
              ) : (
                'Save Changes'
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// Delete Confirmation Modal
function DeleteModal({ isOpen, onClose, template, onDelete }) {
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onDelete();
      onClose();
    } catch (error) {
      showToast(`Delete failed: ${error.message}`, 'error');
    } finally {
      setIsDeleting(false);
    }
  };

  if (!template) return null;

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-xl text-red-600">
            Delete Recipe
          </DialogTitle>
          <DialogDescription>
            Are you sure you want to delete &quot;{template.name}&quot;? This
            action cannot be undone.
          </DialogDescription>
        </DialogHeader>

        <DialogFooter className="mt-4">
          <Button variant="outline" onClick={onClose} disabled={isDeleting}>
            Cancel
          </Button>
          <Button
            onClick={handleDelete}
            disabled={isDeleting}
            className="bg-red-600 hover:bg-red-700 text-white"
          >
            {isDeleting ? (
              <>
                <CircularProgress size={16} className="mr-2" />
                Deleting...
              </>
            ) : (
              <>
                <Trash2Icon className="w-4 h-4 mr-2" />
                Delete
              </>
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// Main YamlDetail Component
export function RecipeDetail() {
  const router = useRouter();
  // Support both old 'yaml' and new 'recipe' query params for backwards compatibility
  const { recipe: recipeSlug } = router.query;
  const slug = recipeSlug;

  const [template, setTemplate] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [copied, setCopied] = useState(false);
  const [commandCopied, setCommandCopied] = useState(false);
  const [yamlCopied, setYamlCopied] = useState(false);

  // Modal states
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);

  const fetchTemplate = useCallback(async () => {
    // Wait for router to be ready before accessing query params
    // (required for Next.js static export where query params are populated client-side)
    if (!router.isReady || !slug) return;

    const templateId = parseRecipeSlug(slug);
    if (!templateId) {
      setError('Invalid template URL');
      setLoading(false);
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const data = await getRecipe(templateId);
      if (!data) {
        setError('Recipe not found');
      } else {
        setTemplate(data);
      }
    } catch (err) {
      setError(err.message || 'Failed to load recipe');
    } finally {
      setLoading(false);
    }
  }, [router.isReady, slug]);

  useEffect(() => {
    fetchTemplate();
  }, [fetchTemplate]);

  const handleEdit = async (data) => {
    const updated = await updateRecipe(template.name, data);
    if (updated) {
      setTemplate(updated);
      showToast('Recipe updated successfully!', 'success');
    } else {
      throw new Error('Failed to update recipe');
    }
  };

  const handleDelete = async () => {
    const deleted = await deleteRecipe(template.name);
    if (deleted) {
      showToast('Recipe deleted successfully!', 'success');
      router.push('/recipes');
    } else {
      throw new Error('Failed to delete recipe');
    }
  };

  const handleTogglePin = async () => {
    try {
      const updated = await togglePinRecipe(template.name, !template.pinned);
      if (updated) {
        setTemplate(updated);
        showToast(
          updated.pinned ? 'Recipe pinned!' : 'Recipe unpinned!',
          'success'
        );
      }
    } catch (error) {
      showToast(`Recipe pin operation failed: ${error.message}`, 'error');
    }
  };

  const handleCopyToNew = () => {
    // Navigate to recipes page with copy data in query params
    const copyData = {
      name: `${template.name}-copied`,
      description: template.description,
      content: template.content,
      recipe_type: template.recipe_type,
    };
    router.push({
      pathname: '/recipes',
      query: { copy: JSON.stringify(copyData) },
    });
  };

  // Helper function to copy text with fallback
  const copyToClipboard = async (text, successMessage, setStateFn) => {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text);
      } else {
        // Fallback for non-secure contexts
        const textArea = document.createElement('textarea');
        textArea.value = text;
        textArea.style.position = 'fixed';
        textArea.style.left = '-999999px';
        textArea.style.top = '-999999px';
        document.body.appendChild(textArea);
        textArea.focus();
        textArea.select();
        document.execCommand('copy');
        textArea.remove();
      }
      setStateFn(true);
      showToast(successMessage, 'success');
      setTimeout(() => setStateFn(false), 2000);
    } catch (err) {
      showToast('Failed to copy to clipboard', 'error');
    }
  };

  const handleShare = () => {
    const url = window.location.href;
    copyToClipboard(url, 'Link copied to clipboard!', setCopied);
  };

  const copyCommandToClipboard = () => {
    if (!template) return;
    const command = getLaunchCommand(template.recipe_type, template.name);
    copyToClipboard(command, 'Command copied to clipboard!', setCommandCopied);
  };

  const copyYamlToClipboard = () => {
    if (!template) return;
    copyToClipboard(
      template.content,
      'YAML copied to clipboard!',
      setYamlCopied
    );
  };

  if (loading) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress size={20} className="mr-2" />
        <span className="text-gray-500">Loading...</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-center justify-center h-64">
        <div className="text-red-500 mb-4">{error}</div>
        <Link href="/recipes">
          <Button variant="outline">
            <ArrowLeftIcon className="w-4 h-4 mr-2" />
            Back to Hub
          </Button>
        </Link>
      </div>
    );
  }

  if (!template) {
    return null;
  }

  const typeInfo = getRecipeTypeInfo(template.recipe_type);
  const TypeIcon = typeInfo.icon;

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Link
            href="/recipes"
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <ArrowLeftIcon className="h-4 w-4 mr-1.5" />
            <span>Back</span>
          </Link>
          <div className="flex items-center gap-3">
            <TypeIcon
              className={`w-5 h-5 ${
                typeInfo.color === 'sky'
                  ? 'text-sky-600'
                  : typeInfo.color === 'purple'
                    ? 'text-purple-600'
                    : typeInfo.color === 'green'
                      ? 'text-green-600'
                      : typeInfo.color === 'orange'
                        ? 'text-orange-600'
                        : 'text-gray-600'
              }`}
            />
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-base text-sky-blue leading-none">
                  {template.name}
                </h1>
                {template.pinned && (
                  <PinIcon className="w-4 h-4 text-amber-500" />
                )}
              </div>
              <div className="flex items-center gap-2 text-sm text-gray-500">
                <span>{typeInfo.fullLabel}</span>
              </div>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-4">
          <button
            onClick={handleShare}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            {copied ? (
              <CheckIcon className="h-4 w-4 mr-1.5 text-green-600" />
            ) : (
              <ShareIcon className="h-4 w-4 mr-1.5" />
            )}
            <span>{copied ? 'Copied!' : 'Share'}</span>
          </button>
          <button
            onClick={
              template.is_pinnable !== false ? handleTogglePin : undefined
            }
            className={`flex items-center ${
              template.is_pinnable === false
                ? 'text-gray-400 cursor-not-allowed'
                : 'text-sky-blue hover:text-sky-blue-bright'
            }`}
            title={
              template.is_pinnable === false
                ? 'Default recipes cannot be pinned/unpinned'
                : ''
            }
            disabled={template.is_pinnable === false}
          >
            {template.pinned ? (
              <>
                <PinOffIcon className="h-4 w-4 mr-1.5" />
                <span>Unpin</span>
              </>
            ) : (
              <>
                <PinIcon className="h-4 w-4 mr-1.5" />
                <span>Pin</span>
              </>
            )}
          </button>
          <button
            onClick={handleCopyToNew}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <CopyIcon className="h-4 w-4 mr-1.5" />
            <span>Copy to New</span>
          </button>
          <button
            onClick={
              template.is_editable !== false
                ? () => setIsEditModalOpen(true)
                : undefined
            }
            className={`flex items-center ${
              template.is_editable === false
                ? 'text-gray-400 cursor-not-allowed'
                : 'text-sky-blue hover:text-sky-blue-bright'
            }`}
            title={
              template.is_editable === false
                ? 'Default recipes cannot be edited'
                : ''
            }
            disabled={template.is_editable === false}
          >
            <EditIcon className="h-4 w-4 mr-1.5" />
            <span>Edit</span>
          </button>
          <button
            onClick={
              template.is_editable !== false
                ? () => setIsDeleteModalOpen(true)
                : undefined
            }
            className={`flex items-center ${
              template.is_editable === false
                ? 'text-gray-400 cursor-not-allowed'
                : 'text-red-600 hover:text-red-700'
            }`}
            title={
              template.is_editable === false
                ? 'Default recipes cannot be deleted'
                : ''
            }
            disabled={template.is_editable === false}
          >
            <Trash2Icon className="h-4 w-4 mr-1.5" />
            <span>Delete</span>
          </button>
        </div>
      </div>

      {/* Template Details Card */}
      <div className="rounded-lg border bg-card text-card-foreground shadow-sm">
        <div className="flex items-center justify-between px-4 pt-4">
          <h3 className="text-lg font-semibold">Details</h3>
        </div>
        <div className="p-4">
          {/* Metadata Grid */}
          <div className="grid grid-cols-2 gap-6 mb-6">
            <div>
              <div className="text-gray-600 font-medium text-base">Name</div>
              <div className="text-base mt-1">{template.name}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-base">Type</div>
              <div className="text-base mt-1">{typeInfo.fullLabel}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-base">
                Authored by
              </div>
              <div className="text-base mt-1">
                {template.user_name || template.user_id || 'Unknown'}
              </div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-base">Updated</div>
              <div className="text-base mt-1">
                <TimestampWithTooltip
                  date={
                    template.updated_at
                      ? new Date(template.updated_at * 1000)
                      : null
                  }
                />{' '}
                by {template.updated_by_name || template.user_name || 'Unknown'}
              </div>
            </div>
          </div>

          {/* Description */}
          {template.description && (
            <div className="mb-6">
              <div className="text-gray-600 font-medium text-base mb-1">
                Description
              </div>
              <p className="text-base text-gray-700">{template.description}</p>
            </div>
          )}

          {/* Launch Command */}
          <div className="mb-6">
            <div className="flex items-center">
              <div className="text-gray-600 font-medium text-base">
                Launch Command
              </div>
              <button
                onClick={copyCommandToClipboard}
                className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                title={commandCopied ? 'Copied!' : 'Copy command'}
              >
                {commandCopied ? (
                  <CheckIcon className="w-4 h-4 text-green-600" />
                ) : (
                  <CopyIcon className="w-4 h-4" />
                )}
              </button>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded-md p-3 mt-2">
              <code className="text-sm text-gray-800 font-mono break-all">
                {getLaunchCommand(template.recipe_type, template.name)}
              </code>
            </div>
          </div>

          {/* YAML Content */}
          <div>
            <div className="flex items-center">
              <div className="text-gray-600 font-medium text-base">
                YAML Content
              </div>
              <button
                onClick={copyYamlToClipboard}
                className="flex items-center text-gray-500 hover:text-gray-700 transition-colors duration-200 p-1 ml-2"
                title={yamlCopied ? 'Copied!' : 'Copy YAML'}
              >
                {yamlCopied ? (
                  <CheckIcon className="w-4 h-4 text-green-600" />
                ) : (
                  <CopyIcon className="w-4 h-4" />
                )}
              </button>
            </div>
            <div className="bg-gray-50 border border-gray-200 rounded-md p-3 max-h-96 overflow-y-auto mt-2">
              <YamlHighlighter className="whitespace-pre-wrap">
                {template.content}
              </YamlHighlighter>
            </div>
          </div>
        </div>
      </div>

      {/* Modals */}
      <EditModal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        template={template}
        onSave={handleEdit}
      />
      <DeleteModal
        isOpen={isDeleteModalOpen}
        onClose={() => setIsDeleteModalOpen(false)}
        template={template}
        onDelete={handleDelete}
      />
    </div>
  );
}
