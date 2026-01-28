'use client';

import React, {
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
} from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
import yaml from 'js-yaml';
import {
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
  getCategories,
} from '@/data/connectors/recipes';
import {
  getRecipeTypeInfo,
  getLaunchCommand,
  capitalizeWords,
} from '@/data/constants/recipeTypes';

// Helper to format relative time
function formatRelativeTime(timestamp) {
  if (!timestamp) return '';
  const now = Date.now() / 1000;
  const diff = now - timestamp;

  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return new Date(timestamp * 1000).toLocaleDateString();
}

// Helper to format full timestamp
function formatFullTimestamp(timestamp) {
  if (!timestamp) return '';
  return new Date(timestamp * 1000).toLocaleString();
}


// Parse template ID from URL slug
function parseRecipeSlug(slug) {
  if (!slug) return null;
  // UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx (36 chars with dashes)
  // Try to extract UUID from end of slug
  const uuidMatch = slug.match(
    /([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})$/i
  );
  if (uuidMatch) {
    return uuidMatch[1];
  }
  // If no UUID pattern found, assume the whole slug is the ID
  return slug;
}

// Category Combobox Component - allows selecting predefined or typing custom
function CategoryCombobox({
  value,
  onChange,
  predefinedCategories,
  customCategories,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [inputValue, setInputValue] = useState(value || '');
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);

  // Combine all categories for suggestions
  const allCategories = useMemo(() => {
    const predefined = predefinedCategories.map((c) => ({
      name: c.name,
      icon: c.icon,
      isPredefined: true,
    }));
    const custom = customCategories.map((name) => ({
      name,
      icon: null,
      isPredefined: false,
    }));
    return [...predefined, ...custom];
  }, [predefinedCategories, customCategories]);

  // Filter categories based on input
  const filteredCategories = useMemo(() => {
    if (!inputValue) return allCategories;
    const lower = inputValue.toLowerCase();
    return allCategories.filter((c) => c.name.toLowerCase().includes(lower));
  }, [allCategories, inputValue]);

  // Check if current input matches an existing category
  const isExistingCategory = useMemo(() => {
    return allCategories.some(
      (c) => c.name.toLowerCase() === inputValue.toLowerCase()
    );
  }, [allCategories, inputValue]);

  // Handle click outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(event.target) &&
        inputRef.current &&
        !inputRef.current.contains(event.target)
      ) {
        setIsOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Sync input value with external value
  useEffect(() => {
    setInputValue(value || '');
  }, [value]);

  const handleInputChange = (e) => {
    const newValue = e.target.value;
    setInputValue(newValue);
    onChange(newValue);
    if (!isOpen) setIsOpen(true);
  };

  const handleSelect = (categoryName) => {
    setInputValue(categoryName);
    onChange(categoryName);
    setIsOpen(false);
  };

  const handleClear = () => {
    setInputValue('');
    onChange('');
    inputRef.current?.focus();
  };

  return (
    <div className="relative">
      <div className="relative">
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={handleInputChange}
          onFocus={() => setIsOpen(true)}
          placeholder="Select or type a category..."
          className="w-full h-10 px-3 pr-8 text-sm border border-input rounded-md focus:outline-none focus:ring-2 focus:ring-ring"
        />
        {inputValue && (
          <button
            type="button"
            onClick={handleClear}
            className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
          >
            <svg
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        )}
      </div>
      {isOpen && (
        <div
          ref={dropdownRef}
          className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-md shadow-lg max-h-48 overflow-y-auto"
        >
          {filteredCategories.length > 0 ? (
            <>
              {filteredCategories.map((cat, index) => (
                <div
                  key={cat.name}
                  className={`px-3 py-2 cursor-pointer hover:bg-gray-50 text-sm ${
                    index !== filteredCategories.length - 1
                      ? 'border-b border-gray-100'
                      : ''
                  }`}
                  onClick={() => handleSelect(cat.name)}
                >
                  <span className="text-gray-700">
                    {cat.icon && <span className="mr-2">{cat.icon}</span>}
                    {cat.name}
                    {cat.isPredefined && (
                      <span className="ml-2 text-xs text-gray-400">
                        (predefined)
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </>
          ) : inputValue && !isExistingCategory ? (
            <div className="px-3 py-2 text-sm text-gray-500">
              Press Enter or click away to use &quot;{inputValue}&quot; as a new
              category
            </div>
          ) : (
            <div className="px-3 py-2 text-sm text-gray-500">
              No categories found
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Edit Modal Component
function EditModal({
  isOpen,
  onClose,
  template,
  onSave,
  predefinedCategories,
  customCategories,
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState('');
  const [category, setCategory] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (template && isOpen) {
      setName(template.name || '');
      setDescription(template.description || '');
      setContent(template.content || '');
      setCategory(template.category || '');
    }
  }, [template, isOpen]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setIsSubmitting(true);

    // Validate YAML syntax
    try {
      yaml.load(content);
    } catch (yamlError) {
      showToast(`Invalid YAML: ${yamlError.message}`, 'error');
      setIsSubmitting(false);
      return;
    }

    try {
      await onSave({
        name,
        description: description || null,
        content,
        category: category || null,
      });
      onClose();
    } catch (error) {
      showToast(`Error: ${error.message}`, 'error');
    } finally {
      setIsSubmitting(false);
    }
  };

  if (!template) return null;

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="text-xl text-gray-900">
            Edit Recipe
          </DialogTitle>
          <DialogDescription>
            Update your recipe details and content.
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name *</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My GPU Training"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="category">Category</Label>
              <CategoryCombobox
                value={category}
                onChange={setCategory}
                predefinedCategories={predefinedCategories}
                customCategories={customCategories}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Optional description..."
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="content">YAML Content *</Label>
            <YamlEditor
              value={content}
              onChange={setContent}
              minHeight="250px"
              maxHeight="400px"
            />
          </div>

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
            Delete Template
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
            className="bg-red-600 hover:bg-red-700"
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
  const [predefinedCategories, setPredefinedCategories] = useState([]);
  const [customCategories, setCustomCategories] = useState([]);
  const [copied, setCopied] = useState(false);
  const [commandCopied, setCommandCopied] = useState(false);
  const [yamlCopied, setYamlCopied] = useState(false);

  // Modal states
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);

  const fetchTemplate = useCallback(async () => {
    if (!slug) return;

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
  }, [slug]);

  const fetchCategories = useCallback(async () => {
    try {
      const data = await getCategories();
      setPredefinedCategories(data?.predefined || []);
      setCustomCategories(data?.custom || []);
    } catch (err) {
      console.error('Failed to fetch categories:', err);
    }
  }, []);

  useEffect(() => {
    fetchTemplate();
    fetchCategories();
  }, [fetchTemplate, fetchCategories]);

  const handleEdit = async (data) => {
    const updated = await updateRecipe(template.id, data);
    if (updated) {
      setTemplate(updated);
      showToast('Recipe updated successfully!', 'success');
    } else {
      throw new Error('Failed to update recipe');
    }
  };

  const handleDelete = async () => {
    const deleted = await deleteRecipe(template.id);
    if (deleted) {
      showToast('Recipe deleted successfully!', 'success');
      router.push('/recipes');
    } else {
      throw new Error('Failed to delete recipe');
    }
  };

  const handleTogglePin = async () => {
    try {
      const updated = await togglePinRecipe(template.id, !template.pinned);
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
      name: `${template.name} (copied)`,
      description: template.description,
      content: template.content,
      recipe_type: template.recipe_type,
      category: template.category,
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
    const command = getLaunchCommand(template.recipe_type, template.id);
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
          <Link href="/recipes">
            <button className="text-sky-blue hover:text-sky-blue-bright flex items-center">
              <ArrowLeftIcon className="h-4 w-4 mr-1.5" />
              <span>Back</span>
            </button>
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
                {template.category && (
                  <>
                    <span>·</span>
                    <span>{capitalizeWords(template.category)}</span>
                  </>
                )}
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
              <div className="text-gray-600 font-medium text-base">Type</div>
              <div className="text-base mt-1">{typeInfo.fullLabel}</div>
            </div>
            <div>
              <div className="text-gray-600 font-medium text-base">
                Category
              </div>
              <div className="text-base mt-1">
                {template.category
                  ? capitalizeWords(template.category)
                  : 'None'}
              </div>
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
              <div
                className="text-base mt-1"
                title={formatFullTimestamp(template.updated_at)}
              >
                {formatRelativeTime(template.updated_at)} by{' '}
                {template.updated_by_name || template.user_name || 'Unknown'}
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
                {getLaunchCommand(template.recipe_type, template.id)}
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
        predefinedCategories={predefinedCategories}
        customCategories={customCategories}
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