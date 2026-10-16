import React, { useState, useRef } from 'react';
import { Button } from '@/components/ui/button';

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
  const formRef = useRef(null);

  const handlePageInputSubmit = (e) => {
    e.preventDefault();
    const p = parseInt(pageInputValue, 10);
    if (p >= 1 && p <= totalPages) {
      onPageChange(p);
    }
    setShowPageInput(false);
    setPageInputValue('');
  };

  const rangeText =
    totalCount > 0
      ? `${startIndex + 1} – ${Math.min(endIndex, totalCount)} of ${totalCount}`
      : '0 – 0 of 0';

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
        {showPageInput ? (
          <form
            ref={formRef}
            onSubmit={handlePageInputSubmit}
            className="flex items-center space-x-1"
          >
            <input
              type="number"
              min={1}
              max={totalPages}
              value={pageInputValue}
              onChange={(e) => setPageInputValue(e.target.value)}
              onBlur={(e) => {
                if (!formRef.current?.contains(e.relatedTarget)) {
                  setShowPageInput(false);
                  setPageInputValue('');
                }
              }}
              onKeyDown={(e) => {
                if (e.key === 'Escape') {
                  setShowPageInput(false);
                  setPageInputValue('');
                }
              }}
              autoFocus
              className="w-16 px-2 py-1 border border-gray-300 rounded text-sm text-center"
              placeholder={`1-${totalPages}`}
            />
            <span className="text-gray-400">of {totalPages}</span>
          </form>
        ) : (
          <div
            className="cursor-pointer select-none hover:text-blue-600"
            onClick={() => {
              if (totalPages > 1) {
                setShowPageInput(true);
                setPageInputValue(String(currentPage));
              }
            }}
            title={totalPages > 1 ? 'Click to jump to a page' : undefined}
          >
            {rangeText}
          </div>
        )}
        <div className="flex items-center space-x-2">
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
