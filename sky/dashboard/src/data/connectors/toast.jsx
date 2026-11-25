// Create a simple DOM-based toast function
export function showToast(message, type = 'info', duration = 5000) {
  // Create toast container if it doesn't exist
  let toastContainer = document.getElementById('toast-container');
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    toastContainer.className =
      'fixed top-0 right-0 p-4 z-[9999] flex flex-col items-end space-y-2';
    document.body.appendChild(toastContainer);
  }

  // Create toast element
  const toast = document.createElement('div');
  toast.className = `rounded-md border-l-4 p-4 shadow-md flex items-center justify-between max-w-md w-full mb-2 pointer-events-auto`;

  // Set styles based on type
  switch (type) {
    case 'success':
      toast.className +=
        ' bg-green-tint-strong border-green-solid-soft text-green-fg-stronger';
      break;
    case 'error':
      toast.className +=
        ' bg-red-tint-strong border-red-solid-soft text-red-fg-stronger';
      break;
    case 'warning':
      toast.className +=
        ' bg-yellow-tint-strong border-yellow-solid-soft text-yellow-fg-stronger';
      break;
    default:
      toast.className +=
        ' bg-blue-tint-strong border-blue-solid-soft text-blue-fg-stronger';
  }

  // Create toast content
  toast.innerHTML = `
      <div class="flex-1 mr-2">
        <p class="text-sm font-medium">${message}</p>
      </div>
      <button class="text-content-tertiary hover:text-content focus:outline-none" aria-label="Close toast">
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    `;

  // Add to container
  toastContainer.appendChild(toast);

  // Add click handler to close button
  const closeButton = toast.querySelector('button');
  closeButton.addEventListener('click', () => {
    toastContainer.removeChild(toast);
  });

  // Auto-remove after duration
  setTimeout(() => {
    if (toastContainer.contains(toast)) {
      toastContainer.removeChild(toast);
    }
  }, duration);

  return toast;
}

// Simple toast API
const toast = {
  success: (message, duration) => showToast(message, 'success', duration),
  error: (message, duration) => showToast(message, 'error', duration),
  info: (message, duration) => showToast(message, 'info', duration),
  warning: (message, duration) => showToast(message, 'warning', duration),
};
