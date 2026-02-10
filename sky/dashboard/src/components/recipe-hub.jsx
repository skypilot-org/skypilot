'use client';

import React, {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
} from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import { CircularProgress } from '@mui/material';
import yaml from 'js-yaml';
import {
  RotateCwIcon,
  PlusIcon,
  PinIcon,
  PinOffIcon,
  Trash2Icon,
  AlertTriangleIcon,
  FileCode,
} from 'lucide-react';

import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from '@/components/ui/table';
import { sortData } from '@/data/utils';
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
import {
  LastUpdatedTimestamp,
  TimestampWithTooltip,
  NonCapitalizedTooltip,
} from '@/components/utils';
import { showToast } from '@/data/connectors/toast';

import {
  getRecipes,
  createRecipe,
  deleteRecipe,
  togglePinRecipe,
} from '@/data/connectors/recipes';
import {
  RecipeType,
  ALL_RECIPE_TYPES,
  getRecipeTypeInfo,
} from '@/data/constants/recipeTypes';

// Define filter options for the YAML filter dropdown
const RECIPE_PROPERTY_OPTIONS = [
  { label: 'Name', value: 'name' },
  { label: 'Type', value: 'recipe_type' },
  { label: 'Owner', value: 'user_name' },
];

// Generate URL slug from recipe
// Recipe names are the unique identifier and are already URL-safe
function generateRecipeSlug(name) {
  return name;
}

// Component to display username, showing just the name part for emails with tooltip
function UserName({ name, className = '' }) {
  if (!name) return <span className={className}>Unknown</span>;

  // Check if it's an email address
  const isEmail = name.includes('@');
  if (isEmail) {
    const displayName = name.split('@')[0];
    return (
      <NonCapitalizedTooltip content={name}>
        <span
          className={`border-b border-dotted border-gray-400 cursor-help ${className}`}
        >
          {displayName}
        </span>
      </NonCapitalizedTooltip>
    );
  }

  return <span className={className}>{name}</span>;
}

