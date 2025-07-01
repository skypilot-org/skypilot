import React, { useState, useRef, useEffect } from 'react';
import { Search, Filter, X, ChevronDown, Trash2 } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';

// Debounced input component for performance
const DebouncedInput = ({
  value: initialValue,
  onChange,
  debounce = 300,
  placeholder,
  className,
  ...props
}) => {
  const [value, setValue] = useState(initialValue);

  useEffect(() => {
    setValue(initialValue);
  }, [initialValue]);

  useEffect(() => {
    const timeout = setTimeout(() => {
      onChange(value);
    }, debounce);

    return () => clearTimeout(timeout);
  }, [value, debounce, onChange]);

  return (
    <input
      {...props}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      placeholder={placeholder}
      className={className}
    />
  );
};

// Filter chip component for showing active filters
const FilterChip = ({ label, value, onRemove }) => (
  <div className="inline-flex items-center gap-1 px-2 py-1 bg-sky-100 text-sky-800 rounded-md text-sm border border-sky-200">
    <span className="font-medium">{label}:</span>
    <span className="truncate max-w-24">{value}</span>
    <button
      onClick={onRemove}
      className="ml-1 hover:bg-sky-200 rounded p-0.5 transition-colors"
      title="Remove filter"
    >
      <X className="h-3 w-3" />
    </button>
  </div>
);

// Main filter system component
export const FilterSystem = ({
  filters,
  onFiltersChange,
  filterConfig,
  className = '',
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownRef = useRef(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setIsOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Update a single filter
  const updateFilter = (key, value) => {
    const newFilters = { ...filters };
    if (value === '' || value === null || value === undefined) {
      delete newFilters[key];
    } else {
      newFilters[key] = value;
    }
    onFiltersChange(newFilters);
  };

  // Clear all filters
  const clearAllFilters = () => {
    onFiltersChange({});
    setIsOpen(false);
  };

  // Get active filter count
  const activeFilterCount = Object.keys(filters).length;

  // Get active filter chips
  const getActiveFilterChips = () => {
    const filterEntries = Object.keys(filters).map(key => [key, filters[key]]);
    return filterEntries.map(([key, value]) => {
      const config = filterConfig.find((f) => f.key === key);
      if (!config || !value) return null;
      
      let displayValue = value;
      if (config.type === 'select' && config.options) {
        const option = config.options.find((opt) => opt.value === value);
        displayValue = option ? option.label : value;
      }

      return (
        <FilterChip
          key={key}
          label={config.label}
          value={displayValue}
          onRemove={() => updateFilter(key, null)}
        />
      );
    }).filter(Boolean);
  };

  // Render filter input based on type
  const renderFilterInput = (config) => {
    const value = filters[config.key] || '';

    switch (config.type) {
      case 'text':
        return (
          <DebouncedInput
            type="text"
            value={value}
            onChange={(newValue) => updateFilter(config.key, newValue)}
            placeholder={config.placeholder || `Search ${config.label.toLowerCase()}...`}
            className="w-full px-3 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none"
          />
        );

      case 'select':
        return (
          <Select
            value={value}
            onValueChange={(newValue) => updateFilter(config.key, newValue)}
          >
            <SelectTrigger className="w-full text-sm">
              <SelectValue placeholder={`Select ${config.label.toLowerCase()}...`} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="">All {config.label}</SelectItem>
              {config.options?.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );

      case 'multiSelect':
        // TODO: Implement multi-select if needed
        return null;

      case 'dateRange':
        // TODO: Implement date range if needed
        return null;

      default:
        return null;
    }
  };

  return (
    <div className={`relative ${className}`}>
      {/* Filter trigger button and chips */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative" ref={dropdownRef}>
          <Button
            variant="outline"
            onClick={() => setIsOpen(!isOpen)}
            className={`h-8 px-3 text-sm flex items-center gap-2 ${
              activeFilterCount > 0 ? 'border-sky-500 bg-sky-50' : ''
            }`}
          >
            <Filter className="h-4 w-4" />
            <span>Filters</span>
            {activeFilterCount > 0 && (
              <span className="bg-sky-500 text-white rounded-full text-xs px-1.5 py-0.5 min-w-[1.25rem] h-5 flex items-center justify-center">
                {activeFilterCount}
              </span>
            )}
            <ChevronDown className={`h-4 w-4 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
          </Button>

          {/* Filter dropdown */}
          {isOpen && (
            <Card className="absolute top-full left-0 mt-1 w-80 z-50 p-4 shadow-lg border bg-white">
              <div className="space-y-4">
                {/* Header with clear all */}
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium text-gray-700">Filter Options</h3>
                  {activeFilterCount > 0 && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={clearAllFilters}
                      className="h-auto p-1 text-xs text-gray-500 hover:text-gray-700"
                    >
                      <Trash2 className="h-3 w-3 mr-1" />
                      Clear all
                    </Button>
                  )}
                </div>

                {/* Filter inputs */}
                <div className="space-y-3">
                  {filterConfig.map((config) => (
                    <div key={config.key} className="space-y-1">
                      <label className="text-xs font-medium text-gray-600 block">
                        {config.label}
                      </label>
                      {renderFilterInput(config)}
                    </div>
                  ))}
                </div>

                {/* Footer with apply button (optional) */}
                <div className="flex justify-end pt-2 border-t">
                  <Button
                    size="sm"
                    onClick={() => setIsOpen(false)}
                    className="h-7 px-3 text-xs"
                  >
                    Done
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Active filter chips */}
        {getActiveFilterChips()}
      </div>
    </div>
  );
};

// Enhanced search input component
export const SearchInput = ({ 
  value, 
  onChange, 
  placeholder = "Search...", 
  className = "" 
}) => {
  return (
    <div className="relative">
      <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
      <DebouncedInput
        type="text"
        value={value}
        onChange={onChange}
        placeholder={placeholder}
        className={`pl-9 pr-4 py-2 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none ${className}`}
      />
      {value && (
        <button
          onClick={() => onChange('')}
          className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
          title="Clear search"
        >
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
};

export default FilterSystem;