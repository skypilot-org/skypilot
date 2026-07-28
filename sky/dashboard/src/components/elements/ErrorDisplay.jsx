import React, { useState, useEffect } from 'react';

// Helper function to clean error messages
const cleanErrorMessage = (error) => {
  if (!error?.message) return 'An unexpected error occurred.';

  let message = error.message;

  // Split on 'failed:' and take the part after it
  if (message.includes('failed:')) {
    message = message.split('failed:')[1].trim();
  }

  return message;
};

// Render a message string, turning fenced code blocks (```lang\n...```) into
// monospace <pre> blocks and leaving the surrounding text untouched. Server
// error messages occasionally embed a well-formatted config example (e.g. the
// expected YAML shape); a plain whitespace-pre-wrap render shows it in the
// proportional body font, so isolate the fence into a code block instead.
const renderMessageWithCodeBlocks = (text) => {
  if (typeof text !== 'string' || !text.includes('```')) return text;
  const fenceRegex = /```[^\n]*\n([\s\S]*?)```/g;
  const parts = [];
  let lastIndex = 0;
  let key = 0;
  let match;
  const pushText = (str) => {
    if (str) {
      parts.push(<React.Fragment key={`text-${key++}`}>{str}</React.Fragment>);
    }
  };
  while ((match = fenceRegex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      pushText(text.slice(lastIndex, match.index));
    }
    parts.push(
      <pre
        key={`code-${key++}`}
        className="mt-1 mb-0.5 p-2 bg-red-100 rounded font-mono text-xs overflow-x-auto whitespace-pre"
      >
        {match[1].replace(/\n$/, '')}
      </pre>
    );
    lastIndex = fenceRegex.lastIndex;
  }
  if (lastIndex < text.length) {
    pushText(text.slice(lastIndex));
  }
  return parts;
};

// Error display component
export const ErrorDisplay = ({ error, title = 'Error', onDismiss }) => {
  const [isDismissed, setIsDismissed] = useState(false);

  // Reset dismissed state when error changes
  useEffect(() => {
    if (error) {
      setIsDismissed(false);
    }
  }, [error]);

  if (!error || isDismissed) return null;

  // Clean the error message if it's an error object
  const displayError =
    typeof error === 'string' ? error : cleanErrorMessage(error);

  const handleDismiss = () => {
    setIsDismissed(true);
    if (onDismiss) {
      onDismiss();
    }
  };

  return (
    <div className="bg-red-50 border border-red-200 rounded-md p-3 mb-4">
      <div className="flex items-center justify-between">
        <div className="flex">
          <div className="flex-shrink-0">
            <svg
              className="h-5 w-5 text-red-400"
              viewBox="0 0 20 20"
              fill="currentColor"
            >
              <path
                fillRule="evenodd"
                d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
                clipRule="evenodd"
              />
            </svg>
          </div>
          <div className="ml-3">
            {/* whitespace-pre-wrap so multi-line server error messages
                (e.g. demotion failure with per-workspace bullets) keep
                their \n line breaks instead of collapsing to one line. */}
            <div className="text-sm text-red-800 whitespace-pre-wrap">
              <strong>{title}:</strong>{' '}
              {renderMessageWithCodeBlocks(displayError)}
            </div>
          </div>
        </div>
        <button
          onClick={handleDismiss}
          className="flex-shrink-0 ml-4 text-red-400 hover:text-red-600 focus:outline-none focus:ring-2 focus:ring-red-500 focus:ring-offset-2 focus:ring-offset-red-50 rounded"
          aria-label="Dismiss error"
        >
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path
              fillRule="evenodd"
              d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z"
              clipRule="evenodd"
            />
          </svg>
        </button>
      </div>
    </div>
  );
};

export default ErrorDisplay;