// Recipe Card Component (for Pinned and My Recipes)
function RecipeCard({ recipe, onPin }) {
  const typeInfo = getRecipeTypeInfo(recipe.recipe_type);
  const TypeIcon = typeInfo.icon;
  const slug = generateRecipeSlug(recipe.name);

  return (
    <div className="relative">
      <Link href={`/recipes/${slug}`} className="block">
        <Card className="h-full hover:bg-gray-50 transition-colors cursor-pointer group">
          <CardContent className="p-3">
            {/* Header with icon and name */}
            <div className="flex items-start gap-2 mb-1.5">
              <TypeIcon
                className={`w-4 h-4 flex-shrink-0 mt-0.5 ${
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
              <div className="flex-1 min-w-0">
                <h3 className="text-base font-medium text-blue-600 truncate group-hover:text-blue-800 transition-colors">
                  {recipe.name}
                </h3>
              </div>
            </div>

            {/* Bottom info section with even spacing */}
            <div className="space-y-1.5">
              {/* Type */}
              <div className="text-sm text-gray-500">{typeInfo.label}</div>

              {/* Description */}
              {recipe.description && (
                <p
                  className="text-sm text-gray-600 truncate"
                  title={recipe.description}
                >
                  {recipe.description}
                </p>
              )}

              {/* Authored by */}
              <div className="text-sm text-gray-500 truncate">
                Authored by{' '}
                <UserName name={recipe.user_name || recipe.user_id} />
              </div>

              {/* Last updated info - only show for editable recipes */}
              {recipe.is_editable && recipe.user_name !== 'local' && (
                <div className="text-sm text-gray-500 truncate">
                  Updated by{' '}
                  <UserName name={recipe.updated_by_name || recipe.user_name} />{' '}
                  <TimestampWithTooltip
                    date={
                      recipe.updated_at
                        ? new Date(recipe.updated_at * 1000)
                        : null
                    }
                  />
                </div>
              )}
            </div>
          </CardContent>
        </Card>
      </Link>
      {onPin && recipe.pinned && recipe.is_pinnable !== false && (
        <button
          onClick={(e) => {
            e.preventDefault();
            e.stopPropagation();
            onPin(recipe.name, false);
          }}
          className="absolute top-2 right-2 p-1 rounded transition-colors text-amber-500 hover:text-amber-700 hover:bg-amber-100"
          title="Unpin recipe"
        >
          <PinOffIcon className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

// Template Row Component (for Pinned and My Recipes)
function TemplateRow({
  title,
  icon: Icon,
  recipes,
  emptyMessage,
  iconColor,
  onPin,
}) {
  if (recipes.length === 0) {
    return (
      <div className="mb-6">
        <div className="flex items-center gap-2 mb-3">
          <Icon className={`w-4 h-4 ${iconColor}`} />
          <h2 className="text-base text-gray-700">{title}</h2>
          <span className="text-sm text-gray-500">({recipes.length})</span>
        </div>
        <div className="text-center py-6 bg-white rounded-lg border border-gray-200 shadow-sm">
          <p className="text-gray-500">{emptyMessage}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <Icon className={`w-4 h-4 ${iconColor}`} />
        <h2 className="text-base text-gray-700">{title}</h2>
        <span className="text-sm text-gray-500">({recipes.length})</span>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
        {recipes.map((recipe) => (
          <RecipeCard key={recipe.name} recipe={recipe} onPin={onPin} />
        ))}
      </div>
    </div>
  );
}

// All Recipes Section with Sortable Table and Filter Bar (same as clusters page)
function AllRecipesSection({ recipes, onPin, onDelete }) {
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [filters, setFilters] = useState([]);
  const [sortConfig, setSortConfig] = useState({
    key: 'updated_at',
    direction: 'descending',
  });

  // Extract option values from templates for the filter dropdown
  const optionValues = useMemo(() => {
    const names = new Set();
    const types = new Set();
    const owners = new Set();

    recipes.forEach((r) => {
      if (r.name) names.add(r.name);
      if (r.recipe_type) types.add(r.recipe_type);
      if (r.user_name) owners.add(r.user_name);
      else if (r.user_id) owners.add(r.user_id);
    });

    return {
      name: Array.from(names).sort(),
      recipe_type: Array.from(types).sort(),
      user_name: Array.from(owners).sort(),
    };
  }, [recipes]);

  const requestSort = (key) => {
    let direction = 'ascending';
    if (sortConfig.key === key && sortConfig.direction === 'ascending') {
      direction = 'descending';
    }
    setSortConfig({ key, direction });
  };

  const getSortDirection = (key) => {
    if (sortConfig.key === key) {
      return sortConfig.direction === 'ascending' ? ' ↑' : ' ↓';
    }
    return '';
  };

  // Filter and sort templates
  const sortedAndFilteredTemplates = useMemo(() => {
    let result = recipes;

    // Apply filters
    if (filters.length > 0) {
      result = result.filter((item) => {
        for (const filter of filters) {
          const propertyKey = filter.property.toLowerCase().replace(' ', '_');
          let itemValue = '';

          if (propertyKey === 'name') itemValue = item.name || '';
          else if (propertyKey === 'type') itemValue = item.recipe_type || '';
          else if (propertyKey === 'owner')
            itemValue = item.user_name || item.user_id || '';

          if (!itemValue.toLowerCase().includes(filter.value.toLowerCase())) {
            return false;
          }
        }
        return true;
      });
    }

    return sortData(result, sortConfig.key, sortConfig.direction);
  }, [recipes, filters, sortConfig]);

  return (
    <div className="mb-6">
      {/* Filter Bar */}
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-base text-gray-700">All Recipes</span>
        <div className="w-full sm:w-auto">
          <RecipeFilterDropdown
            propertyList={RECIPE_PROPERTY_OPTIONS}
            valueList={optionValues}
            setFilters={setFilters}
            placeholder="Filter recipes"
            filters={filters}
          />
        </div>
        <span className="text-sm text-gray-500 ml-auto">
          {sortedAndFilteredTemplates.length} of {recipes.length}
        </span>
      </div>

      {/* Filter Chips */}
      {filters.length > 0 && (
        <RecipeFilters filters={filters} setFilters={setFilters} />
      )}

      {/* Table */}
      <Card>
        <div className="overflow-x-auto rounded-lg">
          <Table className="min-w-full">
            <TableHeader>
              <TableRow>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('recipe_type')}
                >
                  Type{getSortDirection('recipe_type')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('name')}
                >
                  Name{getSortDirection('name')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('description')}
                >
                  Description{getSortDirection('description')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('user_name')}
                >
                  Owner{getSortDirection('user_name')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('updated_by_name')}
                >
                  Last Updated By{getSortDirection('updated_by_name')}
                </TableHead>
                <TableHead
                  className="sortable whitespace-nowrap cursor-pointer hover:bg-gray-50"
                  onClick={() => requestSort('updated_at')}
                >
                  Updated{getSortDirection('updated_at')}
                </TableHead>
                <TableHead className="whitespace-nowrap w-[100px]">
                  Actions
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedAndFilteredTemplates.length === 0 ? (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center py-6 text-gray-500"
                  >
                    {recipes.length === 0
                      ? 'No recipes available.'
                      : 'No recipes match your filter criteria.'}
                  </TableCell>
                </TableRow>
              ) : (
                sortedAndFilteredTemplates.map((recipe) => {
                  const typeInfo = getRecipeTypeInfo(recipe.recipe_type);
                  const TypeIcon = typeInfo.icon;
                  const slug = generateRecipeSlug(recipe.name);
                  const truncatedDesc = recipe.description
                    ? recipe.description.length > 80
                      ? recipe.description.substring(0, 80) + '...'
                      : recipe.description
                    : '-';
                  return (
                    <TableRow key={recipe.name} className="hover:bg-gray-50">
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <TypeIcon
                            className={`w-4 h-4 ${
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
                          <span>{typeInfo.label}</span>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Link
                          href={`/recipes/${slug}`}
                          className="text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {recipe.name}
                        </Link>
                      </TableCell>
                      <TableCell
                        className="text-gray-600 max-w-[400px]"
                        title={recipe.description || ''}
                      >
                        <span className="cursor-default">{truncatedDesc}</span>
                      </TableCell>
                      <TableCell className="text-gray-600">
                        <UserName name={recipe.user_name || recipe.user_id} />
                      </TableCell>
                      <TableCell className="text-gray-600">
                        {recipe.updated_by_name || recipe.user_name ? (
                          <UserName
                            name={recipe.updated_by_name || recipe.user_name}
                          />
                        ) : (
                          '-'
                        )}
                      </TableCell>
                      <TableCell className="text-gray-500">
                        <TimestampWithTooltip
                          date={
                            recipe.updated_at
                              ? new Date(recipe.updated_at * 1000)
                              : null
                          }
                        />
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1">
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (recipe.is_pinnable !== false) {
                                onPin(recipe.name, !recipe.pinned);
                              }
                            }}
                            disabled={recipe.is_pinnable === false}
                            className={`p-1 rounded transition-colors ${
                              recipe.is_pinnable === false
                                ? 'text-gray-300 cursor-not-allowed'
                                : recipe.pinned
                                  ? 'text-amber-500 hover:text-amber-700 hover:bg-amber-100'
                                  : 'text-amber-400 hover:text-amber-600 hover:bg-amber-100'
                            }`}
                            title={
                              recipe.is_pinnable === false
                                ? 'Default recipes cannot be pinned/unpinned'
                                : recipe.pinned
                                  ? 'Unpin recipe'
                                  : 'Pin recipe'
                            }
                          >
                            {recipe.pinned ? (
                              <PinOffIcon className="h-4 w-4" />
                            ) : (
                              <PinIcon className="h-4 w-4" />
                            )}
                          </button>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              if (recipe.is_editable !== false) {
                                setDeleteTarget(recipe);
                              }
                            }}
                            disabled={recipe.is_editable === false}
                            className={`p-1 rounded transition-colors ${
                              recipe.is_editable === false
                                ? 'text-gray-300 cursor-not-allowed'
                                : 'text-red-400 hover:text-red-700 hover:bg-red-100'
                            }`}
                            title={
                              recipe.is_editable === false
                                ? 'Default recipes cannot be deleted'
                                : 'Delete recipe'
                            }
                          >
                            <Trash2Icon className="h-4 w-4" />
                          </button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </Card>

      {/* Delete Confirmation Dialog */}
      <DeleteConfirmDialog
        recipe={deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onDelete={onDelete}
      />
    </div>
  );
}

