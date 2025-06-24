import React, { useState } from 'react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Button } from '@/components/ui/button';

const ALL_WORKSPACES_VALUE = '__ALL_WORKSPACES__';
const ALL_USERS_VALUE = '__ALL_USERS__';

export function FilterBar({
  // Search props
  searchPlaceholder = "Filter by name",
  searchValue = "",
  onSearchChange = (value) => {},
  
  // Workspace props
  workspaces = [],
  workspaceValue = ALL_WORKSPACES_VALUE,
  onWorkspaceChange = (value) => {},
  
  // User props
  users = [],
  userValue = ALL_USERS_VALUE,
  onUserChange = (value) => {},
  
  // Additional controls (like history toggle)
  children = null
}) {
  const [showFilters, setShowFilters] = useState(false);
  
  const hasActiveFilters = () => {
    return workspaceValue !== ALL_WORKSPACES_VALUE || 
           userValue !== ALL_USERS_VALUE || 
           (searchValue && searchValue.trim() !== '');
  };

  const clearAllFilters = () => {
    onSearchChange('');
    onWorkspaceChange(ALL_WORKSPACES_VALUE);
    onUserChange(ALL_USERS_VALUE);
  };

  const getSelectedUserDisplay = () => {
    if (userValue === ALL_USERS_VALUE) return 'All Users';
    const user = users.find(u => u.userId === userValue);
    return user?.display || userValue;
  };

  return (
    <div className="space-y-3">
      {/* Primary row with search and filter toggle */}
      <div className="flex items-center space-x-3">
        {/* Primary search bar */}
        <div className="relative">
          <input
            type="text"
            placeholder={searchPlaceholder}
            value={searchValue}
            onChange={(e) => onSearchChange(e.target.value)}
            className="h-8 w-48 sm:w-64 px-3 pr-8 text-sm border border-gray-300 rounded-md focus:ring-1 focus:ring-sky-500 focus:border-sky-500 outline-none"
          />
          {searchValue && (
            <button
              onClick={() => onSearchChange('')}
              className="absolute right-2 top-1/2 transform -translate-y-1/2 text-gray-400 hover:text-gray-600"
              title="Clear search"
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

        {/* Filter toggle button */}
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`h-8 px-3 text-sm border rounded-md inline-flex items-center ${
            hasActiveFilters() 
              ? 'bg-sky-50 border-sky-300 text-sky-700' 
              : 'border-gray-300 text-gray-700 hover:bg-gray-50'
          }`}
        >
          <svg
            className="h-4 w-4 mr-2"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M3 4a1 1 0 011-1h16a1 1 0 011 1v2.586a1 1 0 01-.293.707l-6.414 6.414a1 1 0 00-.293.707V17l-4 4v-6.586a1 1 0 00-.293-.707L3.293 7.293A1 1 0 013 6.586V4z"
            />
          </svg>
          Filters
          {hasActiveFilters() && (
            <span className="ml-1 bg-sky-100 text-sky-800 text-xs rounded-full px-1.5 py-0.5">
              {[
                searchValue && searchValue.trim() !== '' ? 1 : 0,
                workspaceValue !== ALL_WORKSPACES_VALUE ? 1 : 0,
                userValue !== ALL_USERS_VALUE ? 1 : 0
              ].reduce((a, b) => a + b, 0)}
            </span>
          )}
          <svg
            className={`h-4 w-4 ml-2 transition-transform ${showFilters ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M19 9l-7 7-7-7"
            />
          </svg>
        </button>

        {/* Additional controls (like history toggle) */}
        {children}

        {/* Clear filters button */}
        {hasActiveFilters() && (
          <button
            onClick={clearAllFilters}
            className="h-8 px-2 text-xs text-gray-500 hover:text-gray-700 border border-gray-300 rounded-md"
          >
            Clear all
          </button>
        )}
      </div>

      {/* Expandable filters row */}
      {showFilters && (
        <div className="flex items-center space-x-4 p-3 bg-gray-50 rounded-md border">
          {/* Workspace filter */}
          <div className="flex items-center space-x-2">
            <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
              Workspace:
            </label>
            <Select value={workspaceValue} onValueChange={onWorkspaceChange}>
              <SelectTrigger className="h-8 w-48 text-sm">
                <SelectValue>
                  {workspaceValue === ALL_WORKSPACES_VALUE ? 'All Workspaces' : workspaceValue}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_WORKSPACES_VALUE}>All Workspaces</SelectItem>
                {workspaces.map((ws) => (
                  <SelectItem key={ws} value={ws}>{ws}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {/* User filter */}
          <div className="flex items-center space-x-2">
            <label className="text-sm font-medium text-gray-700 whitespace-nowrap">
              User:
            </label>
            <Select value={userValue} onValueChange={onUserChange}>
              <SelectTrigger className="h-8 w-48 text-sm">
                <SelectValue>
                  {getSelectedUserDisplay()}
                </SelectValue>
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL_USERS_VALUE}>All Users</SelectItem>
                {users.map((user) => (
                  <SelectItem key={user.userId} value={user.userId}>
                    {user.display}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {/* Active filters summary - compact version when filters are collapsed */}
      {hasActiveFilters() && !showFilters && (
        <div className="flex items-center text-xs text-gray-600">
          <span className="mr-2">Active filters:</span>
          <div className="flex items-center space-x-1">
            {searchValue && searchValue.trim() !== '' && (
              <span className="bg-gray-100 px-2 py-1 rounded text-xs">
                "{searchValue.length > 20 ? searchValue.substring(0, 20) + '...' : searchValue}"
              </span>
            )}
            {workspaceValue !== ALL_WORKSPACES_VALUE && (
              <span className="bg-gray-100 px-2 py-1 rounded text-xs">
                workspace: {workspaceValue}
              </span>
            )}
            {userValue !== ALL_USERS_VALUE && (
              <span className="bg-gray-100 px-2 py-1 rounded text-xs">
                user: {getSelectedUserDisplay()}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export { ALL_WORKSPACES_VALUE, ALL_USERS_VALUE };