Managed Spot Jobs
==================

.. raw:: html

    <script type="text/javascript">
        // Function to perform the replacement and redirection
        function redirectToManagedJobs() {
            var currentUrl = window.location.href;
            
            // Check if the URL contains 'spot-jobs.html'
            if (currentUrl.includes("spot-jobs.html")) {
                // Replace 'spot-jobs.html' with 'managed-jobs.html'
                var newUrl = currentUrl.replace("spot-jobs.html", "managed-jobs.html");
                
                // Redirect to the new URL
                window.location.href = newUrl;
            }
        }

        // Call the redirection function on page load
        redirectToManagedJobs();
    </script>
