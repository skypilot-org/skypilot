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
      toast.className += ' bg-green-100 border-green-500 text-green-800';
      break;
    case 'error':
      toast.className += ' bg-red-100 border-red-500 text-red-800';
      break;
    case 'warning':
      toast.className += ' bg-yellow-100 border-yellow-500 text-yellow-800';
      break;
    default:
      toast.className += ' bg-blue-100 border-blue-500 text-blue-800';
  }

  // Create toast content
  toast.innerHTML = `
      <div class="flex-1 mr-2">
        <p class="text-sm font-medium">${message}</p>
      </div>
      <button class="text-gray-500 hover:text-gray-700 focus:outline-none" aria-label="Close toast">
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
