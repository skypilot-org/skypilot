import React, { useState, useRef, useEffect } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

const ALL_VALUES = '__ALL_VALUES__';

export function SearchColumnFilter({ 
  value, 
  onChange, 
  placeholder = "Filter...",
  className = ""
}) {
  const [isOpen, setIsOpen] = useState(false);
  const inputRef = useRef(null);

  const handleToggle = (e) => {
    e.stopPropagation();
    setIsOpen(!isOpen);
    if (!isOpen) {
      // Focus input when opening
      setTimeout(() => inputRef.current?.focus(), 100);
    }
  };

  const handleChange = (e) => {
    onChange(e.target.value);
  };

  const handleClear = (e) => {
    e.stopPropagation();
    onChange('');
  };

  return (
    <div className={`relative inline-block ${className}`}>
      <button
        onClick={handleToggle}
        className={`ml-2 p-1 rounded hover:bg-gray-100 ${
          value ? 'text-sky-600' : 'text-gray-400'
        }`}
        title="Filter"
      >
        <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
          <path
            fillRule="evenodd"
            d="M8 4a4 4 0 100 8 4 4 0 000-8zM2 8a6 6 0 1110.89 3.476l4.817 4.817a1 1 0 01-1.414 1.414l-4.816-4.816A6 6 0 012 8z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      
      {isOpen && (
        <div className="absolute top-8 left-0 z-50 bg-white border border-gray-200 rounded-md shadow-lg p-2 min-w-48">
          <div className="relative">
            <input
              ref={inputRef}
              type="text"
              placeholder={placeholder}
              value={value}
              onChange={handleChange}
              className="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none"
            />
            {value && (
              <button
                onClick={handleClear}
                className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
                  <path
                    fillRule="evenodd"
                    d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            )}
          </div>
          <div className="mt-2 flex justify-end">
            <button
              onClick={() => setIsOpen(false)}
              className="px-2 py-1 text-xs text-gray-500 hover:text-gray-700"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export function DropdownColumnFilter({ 
  value, 
  onChange, 
  options = [],
  allLabel = "All",
  className = "",
  getDisplayValue = (option) => option.display || option.value || option
}) {
  const [isOpen, setIsOpen] = useState(false);

  const handleToggle = (e) => {
    e.stopPropagation();
    setIsOpen(!isOpen);
  };

  const handleSelect = (selectedValue) => {
    onChange(selectedValue);
    setIsOpen(false);
  };

  const currentValue = value === ALL_VALUES ? allLabel : 
    getDisplayValue(options.find(opt => (opt.value || opt.userId || opt) === value) || { value });

  return (
    <div className={`relative inline-block ${className}`}>
      <button
        onClick={handleToggle}
        className={`ml-2 p-1 rounded hover:bg-gray-100 ${
          value && value !== ALL_VALUES ? 'text-sky-600' : 'text-gray-400'
        }`}
        title="Filter"
      >
        <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
          <path
            fillRule="evenodd"
            d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L12 11.414V15a1 1 0 01-.293.707l-2 2A1 1 0 018 17v-5.586L3.293 6.707A1 1 0 013 6V3z"
            clipRule="evenodd"
          />
        </svg>
      </button>
      
      {isOpen && (
        <div className="absolute top-8 left-0 z-50 bg-white border border-gray-200 rounded-md shadow-lg min-w-48 max-h-64 overflow-y-auto">
          <div className="py-1">
            <button
              onClick={() => handleSelect(ALL_VALUES)}
              className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-100 ${
                value === ALL_VALUES ? 'bg-sky-50 text-sky-700' : ''
              }`}
            >
              {allLabel}
            </button>
            {options.map((option, index) => {
              const optionValue = option.value || option.userId || option;
              const optionDisplay = getDisplayValue(option);
              return (
                <button
                  key={index}
                  onClick={() => handleSelect(optionValue)}
                  className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-100 ${
                    value === optionValue ? 'bg-sky-50 text-sky-700' : ''
                  }`}
                >
                  {optionDisplay}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function MultiSelectColumnFilter({ 
  selectedValues = [],
  onChange,
  options = [],
  allLabel = "All",
  className = "",
  getDisplayValue = (option) => option.display || option.value || option,
  getOptionValue = (option) => option.value || option
}) {
  const [isOpen, setIsOpen] = useState(false);

  const handleToggle = (e) => {
    e.stopPropagation();
    setIsOpen(!isOpen);
  };

  const handleSelectAll = () => {
    onChange([]);
  };

  const handleSelectOption = (optionValue) => {
    if (selectedValues.includes(optionValue)) {
      onChange(selectedValues.filter(v => v !== optionValue));
    } else {
      onChange([...selectedValues, optionValue]);
    }
  };

  const isAllSelected = selectedValues.length === 0;

  return (
    <div className={`relative inline-block ${className}`}>
      <button
        onClick={handleToggle}
        className={`ml-2 p-1 rounded hover:bg-gray-100 ${
          !isAllSelected ? 'text-sky-600' : 'text-gray-400'
        }`}
        title="Filter"
      >
        <svg className="h-3 w-3" fill="currentColor" viewBox="0 0 20 20">
          <path
            fillRule="evenodd"
            d="M3 3a1 1 0 011-1h12a1 1 0 011 1v3a1 1 0 01-.293.707L12 11.414V15a1 1 0 01-.293.707l-2 2A1 1 0 018 17v-5.586L3.293 6.707A1 1 0 013 6V3z"
            clipRule="evenodd"
          />
        </svg>
        {!isAllSelected && (
          <span className="absolute -top-1 -right-1 bg-sky-500 text-white text-xs rounded-full h-4 w-4 flex items-center justify-center">
            {selectedValues.length}
          </span>
        )}
      </button>
      
      {isOpen && (
        <div className="absolute top-8 left-0 z-50 bg-white border border-gray-200 rounded-md shadow-lg min-w-48 max-h-64 overflow-y-auto">
          <div className="py-1">
            <label className="flex items-center px-3 py-2 text-sm hover:bg-gray-100 cursor-pointer">
              <input
                type="checkbox"
                checked={isAllSelected}
                onChange={handleSelectAll}
                className="mr-2"
              />
              {allLabel}
            </label>
            {options.map((option, index) => {
              const optionValue = getOptionValue(option);
              const optionDisplay = getDisplayValue(option);
              const isSelected = selectedValues.includes(optionValue);
              return (
                <label
                  key={index}
                  className="flex items-center px-3 py-2 text-sm hover:bg-gray-100 cursor-pointer"
                >
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => handleSelectOption(optionValue)}
                    className="mr-2"
                  />
                  {optionDisplay}
                </label>
              );
            })}
          </div>
          <div className="border-t border-gray-100 px-3 py-2">
            <button
              onClick={() => setIsOpen(false)}
              className="text-xs text-gray-500 hover:text-gray-700"
            >
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// Hook to close dropdowns when clicking outside
export function useClickOutside(refs, handler) {
  useEffect(() => {
    const handleClickOutside = (event) => {
      const isOutside = refs.every(ref => 
        ref.current && !ref.current.contains(event.target)
      );
      if (isOutside) {
        handler();
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [refs, handler]);
}

export { ALL_VALUES };