// Inline delete confirmation dialog for the All Recipes table
function DeleteConfirmDialog({ recipe, onClose, onDelete }) {
  const [isDeleting, setIsDeleting] = useState(false);

  const handleDelete = async () => {
    setIsDeleting(true);
    try {
      await onDelete(recipe.name);
      onClose();
    } catch (error) {
      showToast(`Delete failed: ${error.message}`, 'error');
    } finally {
      setIsDeleting(false);
    }
  };

  if (!recipe) return null;

  return (
    <Dialog open={!!recipe} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-xl text-red-600">
            Delete Recipe
          </DialogTitle>
          <DialogDescription>
            Are you sure you want to delete &quot;{recipe.name}&quot;? This
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

// Filter Dropdown Component (same pattern as clusters page)
const RecipeFilterDropdown = ({
  propertyList = [],
  valueList,
  setFilters,
  placeholder = 'Filter recipes',
  filters = [],
}) => {
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);

  const [isOpen, setIsOpen] = useState(false);
  const [value, setValue] = useState('');
  const [propertyValue, setPropertyValue] = useState('name');
  const [valueOptions, setValueOptions] = useState([]);

  const getPropertyLabel = useCallback(
    (propValue) => {
      const propertyItem = propertyList.find(
        (item) => item.value === propValue
      );
      return propertyItem ? propertyItem.label : propValue;
    },
    [propertyList]
  );

  // Handle clicks outside the dropdown
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
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, []);

  useEffect(() => {
    let updatedValueOptions = [];

    // Get the property label for filtering existing selections
    const propertyLabel = getPropertyLabel(propertyValue);
    const selectedValues = filters
      .filter((filter) => filter.property === propertyLabel)
      .map((filter) => filter.value);

    if (valueList && typeof valueList === 'object') {
      const options = valueList[propertyValue] || [];
      updatedValueOptions = options.filter(
        (val) => !selectedValues.includes(val)
      );
    }

    // Filter options based on current input value
    if (value.trim() !== '') {
      updatedValueOptions = updatedValueOptions.filter(
        (item) =>
          item && item.toString().toLowerCase().includes(value.toLowerCase())
      );
    }

    setValueOptions(updatedValueOptions);
  }, [propertyValue, valueList, value, filters, getPropertyLabel]);

  const handleValueChange = (e) => {
    setValue(e.target.value);
    if (!isOpen) {
      setIsOpen(true);
    }
  };

  const handleInputFocus = () => {
    setIsOpen(true);
  };

  const handleOptionSelect = (option) => {
    setFilters((prevFilters) => [
      ...prevFilters,
      {
        property: getPropertyLabel(propertyValue),
        operator: ':',
        value: option,
      },
    ]);
    setIsOpen(false);
    setValue('');
    inputRef.current.focus();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && value.trim() !== '') {
      setFilters((prevFilters) => [
        ...prevFilters,
        {
          property: getPropertyLabel(propertyValue),
          operator: ':',
          value: value,
        },
      ]);
      setValue('');
      setIsOpen(false);
    } else if (e.key === 'Escape') {
      setIsOpen(false);
      inputRef.current.blur();
    }
  };

  return (
    <div className="flex flex-row border border-gray-300 rounded-md overflow-visible">
      <div className="border-r border-gray-300 flex-shrink-0">
        <Select onValueChange={setPropertyValue} value={propertyValue}>
          <SelectTrigger
            aria-label="Filter Property"
            className="focus:ring-0 focus:ring-offset-0 border-none rounded-l-md rounded-r-none w-20 sm:w-24 md:w-32 h-8 text-xs sm:text-sm"
          >
            <SelectValue placeholder="Name" />
          </SelectTrigger>
          <SelectContent>
            {propertyList.map((item, index) => (
              <SelectItem key={`property-item-${index}`} value={item.value}>
                {item.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <div className="relative flex-1">
        <input
          type="text"
          ref={inputRef}
          placeholder={placeholder}
          value={value}
          onChange={handleValueChange}
          onFocus={handleInputFocus}
          onKeyDown={handleKeyDown}
          className="h-8 w-full sm:w-96 px-3 pr-8 text-sm border-none rounded-l-none rounded-r-md focus:ring-0 focus:outline-none"
          autoComplete="off"
        />
        {value && (
          <button
            onClick={() => {
              setValue('');
              setIsOpen(false);
            }}
            className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
            title="Clear filter"
            tabIndex={-1}
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
        {isOpen && valueOptions.length > 0 && (
          <div
            ref={dropdownRef}
            className="absolute z-50 mt-1 w-full bg-white border border-gray-200 rounded-md shadow-lg max-h-60 overflow-y-auto"
            style={{ zIndex: 9999 }}
          >
            {valueOptions.map((option, index) => (
              <div
                key={`${option}-${index}`}
                className={`px-3 py-2 cursor-pointer hover:bg-gray-50 text-sm ${
                  index !== valueOptions.length - 1
                    ? 'border-b border-gray-100'
                    : ''
                }`}
                onClick={() => handleOptionSelect(option)}
              >
                <span className="text-sm text-gray-700">{option}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// Filter Chips Component (same pattern as clusters page)
const RecipeFilters = ({ filters = [], setFilters }) => {
  const onRemove = (index) => {
    setFilters((prevFilters) => prevFilters.filter((_, i) => i !== index));
  };

  const clearFilters = () => {
    setFilters([]);
  };

  return (
    <div className="flex items-center gap-4 py-2 px-2 mb-2">
      <div className="flex flex-wrap items-center gap-2">
        {filters.map((filter, index) => (
          <RecipeFilterItem
            key={`filteritem-${index}`}
            filter={filter}
            onRemove={() => onRemove(index)}
          />
        ))}
        {filters.length > 0 && (
          <button
            onClick={clearFilters}
            className="rounded-full px-4 py-1 text-sm text-gray-700 bg-gray-200 hover:bg-gray-300"
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
};

// Filter Item Component (same pattern as clusters page)
const RecipeFilterItem = ({ filter, onRemove }) => {
  return (
    <div className="flex items-center text-blue-600 bg-blue-100 px-1 py-1 rounded-full text-sm">
      <div className="flex items-center gap-1 px-2">
        <span>{filter.property}</span>
        <span>{filter.operator}</span>
        <span>{filter.value}</span>
      </div>
      <button
        onClick={onRemove}
        className="p-0.5 ml-1 text-gray-400 hover:text-gray-600 bg-blue-500 hover:bg-blue-600 rounded-full flex items-center"
        title="Remove filter"
      >
        <svg className="h-3 w-3" fill="none" stroke="white" viewBox="0 0 24 24">
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={5}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>
  );
};

// Helper to generate example YAML based on type
function getExampleRecipe(recipeType) {
  switch (recipeType) {
    case RecipeType.CLUSTER:
      return `name: my-cluster
resources:
  infra: aws
  accelerators: A100:1

run: |
  echo "Hello, SkyPilot!"
`;
    case RecipeType.JOB:
      return `name: my-job
resources:
  infra: aws
  accelerators: A100:1

run: |
  echo "Running managed job..."
`;
    case RecipeType.POOL:
      return `pool:
  name: my-pool

resources:
  infra: aws
  accelerators: A100:1
`;
    default:
      return `name: my-${recipeType}
resources:
  infra: aws
  accelerators: A100:1

run: |
  echo "Hello, SkyPilot!"
`;
  }
}

// Create YAML Modal with Owner Name field
function CreateRecipeModal({
  isOpen,
  onClose,
  onSubmit,
  initialData,
  isAuthenticated,
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState(getExampleRecipe(RecipeType.CLUSTER));
  const [recipeType, setRecipeType] = useState(RecipeType.CLUSTER);
  const [ownerName, setOwnerName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState(null);

  useEffect(() => {
    if (isOpen) {
      if (initialData) {
        setName(initialData.name || '');
        setDescription(initialData.description || '');
        setContent(initialData.content || '');
        setRecipeType(initialData.recipe_type || RecipeType.CLUSTER);
      } else {
        setName('');
        setDescription('');
        setRecipeType(RecipeType.CLUSTER);
        setContent(getExampleRecipe(RecipeType.CLUSTER));
      }
      setOwnerName('');
      setFormError(null);
    }
  }, [initialData, isOpen]);

  // Update example YAML when type changes (only if content matches previous example)
  const handleRecipeTypeChange = (newType) => {
    const oldExample = getExampleRecipe(recipeType);
    // If user hasn't modified the content, update it with new example
    if (content === oldExample || content === '') {
      setContent(getExampleRecipe(newType));
    }
    setRecipeType(newType);
  };

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
      await onSubmit({
        name,
        description: description || null,
        content,
        recipeType,
        ownerName: ownerName || null,
      });
      onClose();
    } catch (error) {
      setFormError(error.message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto px-8">
        <DialogHeader>
          <DialogTitle className="text-xl text-gray-900">
            Create New Recipe
          </DialogTitle>
          <DialogDescription>
            Create a reusable recipe for clusters, jobs, and more.
          </DialogDescription>
        </DialogHeader>

        <div className="bg-amber-50 border border-amber-200 rounded-md p-3 flex items-start gap-2 mt-4">
          <AlertTriangleIcon className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
          <p className="text-sm text-amber-800">
            This recipe will be visible to everyone with access to this
            dashboard.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="name">Name *</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => {
                  setName(e.target.value);
                  setFormError(null);
                }}
                placeholder="my-gpu-training"
                className="placeholder:text-gray-400"
                required
              />
            </div>

            <div className="space-y-2">
              <Label htmlFor="recipe-type">Type *</Label>
              <Select value={recipeType} onValueChange={handleRecipeTypeChange}>
                <SelectTrigger>
                  <SelectValue placeholder="Select type" />
                </SelectTrigger>
                <SelectContent>
                  {ALL_RECIPE_TYPES.map((type) => {
                    const info = getRecipeTypeInfo(type);
                    const TypeIcon = info.icon;
                    return (
                      <SelectItem key={type} value={type}>
                        <div className="flex items-center gap-2">
                          <TypeIcon className={`w-4 h-4 ${info.colorClass}`} />
                          <span>{info.fullLabel}</span>
                        </div>
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Owner Name field - only shown when not authenticated */}
          {!isAuthenticated && (
            <div className="space-y-2">
              <Label htmlFor="owner-name">Your Name</Label>
              <Input
                id="owner-name"
                value={ownerName}
                onChange={(e) => setOwnerName(e.target.value)}
                placeholder="Enter your name (optional)"
                className="placeholder:text-gray-400"
              />
              <p className="text-xs text-gray-500">
                This name will be shown as the template owner.
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Input
              id="description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="A brief description of what this recipe does..."
              className="placeholder:text-gray-400"
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
                  Creating...
                </>
              ) : (
                'Create Recipe'
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// Main Hub component
export function RecipeHub() {
  const router = useRouter();

  // Data state
  const [allRecipes, setAllRecipes] = useState([]);
  const [currentUserId, setCurrentUserId] = useState(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);

  // UI state
  const [loading, setLoading] = useState(true);
  const [lastFetchedTime, setLastFetchedTime] = useState(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [initialCreateData, setInitialCreateData] = useState(null);

  // Fetch current user ID
  const fetchCurrentUser = useCallback(async () => {
    try {
      const response = await fetch(
        `${window.location.origin}/internal/dashboard/users/role`
      );
      if (response.ok) {
        const data = await response.json();
        setCurrentUserId(data.id || 'local');
        // Check if user is authenticated (not 'local')
        setIsAuthenticated(data.id && data.id !== 'local');
      } else {
        setCurrentUserId('local');
        setIsAuthenticated(false);
      }
    } catch (error) {
      console.error('Failed to get user info:', error);
      setCurrentUserId('local');
      setIsAuthenticated(false);
    }
  }, []);

  // Fetch all data
  const fetchData = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true);
    try {
      const recipesData = await getRecipes();
      setAllRecipes(recipesData || []);
      setLastFetchedTime(new Date());
    } catch (error) {
      console.error('Error fetching recipes:', error);
      showToast(`Error loading recipes: ${error.message}`, 'error');
    } finally {
      if (showLoading) setLoading(false);
    }
  }, []);

  // Check for copy data in query params
  useEffect(() => {
    if (router.query.copy) {
      try {
        const copyData = JSON.parse(router.query.copy);
        setInitialCreateData(copyData);
        setIsCreateModalOpen(true);
        // Clear the query param
        router.replace('/recipes', undefined, { shallow: true });
      } catch (e) {
        console.error('Failed to parse copy data:', e);
      }
    }
  }, [router.query.copy, router]);

  useEffect(() => {
    fetchCurrentUser();
    fetchData(true);
  }, [fetchCurrentUser, fetchData]);

  // Separate templates into categories
  const pinnedRecipes = allRecipes.filter((r) => r.pinned);

  // When authenticated, separate user's non-pinned recipes into "My Recipes"
  // "All Recipes" always shows every recipe regardless of pinned/ownership status
  const myRecipes = isAuthenticated
    ? allRecipes.filter((r) => !r.pinned && r.user_id === currentUserId)
    : [];
  const otherRecipes = allRecipes;

  // Handlers
  const handleCreate = async (data) => {
    await createRecipe({
      ...data,
      // If ownerName is provided, pass it to override the user_name
      ...(data.ownerName ? { ownerName: data.ownerName } : {}),
    });
    showToast('Recipe created successfully!', 'success');
    await fetchData();
    setInitialCreateData(null);
  };

  const handleRefresh = () => {
    fetchData(true);
  };

  const handleCloseCreateModal = () => {
    setIsCreateModalOpen(false);
    setInitialCreateData(null);
  };

  const handlePin = async (recipeName, pinned) => {
    try {
      const updated = await togglePinRecipe(recipeName, pinned);
      if (updated) {
        showToast(pinned ? 'Recipe pinned!' : 'Recipe unpinned!', 'success');
        await fetchData();
      }
    } catch (error) {
      showToast(`Pin operation failed: ${error.message}`, 'error');
    }
  };

  const handleDelete = async (recipeName) => {
    const deleted = await deleteRecipe(recipeName);
    if (deleted) {
      showToast('Recipe deleted successfully!', 'success');
      await fetchData();
    } else {
      throw new Error('Failed to delete recipe');
    }
  };

  // Empty state
  const EmptyState = () => (
    <div className="text-center py-12">
      <p className="text-gray-500 mb-4">No recipes yet</p>
      <button
        onClick={() => setIsCreateModalOpen(true)}
        className="bg-sky-600 hover:bg-sky-700 text-white flex items-center justify-center mx-auto rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-200"
      >
        <PlusIcon className="h-4 w-4 mr-2" />
        Create Your First Template
      </button>
    </div>
  );

  if (loading && allRecipes.length === 0) {
    return (
      <div className="flex justify-center items-center h-64">
        <CircularProgress size={20} className="mr-2" />
        <span className="text-gray-500">Loading...</span>
      </div>
    );
  }

  return (
    <div className="h-full">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 mb-4 min-h-[20px]">
        <div className="text-base flex items-center">
          <span className="text-sky-blue leading-none text-base">Recipes</span>
        </div>

        <div className="flex items-center gap-2 ml-auto">
          {loading && (
            <div className="flex items-center mr-2">
              <CircularProgress size={16} />
              <span className="ml-2 text-gray-500 text-sm">Refreshing...</span>
            </div>
          )}
          {!loading && lastFetchedTime && (
            <LastUpdatedTimestamp
              timestamp={lastFetchedTime}
              className="mr-2"
            />
          )}
          <button
            onClick={handleRefresh}
            disabled={loading}
            className="text-sky-blue hover:text-sky-blue-bright flex items-center"
          >
            <RotateCwIcon className="h-4 w-4 mr-1.5" />
            <span>Refresh</span>
          </button>
          <button
            onClick={() => setIsCreateModalOpen(true)}
            className="ml-4 bg-sky-600 hover:bg-sky-700 text-white flex items-center rounded-md px-3 py-1 text-sm font-medium transition-colors duration-200"
            title="New Recipe"
          >
            <PlusIcon className="h-4 w-4 mr-2" />
            New Recipe
          </button>
        </div>
      </div>

      {allRecipes.length === 0 ? (
        <Card>
          <EmptyState />
        </Card>
      ) : (
        <div>
          {/* Pinned Recipes */}
          <TemplateRow
            title="Pinned Recipes"
            icon={PinIcon}
            iconColor="text-amber-500"
            recipes={pinnedRecipes}
            emptyMessage="No pinned recipes. Pin important recipes for quick access."
            onPin={handlePin}
          />

          {/* My Recipes - only shown when authenticated */}
          {isAuthenticated && (
            <TemplateRow
              title="My Recipes"
              icon={FileCode}
              iconColor="text-sky-500"
              recipes={myRecipes}
              emptyMessage="You haven't created any recipes yet."
            />
          )}

          {/* All Recipes - Searchable List */}
          <AllRecipesSection
            recipes={otherRecipes}
            onPin={handlePin}
            onDelete={handleDelete}
          />
        </div>
      )}

      {/* Create Modal */}
      <CreateRecipeModal
        isOpen={isCreateModalOpen}
        onClose={handleCloseCreateModal}
        onSubmit={handleCreate}
        initialData={initialCreateData}
        isAuthenticated={isAuthenticated}
      />
    </div>
  );
}
