import React, { useState } from 'react';

export function Tooltip({ children, text }) {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className="relative"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      {children}
      {isHovered && (
        <div className="absolute top-0 right-0 transform -translate-y-full -translate-x-2 mb-2 px-2 py-1 bg-gray-700 text-white text-xs rounded-md shadow-lg whitespace-nowrap z-50">
          {text}
        </div>
      )}
    </div>
  );
}
