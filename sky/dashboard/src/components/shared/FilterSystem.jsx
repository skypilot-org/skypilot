import React, { useState, useEffect, useRef } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

// Utility: checks a condition based on operator
export const evaluateCondition = (item, filter) => {
  const { property, operator, value } = filter;

  if (!value) return true; // skip empty filters

  // Global search: check all values
  if (!property) {
    const strValue = value.toLowerCase();
    return Object.values(item).some((val) =>
      val?.toString().toLowerCase().includes(strValue)
    );
  }

  const itemValue = item[property.toLowerCase()]?.toString().toLowerCase();
  const filterValue = value.toString().toLowerCase();

  switch (operator) {
    case '=':
      return itemValue === filterValue;
    case ':':
      return itemValue?.includes(filterValue);
    default:
      return true;
  }
};

// Main filter function
export const filterData = (data, filters) => {
  if (filters.length === 0) {
    return data;
  }

  return data.filter((item) => {
    let result = null;

    for (let i = 0; i < filters.length; i++) {
      const filter = filters[i];
      const current = evaluateCondition(item, filter);

      if (result === null) {
        result = current;
      } else {
        result = result && current;
      }
    }

    return result;
  });
};

// URL parameter handling utilities
export const updateURLParams = (router, filters) => {
  const query = { ...router.query };

  let properties = [];
  let operators = [];
  let values = [];

  filters.map((filter, _index) => {
    properties.push(filter.property.toLowerCase() ?? '');
    operators.push(filter.operator);
    values.push(filter.value);
  });

  query.property = properties;
  query.operator = operators;
  query.value = values;

  // Use replace to avoid adding to browser history for filter changes
  router.replace(
    {
      pathname: router.pathname,
      query,
    },
    undefined,
    { shallow: true }
  );
};

export const updateFiltersByURLParams = (router, propertyMap) => {
  const query = { ...router.query };

  const properties = query.property;
  const operators = query.operator;
  const values = query.value;

  if (properties === undefined) {
    return [];
  }

  let filters = [];

  const length = Array.isArray(properties) ? properties.length : 1;

  if (length === 1) {
    filters.push({
      property: propertyMap.get(properties),
      operator: operators,
      value: values,
    });
  } else {
    for (let i = 0; i < length; i++) {
      filters.push({
        property: propertyMap.get(properties[i]),
        operator: operators[i],
        value: values[i],
      });
    }
  }

  return filters;
};

// Helper function to build a URL with a filter for a specific property
export const buildFilterUrl = (basePath, property, operator, value) => {
  if (!value) return basePath;
  const params = new URLSearchParams({
    property: property.toLowerCase(),
    operator: operator,
    value: value,
  });
  return `${basePath}?${params.toString()}`;
};

export const FilterDropdown = ({
  propertyList = [],
  valueList,
  setFilters,
  updateURLParams,
  placeholder = 'Filter items',
}) => {
  const inputRef = useRef(null);
  const dropdownRef = useRef(null);

  const [isOpen, setIsOpen] = useState(false);
  const [value, setValue] = useState('');
  const [propertyValue, setPropertyValue] = useState(
    propertyList[0]?.value || 'status'
  );
  const [valueOptions, setValueOptions] = useState([]);

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

    if (valueList && typeof valueList === 'object') {
      updatedValueOptions = valueList[propertyValue] || [];
    }

    // Filter options based on current input value
    if (value.trim() !== '') {
      updatedValueOptions = updatedValueOptions.filter(
        (item) =>
          item && item.toString().toLowerCase().includes(value.toLowerCase())
      );
    }

    setValueOptions(updatedValueOptions);
  }, [propertyValue, valueList, value]);

  // Helper function to get the capitalized label for a property value
  const getPropertyLabel = (propertyValue) => {
    const propertyItem = propertyList.find(
      (item) => item.value === propertyValue
    );
    return propertyItem ? propertyItem.label : propertyValue;
  };

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
    setFilters((prevFilters) => {
      const updatedFilters = [
        ...prevFilters,
        {
          property: getPropertyLabel(propertyValue),
          operator: ':',
          value: option,
        },
      ];

      updateURLParams(updatedFilters);
      return updatedFilters;
    });
    setIsOpen(false);
    setValue('');
    inputRef.current.focus();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && value.trim() !== '') {
      setFilters((prevFilters) => {
        const updatedFilters = [
          ...prevFilters,
          {
            property: getPropertyLabel(propertyValue),
            operator: ':',
            value: value,
          },
        ];

        updateURLParams(updatedFilters);
        return updatedFilters;
      });
      setValue('');
      setIsOpen(false);
    } else if (e.key === 'Escape') {
      setIsOpen(false);
      inputRef.current.blur();
    }
  };

  return (
    <div className="flex flex-row border border-gray-300 rounded-md overflow-visible bg-white">
      <div className="border-r border-gray-300 flex-shrink-0">
        <Select onValueChange={setPropertyValue} value={propertyValue}>
          <SelectTrigger
            aria-label="Filter Property"
            className="focus:ring-0 focus:ring-offset-0 border-none rounded-l-md rounded-r-none w-20 sm:w-24 md:w-32 h-8 text-xs sm:text-sm bg-white"
          >
            <SelectValue placeholder={propertyList[0]?.label || 'Status'} />
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
          className="h-8 w-full px-3 pr-8 text-sm border-none rounded-l-none rounded-r-md focus:ring-0 focus:outline-none"
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

export const Filters = ({ filters = [], setFilters, updateURLParams }) => {
  const onRemove = (index) => {
    setFilters((prevFilters) => {
      const updatedFilters = prevFilters.filter(
        (_, _index) => _index !== index
      );

      updateURLParams(updatedFilters);

      return updatedFilters;
    });
  };

  const clearFilters = () => {
    updateURLParams([]);
    setFilters([]);
  };

  return (
    <>
      <div className="flex items-center gap-4 py-2 px-2">
        <div className="flex flex-wrap items-content gap-2">
          {filters.map((filter, _index) => (
            <FilterItem
              key={`filteritem-${_index}`}
              filter={filter}
              onRemove={() => onRemove(_index)}
            />
          ))}

          {filters.length > 0 && (
            <>
              <button
                onClick={clearFilters}
                className="rounded-full px-4 py-1 text-sm text-gray-700 bg-gray-200 hover:bg-gray-300"
              >
                Clear filters
              </button>
            </>
          )}
        </div>
      </div>
    </>
  );
};

export const FilterItem = ({ filter, onRemove }) => {
  return (
    <>
      <div className="flex items-center text-blue-600 bg-blue-100 px-1 py-1 rounded-full text-sm">
        <div className="flex items-center gap-1 px-2">
          <span>{`${filter.property} `}</span>
          <span>{`${filter.operator} `}</span>
          <span>{` ${filter.value}`}</span>
        </div>

        <button
          onClick={() => onRemove()}
          className="p-0.5 ml-1 transform text-gray-400 hover:text-gray-600 bg-blue-500 hover:bg-blue-600 rounded-full flex flex-col items-center"
          title="Clear filter"
        >
          <svg
            className="h-3 w-3"
            fill="none"
            stroke="white"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={5}
              d="M6 18L18 6M6 6l12 12"
            />
          </svg>
        </button>
      </div>
    </>
  );
};
