import React, { useState } from 'react';
import { Button } from '@/components/ui/button';

function getPageNumbers(currentPage, totalPages) {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const pages = new Set([1, totalPages]);
  for (
    let i = Math.max(2, currentPage - 1);
    i <= Math.min(totalPages - 1, currentPage + 1);
    i++
  ) {
    pages.add(i);
  }
  const sorted = Array.from(pages).sort((a, b) => a - b);
  const result = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i - 1] > 1) {
      result.push('...');
    }
    result.push(sorted[i]);
  }
  return result;
}

export function PaginationControls({
  currentPage,
  totalPages,
  totalCount,
  startIndex,
  endIndex,
  onPageChange,
  onPreviousPage,
  onNextPage,
  isPrevDisabled,
  isNextDisabled,
  pageSize,
  onPageSizeChange,
  pageSizeOptions = [10, 30, 50, 100, 200],
  itemLabel = 'Rows',
}) {
  const [pageInputValue, setPageInputValue] = useState('');
  const [showPageInput, setShowPageInput] = useState(false);

  const handlePageInputSubmit = (e) => {
    e.preventDefault();
    const p = parseInt(pageInputValue, 10);
    if (p >= 1 && p <= totalPages) {
      onPageChange(p);
    }
    setShowPageInput(false);
    setPageInputValue('');
  };

  const pageNumbers = getPageNumbers(currentPage, totalPages);

  return (
    <div className="flex justify-end items-center py-2 px-4 text-sm text-gray-700">
      <div className="flex items-center space-x-4">
        <div className="flex items-center">
          <span className="mr-2">{itemLabel} per page:</span>
          <div className="relative inline-block">
            <select
              value={pageSize}
              onChange={onPageSizeChange}
              className="py-1 pl-2 pr-6 appearance-none outline-none cursor-pointer border-none bg-transparent"
              style={{ minWidth: '40px' }}
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              className="h-4 w-4 text-gray-500 absolute right-0 top-1/2 transform -translate-y-1/2 pointer-events-none"
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </div>
        </div>
        <div
          className="cursor-pointer select-none"
          onClick={() => {
            if (totalPages > 1) {
              setShowPageInput(true);
              setPageInputValue(String(currentPage));
            }
          }}
          title={totalPages > 1 ? 'Click to jump to a page' : undefined}
        >
          {totalCount > 0
            ? `${startIndex + 1} – ${Math.min(endIndex, totalCount)} of ${totalCount}`
            : '0 – 0 of 0'}
        </div>
        {showPageInput && (
          <form
            onSubmit={handlePageInputSubmit}
            className="flex items-center space-x-1"
          >
            <span className="text-gray-500">Go to</span>
            <input
              type="number"
              min={1}
              max={totalPages}
              value={pageInputValue}
              onChange={(e) => setPageInputValue(e.target.value)}
              onBlur={() => {
                setShowPageInput(false);
                setPageInputValue('');
              }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setShowPageInput(false);
                  setPageInputValue('');
                }
              }}
              autoFocus
              className="w-16 px-2 py-1 border border-gray-300 rounded text-sm text-center"
            />
          </form>
        )}
        <div className="flex items-center space-x-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={onPreviousPage}
            disabled={isPrevDisabled}
            className="text-gray-500 h-8 w-8 p-0"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M15 18l-6-6 6-6" />
            </svg>
          </Button>
          {totalPages > 1 &&
            pageNumbers.map((p, i) =>
              p === '...' ? (
                <span
                  key={`ellipsis-${i}`}
                  className="px-1 text-gray-400 select-none"
                >
                  ...
                </span>
              ) : (
                <Button
                  key={p}
                  variant={p === currentPage ? 'default' : 'ghost'}
                  size="icon"
                  onClick={() => onPageChange(p)}
                  className={`h-8 w-8 p-0 text-sm ${
                    p === currentPage
                      ? 'bg-blue-600 text-white hover:bg-blue-700'
                      : 'text-gray-600 hover:bg-gray-100'
                  }`}
                >
                  {p}
                </Button>
              )
            )}
          <Button
            variant="ghost"
            size="icon"
            onClick={onNextPage}
            disabled={isNextDisabled}
            className="text-gray-500 h-8 w-8 p-0"
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 18l6-6-6-6" />
            </svg>
          </Button>
        </div>
      </div>
    </div>
  );
}
