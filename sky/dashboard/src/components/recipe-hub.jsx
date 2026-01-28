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
  ServerIcon,
  BriefcaseIcon,
  LayersIcon,
  AlertTriangleIcon,
  DatabaseIcon,
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
import { LastUpdatedTimestamp } from '@/components/utils';
import { showToast } from '@/data/connectors/toast';

import {
  getRecipes,
  createRecipe,
  getCategories,
} from '@/data/connectors/recipes';
import {
  RecipeType,
  getRecipeTypeInfo,
  capitalizeWords,
} from '@/data/constants/recipeTypes';

// Define filter options for the YAML filter dropdown
const RECIPE_PROPERTY_OPTIONS = [
  { label: 'Name', value: 'name' },
  { label: 'Type', value: 'recipe_type' },
  { label: 'Category', value: 'category' },
  { label: 'Owner', value: 'user_name' },
];

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

// Generate URL slug from template
function generateRecipeSlug(name, id) {
  const slugifiedName = name
    .toLowerCase()
    .replace(/\s+/g, '-')
    .replace(/[^a-z0-9-]/g, '')
    .substring(0, 50);
  return `${slugifiedName}-${id}`;
}


// Recipe Card Component (for Pinned and My Recipes)
function RecipeCard({ recipe }) {
  const typeInfo = getRecipeTypeInfo(recipe.recipe_type);
  const TypeIcon = typeInfo.icon;
  const slug = generateRecipeSlug(recipe.name, recipe.id);

  return (
    <Link href={`/recipes/${slug}`} className="block">
      <Card className="h-full hover:bg-gray-50 transition-colors cursor-pointer group">
        <CardContent className="p-3">
          {/* Header with icon and name */}
          <div className="flex items-start gap-2 mb-2">
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
              <div className="flex items-center gap-2">
                <h3 className="text-base font-medium text-blue-600 truncate group-hover:text-blue-800 transition-colors">
                  {recipe.name}
                </h3>
                {recipe.pinned && (
                  <PinIcon className="w-4 h-4 text-amber-500 flex-shrink-0" />
                )}
              </div>
              <div className="flex items-center gap-2 mt-1">
                <span className="text-sm text-gray-500">{typeInfo.label}</span>
                {recipe.category && (
                  <>
                    <span className="text-sm text-gray-300">·</span>
                    <span className="text-sm text-gray-500">
                      {capitalizeWords(recipe.category)}
                    </span>
                  </>
                )}
              </div>
            </div>
          </div>

          {/* Bottom info section with even spacing */}
          <div className="space-y-1.5 mt-2">
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
              Authored by {recipe.user_name || recipe.user_id || 'Unknown'}
            </div>

            {/* Last updated info */}
            <div className="text-sm text-gray-500 truncate">
              <span
                className="border-b border-dotted border-gray-400 cursor-help"
                title={`Updated by ${recipe.updated_by_name || recipe.user_name || 'Unknown'} ${formatRelativeTime(recipe.updated_at)}`}
              >
                Updated by{' '}
                {recipe.updated_by_name || recipe.user_name || 'Unknown'}{' '}
                {formatRelativeTime(recipe.updated_at)}
              </span>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

// Template Row Component (for Pinned and My Recipes)
function TemplateRow({
  title,
  icon: Icon,
  recipes,
  emptyMessage,
  iconColor,
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
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-3">
        {recipes.map((recipe) => (
          <RecipeCard key={recipe.id} recipe={recipe} />
        ))}
      </div>
    </div>
  );
}

// All Recipes Section with Sortable Table and Filter Bar (same as clusters page)
function AllRecipesSection({ recipes }) {
  const [filters, setFilters] = useState([]);
  const [sortConfig, setSortConfig] = useState({
    key: 'updated_at',
    direction: 'descending',
  });

  // Extract option values from templates for the filter dropdown
  const optionValues = useMemo(() => {
    const names = new Set();
    const types = new Set();
    const categories = new Set();
    const owners = new Set();

    recipes.forEach((r) => {
      if (r.name) names.add(r.name);
      if (r.recipe_type) types.add(r.recipe_type);
      if (r.category) categories.add(r.category);
      if (r.user_name) owners.add(r.user_name);
      else if (r.user_id) owners.add(r.user_id);
    });

    return {
      name: Array.from(names).sort(),
      recipe_type: Array.from(types).sort(),
      category: Array.from(categories).sort(),
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
          else if (propertyKey === 'category') itemValue = item.category || '';
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
                  onClick={() => requestSort('category')}
                >
                  Category{getSortDirection('category')}
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
                      ? 'No recipes from other users.'
                      : 'No recipes match your filter criteria.'}
                  </TableCell>
                </TableRow>
              ) : (
                sortedAndFilteredTemplates.map((recipe) => {
                  const typeInfo = getRecipeTypeInfo(recipe.recipe_type);
                  const TypeIcon = typeInfo.icon;
                  const slug = generateRecipeSlug(recipe.name, recipe.id);
                  const truncatedDesc = recipe.description
                    ? recipe.description.length > 30
                      ? recipe.description.substring(0, 30) + '...'
                      : recipe.description
                    : '-';
                  return (
                    <TableRow key={recipe.id} className="hover:bg-gray-50">
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
                        className="text-gray-600 max-w-[200px]"
                        title={recipe.description || ''}
                      >
                        <span className="cursor-default">{truncatedDesc}</span>
                      </TableCell>
                      <TableCell className="text-gray-600">
                        {recipe.category
                          ? capitalizeWords(recipe.category)
                          : '-'}
                      </TableCell>
                      <TableCell className="text-gray-600">
                        {recipe.user_name || recipe.user_id || 'Unknown'}
                      </TableCell>
                      <TableCell className="text-gray-600">
                        {recipe.updated_by_name || recipe.user_name || '-'}
                      </TableCell>
                      <TableCell className="text-gray-500">
                        {formatRelativeTime(recipe.updated_at)}
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </div>
      </Card>
    </div>
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
  cloud: aws
  accelerators: A100:1

run: |
  echo "Hello, SkyPilot!"
`;
    case RecipeType.JOB:
      return `name: my-job
resources:
  cloud: aws
  accelerators: A100:1

run: |
  echo "Running managed job..."
`;
    case RecipeType.POOL:
      return `pool:
  name: my-pool

resources:
  cloud: aws
  accelerators: A100:1
`;
    default:
      return `name: my-${recipeType}
resources:
  cloud: aws
  accelerators: A100:1

run: |
  echo "Hello, SkyPilot!"
`;
  }
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

// Create YAML Modal with Category Combobox and Owner Name field
function CreateRecipeModal({
  isOpen,
  onClose,
  onSubmit,
  initialData,
  predefinedCategories,
  customCategories,
  isAuthenticated,
}) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [content, setContent] = useState(getExampleRecipe(RecipeType.CLUSTER));
  const [recipeType, setRecipeType] = useState(RecipeType.CLUSTER);
  const [category, setCategory] = useState('');
  const [ownerName, setOwnerName] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (isOpen) {
      if (initialData) {
        setName(initialData.name || '');
        setDescription(initialData.description || '');
        setContent(initialData.content || '');
        setRecipeType(initialData.recipe_type || RecipeType.CLUSTER);
        setCategory(initialData.category || '');
      } else {
        setName('');
        setDescription('');
        setRecipeType(RecipeType.CLUSTER);
        setContent(getExampleRecipe(RecipeType.CLUSTER));
        setCategory('');
      }
      setOwnerName('');
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

    // Validate YAML syntax
    try {
      yaml.load(content);
    } catch (yamlError) {
      showToast(`Invalid YAML: ${yamlError.message}`, 'error');
      setIsSubmitting(false);
      return;
    }

    try {
      await onSubmit({
        name,
        description: description || null,
        content,
        recipeType,
        category: category || null,
        ownerName: ownerName || null,
      });
      onClose();
    } catch (error) {
      showToast(`Error: ${error.message}`, 'error');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] overflow-y-auto">
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
                onChange={(e) => setName(e.target.value)}
                placeholder="My GPU Training"
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
                  <SelectItem value={RecipeType.CLUSTER}>
                    <div className="flex items-center gap-2">
                      <ServerIcon className="w-4 h-4 text-sky-600" />
                      <span>Cluster</span>
                    </div>
                  </SelectItem>
                  <SelectItem value={RecipeType.JOB}>
                    <div className="flex items-center gap-2">
                      <BriefcaseIcon className="w-4 h-4 text-purple-600" />
                      <span>Managed Job</span>
                    </div>
                  </SelectItem>
                  <SelectItem value={RecipeType.POOL}>
                    <div className="flex items-center gap-2">
                      <LayersIcon className="w-4 h-4 text-orange-600" />
                      <span>Job Pool</span>
                    </div>
                  </SelectItem>
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
            <Label htmlFor="category">Category</Label>
            <CategoryCombobox
              value={category}
              onChange={setCategory}
              predefinedCategories={predefinedCategories}
              customCategories={customCategories}
            />
            <p className="text-xs text-gray-500">
              Select a predefined category or type a custom one
            </p>
          </div>

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
              onChange={setContent}
              minHeight="300px"
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
  const [predefinedCategories, setPredefinedCategories] = useState([]);
  const [customCategories, setCustomCategories] = useState([]);
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
      const [recipesData, categoriesData] = await Promise.all([
        getRecipes(),
        getCategories(),
      ]);
      setAllRecipes(recipesData || []);
      setPredefinedCategories(categoriesData?.predefined || []);
      setCustomCategories(categoriesData?.custom || []);
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

  // When not authenticated, show all non-pinned recipes in "All Recipes"
  // When authenticated, separate into "My Recipes" and "All Recipes"
  const myRecipes = isAuthenticated
    ? allRecipes.filter((r) => !r.pinned && r.user_id === currentUserId)
    : [];
  const otherRecipes = isAuthenticated
    ? allRecipes.filter((r) => !r.pinned && r.user_id !== currentUserId)
    : allRecipes.filter((r) => !r.pinned);

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
          />

          {/* My Recipes - only shown when authenticated */}
          {isAuthenticated && (
            <TemplateRow
              title="My Recipes"
              icon={FileCodeIcon}
              iconColor="text-sky-500"
              recipes={myRecipes}
              emptyMessage="You haven't created any recipes yet."
            />
          )}

          {/* All Recipes - Searchable List */}
          <AllRecipesSection recipes={otherRecipes} />
        </div>
      )}

      {/* Create Modal */}
      <CreateRecipeModal
        isOpen={isCreateModalOpen}
        onClose={handleCloseCreateModal}
        onSubmit={handleCreate}
        initialData={initialCreateData}
        predefinedCategories={predefinedCategories}
        customCategories={customCategories}
        isAuthenticated={isAuthenticated}
      />
    </div>
  );
}