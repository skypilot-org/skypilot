document.addEventListener('DOMContentLoaded', function () {
    var script = document.createElement('script');
    script.src = 'https://widget.runllm.com';
    script.setAttribute("crossorigin", "true");
    script.setAttribute('id', 'runllm-widget-script');
    script.setAttribute('type', 'module');
    script.setAttribute('runllm-assistant-id', '9');
    script.setAttribute('runllm-name', 'SkyPilot');
    script.setAttribute('runllm-theme-color', '#4C4C4D');
    script.setAttribute('runllm-brand-logo', 'https://avatars.githubusercontent.com/u/109387420?s=100&v=4');
    script.setAttribute('runllm-disclaimer', 'Results are automatically generated and may be inaccurate or contain inappropriate information. Do not include any sensitive information in your query.');
    script.setAttribute('runllm-keyboard-shortcut', 'Mod+k')
    script.setAttribute('runllm-community-type', 'slack')
    script.setAttribute('runllm-community-url', 'https://slack.skypilot.co/')
    script.setAttribute('runllm-position-y', '100px');
    script.async = true;
    document.head.appendChild(script);
});

(function (h, o, t, j, a, r) {
    h.hj = h.hj || function () { (h.hj.q = h.hj.q || []).push(arguments) };
    h._hjSettings = { hjid: 3768396, hjsv: 6 };
    a = o.getElementsByTagName('head')[0];
    r = o.createElement('script'); r.async = 1;
    r.src = t + h._hjSettings.hjid + j + h._hjSettings.hjsv;
    a.appendChild(r);
})(window, document, 'https://static.hotjar.com/c/hotjar-', '.js?sv=');

// New items: add 'new-item' class for for new items.
document.addEventListener('DOMContentLoaded', () => {
    // New items:
    const newItems = [
        { selector: '.toctree-l1 > a', text: 'Many Parallel Jobs' },
        { selector: '.toctree-l2 > a', text: 'Multiple Kubernetes Clusters' },
        { selector: '.toctree-l2 > a', text: 'HTTPS Encryption' },
        { selector: '.toctree-l1 > a', text: 'Asynchronous Execution' },
        { selector: '.toctree-l1 > a', text: 'Team Deployment' },
        { selector: '.toctree-l1 > a', text: 'Examples' },
        { selector: '.toctree-l3 > a', text: 'DeepSeek-R1 for RAG' },
    ];
    newItems.forEach(({ selector, text }) => {
        document.querySelectorAll(selector).forEach((el) => {
            if (el.textContent.includes(text)) {
                el.classList.add('new-item');
            }
        });
    });
});

// Remove Previous button from every first page of each tab.
document.addEventListener("DOMContentLoaded", function () {
    if (window.location.pathname.endsWith('/') || window.location.pathname.endsWith('index.html')) {
        var style = document.createElement('style');
        style.innerHTML = '.prev-next-area a.left-prev { display: none; }';
        document.head.appendChild(style);
    }
});
